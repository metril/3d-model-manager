"""`GET /storage/tree` (R13b): a real drillable file browser over the
canonical storage layout, derived from `File.storage_path` prefixes (no
filesystem walk) -- so the tree's depth is exactly what the layout is:

- at the root, immediate children are model slug directories;
- inside a model dir, immediate children are revision directories;
- inside a revision dir (or deeper), immediate children are `rel_path`
  subdirectories (e.g. `images/`).

For a given `path`, `dirs` lists those immediate child directories (each
with `file_count`/`model_count` aggregated over everything nested beneath
it), `files` lists (paginated by `limit`/`offset`, `total_files` is the full count)
the files whose parent directory is `path` exactly, and
`model` is the `ModelSummary` for the model when `path`'s first segment is
a model slug (so the UI can offer an "Open model" action), else `None`.

Internal snapshot files (`_snapshots/...`) are excluded everywhere, same as
every other user-facing file listing (`app.services.layout.is_snapshot_path`).
A path is normalized by stripping leading/trailing slashes; a path-traversal
or unknown path simply matches nothing and comes back empty -- no special
casing needed, since no real `storage_path` ever contains a `..` segment.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models.library import Blob, File, Model
from app.schemas.storage_tree import StorageTreeDirOut, StorageTreeFileOut, StorageTreeOut
from app.services.layout import SNAPSHOT_PREFIX
from app.services.library import _escape_like, build_model_summaries

DEFAULT_FILE_LIMIT = 500


async def get_storage_tree(
    db: AsyncSession,
    settings: Settings,
    path: str,
    *,
    limit: int = DEFAULT_FILE_LIMIT,
    offset: int = 0,
) -> StorageTreeOut:
    path = path.strip("/")
    prefix = "" if not path else path + "/"

    conditions = [
        ~File.rel_path.like(f"{_escape_like(SNAPSHOT_PREFIX)}%", escape="\\"),
        func.length(File.storage_path) > len(prefix),
    ]
    if prefix:
        conditions.append(File.storage_path.like(f"{_escape_like(prefix)}%", escape="\\"))
    rest = func.substr(File.storage_path, len(prefix) + 1)
    has_child = func.strpos(rest, "/") > 0

    # Child directories: aggregate in SQL by the first segment of the rest.
    child = func.split_part(rest, "/", 1)
    dir_rows = (
        await db.execute(
            select(
                child.label("name"),
                func.count().label("file_count"),
                func.count(func.distinct(func.split_part(File.storage_path, "/", 1))).label(
                    "model_count"
                ),
            )
            .where(*conditions, has_child)
            .group_by(child)
            .order_by(child)
        )
    ).all()
    dirs = [
        StorageTreeDirOut(
            name=r.name,
            path=f"{prefix}{r.name}",
            file_count=r.file_count,
            model_count=r.model_count,
        )
        for r in dir_rows
    ]

    # Files directly under `path`: paginated, ordered by name.
    direct = (*conditions, ~has_child)
    total_files = (await db.scalar(select(func.count()).select_from(File).where(*direct))) or 0
    file_rows = (
        await db.execute(
            select(
                File.id,
                File.storage_path,
                File.rel_path,
                File.blob_hash,
                File.revision_id,
                Blob.size,
                Blob.kind,
                Blob.format,
                rest.label("name"),
            )
            .join(Blob, Blob.hash == File.blob_hash)
            .where(*direct)
            .order_by(rest, File.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    files = [
        StorageTreeFileOut(
            id=row.id,
            name=row.name,
            rel_path=row.rel_path,
            size=row.size,
            kind=row.kind,
            format=row.format,
            model_slug=row.storage_path.split("/", 1)[0],
            blob_hash=row.blob_hash,
            revision_id=row.revision_id,
        )
        for row in file_rows
    ]

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

    return StorageTreeOut(path=path, dirs=dirs, files=files, total_files=total_files, model=model)
