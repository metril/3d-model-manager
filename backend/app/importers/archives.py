"""Zip/3MF intelligence at import time (feat/import-fidelity T1 -- "Live
facts" in the task brief): MakerWorld's per-print-profile downloads arrive as
``<profile name>-<id>.zip`` but are actually mislabeled 3MF containers --
``layout.infer_blob_kind_format`` types purely by extension, so a bare
``.zip`` lands on ``BlobFormat.OTHER`` (``PIPELINE_STEPS[OTHER] = ()``,
nothing viewable) even though the bytes are a perfectly normal 3MF the
existing pipeline already knows how to mine whole. Thingiverse, by contrast,
ships a genuine ``ZipFile.zip`` of loose, arbitrary files -- that one needs
real extraction, member by member, so each ends up individually pipeline-
eligible instead of trapped inside an opaque OTHER-format archive.

``process_staged_zips`` is a PURE function over a staged-file list plus the
``Settings`` handle needed to spool new members -- no import-row coupling --
so a later re-download task (T3) can call the exact same zip intelligence
over its own staged list unchanged. Called from ``app.tasks.importing``
after the download loop and before ``create_imported_model_sync``.

Safety is the load-bearing property throughout: a weird/hostile/corrupt
archive must never fail an otherwise-successful import (Global Constraints
"IMPORTS ATOMIC" spirit) -- it just stays a single opaque zip file, exactly
as if this module didn't exist.
"""

from __future__ import annotations

import dataclasses
import logging
import zipfile
from pathlib import PurePosixPath

from app.config import Settings
from app.importers import download
from app.importers.download import StagedFile
from app.services import layout

logger = logging.getLogger(__name__)

# Bambu's per-print-profile 3MF payload always carries the root model part at
# this fixed path, whether it's a project 3mf or a sliced gcode.3mf -- the
# one marker the "Live facts" verification checked for across every real
# example (`[Content_Types].xml`, `3D/Objects/object_*.model`, `_rels/.rels`,
# `Auxiliaries/Model Pictures/*.webp` all vary; this doesn't).
_THREEMF_MARKER = "3D/3dmodel.model"

_ZIP_SUFFIX_LEN = len(".zip")
_MACOSX_PREFIX = "__MACOSX/"

# A staged zip must clear both caps BEFORE any member is streamed to spool --
# breaching either means the archive is left untouched as a single opaque
# zip file rather than attempted (a hostile/weird archive must never be able
# to fail or stall an otherwise-successful import).
MAX_ZIP_MEMBERS = 500
MAX_ZIP_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


def process_staged_zips(settings: Settings, staged: list[StagedFile]) -> list[StagedFile]:
    """Sniff/extract every ``.zip``-suffixed entry in ``staged``, in order;
    non-zip entries pass through untouched. Pure function over the staged-
    file list (plus the settings handle new spooled members need) -- no
    import-row coupling.
    """
    result: list[StagedFile] = []
    for sf in staged:
        result.extend(_process_entry(settings, sf, allow_extract=True))
    return result


def _process_entry(settings: Settings, sf: StagedFile, *, allow_extract: bool) -> list[StagedFile]:
    """One staged file's worth of zip intelligence.

    ``allow_extract=False`` is the "one level only" guard: an extracted
    member that is itself a zip is still sniffed for a hidden 3MF (Bambu
    3mfs can hide anywhere), but never extracted a second time -- it just
    stays a file.
    """
    if not sf.rel_path.lower().endswith(".zip"):
        return [sf]

    try:
        with zipfile.ZipFile(sf.spool_path) as zf:
            if _THREEMF_MARKER in zf.namelist():
                return [_rename_as_3mf(sf)]
            if not allow_extract:
                return [sf]
            extracted = _extract(settings, zf, sf)
    except zipfile.BadZipFile:
        logger.warning("staged zip %r is not a valid archive; keeping as-is", sf.rel_path)
        return [sf]

    if extracted is None:
        return [sf]
    sf.spool_path.unlink(missing_ok=True)  # original archive discarded -- extraction replaced it
    return extracted


def _rename_as_3mf(sf: StagedFile) -> StagedFile:
    """Same spool bytes, same hash -- only ``rel_path`` (and therefore the
    re-inferred ``kind``/``format_``) changes, so the existing ``.3mf``
    pipeline (Global Constraints "Pipeline shape") picks it up whole; no
    extraction. Strips exactly the trailing ``.zip`` (case-insensitive,
    already confirmed by the caller) so any directory prefix -- e.g. a
    nested member's own ``<zip-stem>/...`` path -- survives untouched.
    """
    new_rel_path = sf.rel_path[:-_ZIP_SUFFIX_LEN] + ".3mf"
    kind, format_ = layout.infer_blob_kind_format(new_rel_path)
    return dataclasses.replace(sf, rel_path=new_rel_path, kind=kind, format_=format_)


def _sanitize_member_name(name: str) -> str | None:
    """Mirror ``app.services.library._validate_rel_path``'s rules -- an
    archive member's own path is untrusted input that ends up embedded
    verbatim into a storage key (``<slug>/<dir>/<rel_path>``). Returns
    ``None`` (caller quiet-skips, no exception) for anything unsafe: empty,
    a backslash, absolute, or containing ``..``.
    """
    if not name or "\\" in name:
        return None
    pure = PurePosixPath(name)
    if pure.is_absolute() or pure.parts == () or ".." in pure.parts:
        return None
    return name


def _dedupe_member_name(name: str, seen: set[str]) -> str:
    """Two zip members can sanitize to the identical name (a hostile or
    merely careless archive can contain outright duplicate entries) -- the
    eventual ``rel_path`` is unique per ``(revision_id, rel_path)`` at the DB
    layer, so an unresolved collision would ``IntegrityError`` the whole
    import (the very thing this module exists to prevent -- module docstring
    "a weird/hostile/corrupt archive must never fail an otherwise-successful
    import"). Content may differ between the duplicates, so neither is
    dropped: a repeat gets a deterministic ``name (2).ext``, ``name (3).ext``,
    ... suffix -- same convention a desktop file manager uses to resolve a
    copy conflict -- checked against ``seen`` so a generated name can never
    itself collide with either an original member name or an earlier
    rename.
    """
    if name not in seen:
        seen.add(name)
        return name
    pure = PurePosixPath(name)
    parent, stem, suffix = pure.parent, pure.stem, pure.suffix
    n = 2
    while True:
        candidate = f"{stem} ({n}){suffix}"
        deduped = candidate if str(parent) == "." else str(parent / candidate)
        if deduped not in seen:
            seen.add(deduped)
            return deduped
        n += 1


def _extract(settings: Settings, zf: zipfile.ZipFile, sf: StagedFile) -> list[StagedFile] | None:
    """Stream every safe member of ``zf`` to its own staged file, or return
    ``None`` (caller falls back to keeping the original zip untouched) when
    the caps are breached, nothing survives the safety filter, or the
    archive turns out corrupt partway through.
    """
    candidates: list[tuple[zipfile.ZipInfo, str]] = []
    seen_names: set[str] = set()
    total_size = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if name == "__MACOSX" or name.startswith(_MACOSX_PREFIX):
            continue
        sanitized = _sanitize_member_name(name)
        if sanitized is None:
            logger.warning("skipping unsafe zip member %r in %r", name, sf.rel_path)
            continue
        deduped = _dedupe_member_name(sanitized, seen_names)
        if deduped != sanitized:
            logger.warning(
                "staged zip %r: renaming duplicate member %r to %r",
                sf.rel_path,
                sanitized,
                deduped,
            )
        candidates.append((info, deduped))
        total_size += info.file_size

    if not candidates:
        return None
    if len(candidates) > MAX_ZIP_MEMBERS or total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
        logger.warning(
            "staged zip %r exceeds extraction caps (%d members, %d bytes); keeping as-is",
            sf.rel_path,
            len(candidates),
            total_size,
        )
        return None

    zip_stem = sf.rel_path[:-_ZIP_SUFFIX_LEN]
    staged_members: list[StagedFile] = []
    try:
        for info, member_name in candidates:
            staged_members.append(
                download.stage_zip_member(settings, zf, info, f"{zip_stem}/{member_name}")
            )
    except Exception as exc:  # noqa: BLE001 -- encrypted/unsupported-compression/zlib errors too
        logger.warning(
            "staged zip %r failed mid-extraction (%s); keeping as-is",
            sf.rel_path,
            type(exc).__name__,
        )
        for member in staged_members:
            member.spool_path.unlink(missing_ok=True)
        return None

    # One level only: sniff (but never re-extract) any extracted member that
    # is itself a zip.
    result: list[StagedFile] = []
    for member in staged_members:
        result.extend(_process_entry(settings, member, allow_extract=False))
    return result
