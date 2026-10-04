"""Streaming ZIP export for a model or a followed collection (R11-A plan
item 12).

Files are read through the storage backend abstraction
(``app.storage.base.StorageBackend.read``), never loaded whole into memory.
``zipstream-ng`` builds the archive incrementally: each file is queued and
immediately drained (via ``ZipStream.file()``) before the next one is even
resolved, so a backend read for file N+1 is never issued until file N's
bytes have been fully yielded -- proving the stream is genuinely
incremental, not "buffer it all, then zip it".
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from pathlib import PurePosixPath
from zipfile import ZIP_DEFLATED, ZIP_STORED

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from zipstream import ZipStream

from app.config import Settings
from app.models import Blob, File, FollowedCollection, Model, Project
from app.models.enums import BlobFormat
from app.services import layout
from app.services.storage_backends import resolve_backend_for_file

# Already-compressed archive formats -- re-deflating them wastes CPU for
# ~0 size benefit, so they're stored raw in the export zip.
_STORED_FORMATS = {BlobFormat.THREEMF, BlobFormat.GCODE_3MF}


class EmptyModelError(Exception):
    """Raised when a model has no current-revision files to export."""

    def __init__(self, model: Model) -> None:
        self.model = model
        super().__init__(f"model {model.slug!r} has no files")


def _compress_type_for(blob_format: BlobFormat) -> int:
    return ZIP_STORED if blob_format in _STORED_FORMATS else ZIP_DEFLATED


def _dedupe_name(used: set[str], name: str) -> str:
    """Return ``name``, or ``"<stem> (2).<ext>"``/``"<stem> (3).<ext>"``/...
    the first time it collides with a name already ``used`` (mutated in
    place to record whichever name is returned)."""
    if name not in used:
        used.add(name)
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    n = 2
    while True:
        candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


def _readme_bytes(model: Model) -> bytes:
    lines = [f"Name: {model.name}"]
    if model.source_url:
        lines.append(f"Source: {model.source_url}")
    if model.source_author:
        lines.append(f"Author: {model.source_author}")
    if model.source_license:
        lines.append(f"License: {model.source_license}")
    return ("\n".join(lines) + "\n").encode()


async def _current_revision_files(db: AsyncSession, model: Model) -> list[tuple[File, Blob]]:
    if model.current_revision_id is None:
        return []
    stmt = (
        select(File, Blob)
        .join(Blob, Blob.hash == File.blob_hash)
        .where(File.revision_id == model.current_revision_id)
        .order_by(File.rel_path)
    )
    return [
        (f, b)
        for f, b in (await db.execute(stmt)).all()
        # Internal snapshot files (the cover-image snapshot -- R13a review
        # fix) are never user content; exclude them from the export.
        if not layout.is_snapshot_path(f.rel_path)
    ]


async def _add_model_entries(
    zs: ZipStream,
    db: AsyncSession,
    settings: Settings,
    model: Model,
    files: list[tuple[File, Blob]],
    *,
    prefix: str,
    used_names: set[str],
) -> AsyncIterator[bytes]:
    """Queue README + every current-revision file of ``model`` (already
    fetched by the caller as ``files``, so it's read once per model rather
    than once per caller *and* once here) under ``prefix`` (e.g.
    ``"<model-slug>"`` or ``"<collection-name>/<model-slug>"``), draining
    each entry as soon as it's queued so files stay in strict one-at-a-time
    streaming order."""
    readme_name = _dedupe_name(used_names, "README.txt")
    zs.add(_readme_bytes(model), f"{prefix}/{readme_name}")
    for chunk in zs.file():
        yield chunk

    for file, blob in files:
        backend = await resolve_backend_for_file(db, settings, file)
        arcname = _dedupe_name(used_names, PurePosixPath(file.rel_path).name)
        zs.add(
            backend.read(file.storage_path),
            f"{prefix}/{arcname}",
            size=blob.size,
            compress_type=_compress_type_for(blob.format),
        )
        for chunk in zs.file():
            yield chunk


async def iter_model_zip(
    db: AsyncSession, settings: Settings, model: Model
) -> AsyncIterator[bytes]:
    """Stream a zip of ``model``'s current revision as
    ``<model-slug>/<original filename>`` entries plus a ``README.txt``.

    Raises :class:`EmptyModelError` if the model has zero current-revision
    files -- callers turn that into a 409.
    """
    files = await _current_revision_files(db, model)
    if not files:
        raise EmptyModelError(model)

    zs = ZipStream()
    used_names: set[str] = set()
    async for chunk in _add_model_entries(
        zs, db, settings, model, files, prefix=model.slug, used_names=used_names
    ):
        yield chunk
    for chunk in zs.footer():
        yield chunk


async def first_chunk(gen: AsyncIterator[bytes]) -> tuple[bytes, AsyncIterator[bytes]]:
    """Force ``gen`` to run up to its first ``yield`` (or raise, e.g.
    :class:`EmptyModelError`) and return that chunk plus an async iterator
    that replays it followed by the rest of ``gen`` -- lets a route decide
    the response status (200 vs 409) before any bytes are actually streamed
    to the client, without buffering the whole export."""
    chunk = await gen.__anext__()

    async def _rest() -> AsyncIterator[bytes]:
        try:
            yield chunk
            async for c in gen:
                yield c
        finally:
            # A client that disconnects mid-stream (or any other early
            # exit) throws `GeneratorExit` into this generator -- without
            # explicitly closing `gen` too, its open backend read handles
            # (file descriptors / S3 response bodies) would only get
            # released whenever the GC eventually collects it.
            await gen.aclose()

    return chunk, _rest()


_UNSAFE_PATH_CHARS = re.compile(r"[\x00-\x1f\x7f/\\]")
_WHITESPACE_RUN = re.compile(r"\s+")


def _safe_path_segment(name: str, *, fallback: str) -> str:
    """Sanitize ``name`` into a single safe zip path segment: strip path
    separators (``/``, ``\\``), control characters, and ``..`` (so it can
    never zip-slip out of the archive root or the collection's own
    subtree), and collapse whitespace. Falls back to ``fallback`` if
    nothing usable remains."""
    cleaned = _UNSAFE_PATH_CHARS.sub("", name)
    cleaned = cleaned.replace("..", "")
    cleaned = _WHITESPACE_RUN.sub(" ", cleaned).strip()
    if cleaned in ("", ".", ".."):
        return fallback
    return cleaned


async def _collection_models(db: AsyncSession, collection: FollowedCollection) -> Sequence[Model]:
    stmt = (
        select(Model)
        .where(Model.source_collection_id == collection.id)
        .order_by(Model.name, Model.id)
    )
    return (await db.execute(stmt)).scalars().all()


async def iter_collection_zip(
    db: AsyncSession, settings: Settings, collection: FollowedCollection
) -> AsyncIterator[bytes]:
    """Stream a zip nesting every model in ``collection`` as
    ``<collection-name>/<model-slug>/...`` (same per-model layout as
    :func:`iter_model_zip`). Models with zero current-revision files are
    silently skipped rather than failing the whole export.
    """
    models = await _collection_models(db, collection)
    collection_prefix = _safe_path_segment(collection.title, fallback=f"collection-{collection.id}")

    zs = ZipStream()
    for model in models:
        files = await _current_revision_files(db, model)
        if not files:
            continue
        used_names: set[str] = set()
        async for chunk in _add_model_entries(
            zs,
            db,
            settings,
            model,
            files,
            prefix=f"{collection_prefix}/{model.slug}",
            used_names=used_names,
        ):
            yield chunk
    for chunk in zs.footer():
        yield chunk


async def _get_project_tree(db: AsyncSession, root_project: Project) -> list[tuple[Project, str]]:
    all_projects = (await db.execute(select(Project))).scalars().all()
    by_parent: dict[int | None, list[Project]] = {}
    for p in all_projects:
        by_parent.setdefault(p.parent_id, []).append(p)

    results: list[tuple[Project, str]] = []

    seen: set[int] = set()

    def _traverse(proj: Project, current_prefix: str) -> None:
        if proj.id in seen:
            return
        seen.add(proj.id)
        results.append((proj, current_prefix))
        used_segments: set[str] = set()
        for child in by_parent.get(proj.id, []):
            child_seg = _safe_path_segment(child.name, fallback=f"project-{child.id}")
            if child_seg in used_segments:
                n = 2
                while f"{child_seg} ({n})" in used_segments:
                    n += 1
                child_seg = f"{child_seg} ({n})"
            used_segments.add(child_seg)
            _traverse(child, f"{current_prefix}/{child_seg}")

    root_seg = _safe_path_segment(root_project.name, fallback=f"project-{root_project.id}")
    _traverse(root_project, root_seg)
    return results


async def iter_project_zip(
    db: AsyncSession, settings: Settings, project: Project
) -> AsyncIterator[bytes]:
    """Stream a zip nesting every model in this project and its subprojects
    as `<project>/[<subproject>/.../]<model-slug>/...`. Models with zero
    current-revision files are silently skipped; an empty project still
    streams a valid zip.
    """
    tree = await _get_project_tree(db, project)
    zs = ZipStream()

    for proj, prefix in tree:
        stmt = select(Model).where(Model.project_id == proj.id).order_by(Model.name, Model.id)
        models = (await db.execute(stmt)).scalars().all()
        for model in models:
            files = await _current_revision_files(db, model)
            if not files:
                continue
            used_names: set[str] = set()
            async for chunk in _add_model_entries(
                zs,
                db,
                settings,
                model,
                files,
                prefix=f"{prefix}/{model.slug}",
                used_names=used_names,
            ):
                yield chunk

    for chunk in zs.footer():
        yield chunk
