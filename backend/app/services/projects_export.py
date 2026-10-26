"""Streaming zip export of a project: BOM csv + project json + the library
files its parts reference (whole-model lines contribute the model's current
revision, file-specific lines just that file)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from zipstream import ZipStream

from app.api.deps import AuthContext
from app.config import Settings
from app.models import Blob, File, Model
from app.models.projects import Project, ProjectItem
from app.services import projects as svc
from app.services import zip_export


async def iter_project_zip(
    db: AsyncSession, settings: Settings, project: Project, ctx: AuthContext | None = None
) -> AsyncIterator[bytes]:
    out = await svc.to_out(db, project, ctx)
    zs = ZipStream()
    root = zip_export._safe_path_segment(project.name, fallback=f"project-{project.id}")
    zs.add(svc.bom_csv(out).encode(), f"{root}/bom.csv")
    for c in zs.file():
        yield c
    zs.add(svc.export_json(out).encode(), f"{root}/project.json")
    for c in zs.file():
        yield c

    items = (
        (
            await db.execute(
                select(ProjectItem)
                .where(ProjectItem.project_id == project.id, ProjectItem.kind == "part")
                .order_by(ProjectItem.position, ProjectItem.id)
            )
        )
        .scalars()
        .all()
    )
    whole: dict[int, bool] = {}
    picked: dict[int, set[int]] = {}
    for i in items:
        if i.model_id is None:
            continue
        if i.file_id is None:
            whole[i.model_id] = True
        else:
            picked.setdefault(i.model_id, set()).add(i.file_id)
    used: set[str] = set()
    for mid in dict.fromkeys(i.model_id for i in items if i.model_id is not None):
        model = await db.get(Model, mid)
        if model is None or model.deleted_at is not None:
            continue
        files: list[tuple[File, Blob]]
        if whole.get(mid):
            files = await zip_export._current_revision_files(db, model)
        else:
            rows = await db.execute(
                select(File, Blob)
                .join(Blob, Blob.hash == File.blob_hash)
                .where(File.id.in_(picked[mid]))
                .order_by(File.rel_path)
            )
            files = [(f, b) for f, b in rows.all()]
        if not files:
            continue
        async for chunk in zip_export._add_model_entries(
            zs, db, settings, model, files, prefix=f"{root}/{model.slug}", used_names=used
        ):
            yield chunk
    for c in zs.footer():
        yield c
