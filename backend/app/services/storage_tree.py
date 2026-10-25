"""`GET /storage/tree` (R13b): a real drillable file browser over the
canonical storage layout, derived from `File.storage_path` prefixes (no
filesystem walk) -- so the tree's depth is exactly what the layout is:

- at the root, immediate children are model slug directories;
- inside a model dir, immediate children are revision directories;
- inside a revision dir (or deeper), immediate children are `rel_path`
  subdirectories (e.g. `images/`).

For a given `path`, `dirs` lists those immediate child directories (each
with `file_count`/`model_count` aggregated over everything nested beneath
it), `files` lists the files whose parent directory is `path` exactly, and
`model` is the `ModelSummary` for the model when `path`'s first segment is
a model slug (so the UI can offer an "Open model" action), else `None`.

Internal snapshot files (`_snapshots/...`) are excluded everywhere, same as
every other user-facing file listing (`app.services.layout.is_snapshot_path`).
A path is normalized by stripping leading/trailing slashes; a path-traversal
or unknown path simply matches nothing and comes back empty -- no special
casing needed, since no real `storage_path` ever contains a `..` segment.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models.library import Blob, File, Model
from app.schemas.storage_tree import StorageTreeDirOut, StorageTreeFileOut, StorageTreeOut
from app.services.layout import is_snapshot_path
from app.services.library import _escape_like, build_model_summaries


async def get_storage_tree(db: AsyncSession, settings: Settings, path: str) -> StorageTreeOut:
    path = path.strip("/")
    prefix = "" if not path else path + "/"

    stmt = select(
        File.id,
        File.storage_path,
        File.rel_path,
        File.blob_hash,
        File.revision_id,
        Blob.size,
        Blob.kind,
        Blob.format,
    ).join(Blob, Blob.hash == File.blob_hash)
    if prefix:
        stmt = stmt.where(File.storage_path.like(f"{_escape_like(prefix)}%", escape="\\"))
    rows = (await db.execute(stmt)).all()

    files: list[StorageTreeFileOut] = []
    dir_file_counts: dict[str, int] = {}
    dir_model_slugs: dict[str, set[str]] = {}

    for row in rows:
        if is_snapshot_path(row.rel_path):
            continue
        rest = row.storage_path[len(prefix) :]
        if not rest:
            continue
        segments = rest.split("/")
        model_slug = row.storage_path.split("/", 1)[0]

        if len(segments) == 1:
            files.append(
                StorageTreeFileOut(
                    id=row.id,
                    name=segments[0],
                    rel_path=row.rel_path,
                    size=row.size,
                    kind=row.kind,
                    format=row.format,
                    model_slug=model_slug,
                    blob_hash=row.blob_hash,
                    revision_id=row.revision_id,
                )
            )
        else:
            child = segments[0]
            dir_file_counts[child] = dir_file_counts.get(child, 0) + 1
            dir_model_slugs.setdefault(child, set()).add(model_slug)

    dirs = [
        StorageTreeDirOut(
            name=name,
            path=f"{prefix}{name}",
            file_count=dir_file_counts[name],
            model_count=len(dir_model_slugs[name]),
        )
        for name in sorted(dir_file_counts)
    ]
    files.sort(key=lambda f: f.name)

    model = None
    first_segment = path.split("/", 1)[0] if path else ""
    if first_segment:
        model_row = (
            await db.execute(
                select(Model)
                .where(Model.slug == first_segment, Model.deleted_at.is_(None))
                .options(selectinload(Model.tags), selectinload(Model.category))
            )
        ).scalar_one_or_none()
        if model_row is not None:
            summaries = await build_model_summaries(db, settings, [model_row])
            model = summaries[0] if summaries else None

    return StorageTreeOut(path=path, dirs=dirs, files=files, model=model)
