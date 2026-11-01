"""Tags: shared vocabulary CRUD/merge + per-entity tag sets (SPEC "API
surface"). Per-entity ``PUT .../tags`` routes for models, model files,
projects, attachments, archives and followed collections.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    require_owner_or_admin,
    require_perm,
    require_session,
    visible_printer_ids,
)
from app.db import get_db
from app.models import PrintJob
from app.models.collections import FollowedCollection
from app.models.library import Model, model_tags
from app.models.printing import PrintArchive
from app.models.projects import Project, ProjectAttachment
from app.models.tag_links import followed_collection_tags, print_archive_tags, project_tags
from app.schemas.housekeeping import BulkIdsIn, BulkItem, BulkOut
from app.schemas.library import (
    FileTagIdsIn,
    TagBulkMergeIn,
    TagBulkUpdateIn,
    TagCounts,
    TagCreate,
    TagIdsIn,
    TagMergeIn,
    TagOut,
    TagRef,
    TagUpdate,
    TagUsageOut,
    UsageArchive,
    UsageCollection,
    UsageModel,
    UsageProject,
)
from app.services import library
from app.services import projects as projects_svc
from app.services import tags as tags_svc

router = APIRouter(tags=["tags"])
_W = [Depends(require_perm("library:write"))]


@router.get("/tags", response_model=list[TagOut])
async def list_tags(with_counts: bool = False, db: AsyncSession = Depends(get_db)) -> list[TagOut]:
    return await tags_svc.list_tags(db, with_counts=with_counts)


@router.post("/tags", status_code=status.HTTP_201_CREATED, response_model=TagOut, dependencies=_W)
async def create_tag(payload: TagCreate, db: AsyncSession = Depends(get_db)) -> TagOut:
    return await tags_svc.create_tag(db, payload)


@router.post(
    "/models/{model_id}/tags",
    status_code=status.HTTP_201_CREATED,
    response_model=TagOut,
    dependencies=_W,
)
async def add_tag(model_id: int, payload: TagCreate, db: AsyncSession = Depends(get_db)) -> TagOut:
    return await library.add_tag_to_model(db, model_id, payload.name, payload.color)


def _bulk_out(ok: list[int], missing: list[int]) -> BulkOut:
    return BulkOut(results=[BulkItem(id=i, ok=True) for i in ok], not_found=missing)


# Literal bulk paths are declared BEFORE ``/tags/{tag_id}...``.
@router.post("/tags/bulk-delete", response_model=BulkOut, dependencies=_W)
async def bulk_delete_tags(payload: BulkIdsIn, db: AsyncSession = Depends(get_db)) -> BulkOut:
    return _bulk_out(*await tags_svc.bulk_delete(db, payload.ids))


@router.post("/tags/bulk-merge", response_model=BulkOut, dependencies=_W)
async def bulk_merge_tags(payload: TagBulkMergeIn, db: AsyncSession = Depends(get_db)) -> BulkOut:
    """Merge every tag in ``ids`` into ``into_id`` in one transaction."""
    return _bulk_out(*await tags_svc.bulk_merge(db, payload.ids, payload.into_id))


@router.post("/tags/bulk-update", response_model=BulkOut, dependencies=_W)
async def bulk_update_tags(payload: TagBulkUpdateIn, db: AsyncSession = Depends(get_db)) -> BulkOut:
    if "color" not in payload.model_fields_set:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "nothing to update")
    return _bulk_out(*await tags_svc.bulk_recolor(db, payload.ids, payload.color))


@router.get("/tags/{tag_id}/usage", response_model=TagUsageOut)
async def tag_usage(
    tag_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> TagUsageOut:
    """Per-entity-type samples (newest first, ``limit`` each) of what carries this tag."""
    await tags_svc.get_tag(db, tag_id)
    counts = (await tags_svc.tag_counts(db)).get(tag_id) or TagCounts()

    mrows = (
        await db.execute(
            select(Model)
            .join(model_tags, model_tags.c.model_id == Model.id)
            .where(model_tags.c.tag_id == tag_id, Model.deleted_at.is_(None))
            .order_by(Model.updated_at.desc(), Model.id.desc())
            .limit(limit)
        )
    ).scalars()
    models = []
    for m in mrows:
        if m.cover_blob_hash:
            thumb = f"/api/blobs/{m.cover_blob_hash}/thumb?size=256"
        elif m.current_revision_id:
            thumb = f"/api/revisions/{m.current_revision_id}/assembly-thumb"
        else:
            thumb = None
        models.append(UsageModel(id=m.id, slug=m.slug, name=m.name, thumbnail_url=thumb))

    projects = [
        UsageProject(id=p.id, name=p.name, status=p.status)
        for p in (
            await db.execute(
                select(Project)
                .join(project_tags, project_tags.c.project_id == Project.id)
                .where(project_tags.c.tag_id == tag_id)
                .order_by(Project.id.desc())
                .limit(limit)
            )
        ).scalars()
    ]
    collections = [
        UsageCollection(
            id=c.id, title=c.title, site=str(c.site.value if hasattr(c.site, "value") else c.site)
        )
        for c in (
            await db.execute(
                select(FollowedCollection)
                .join(
                    followed_collection_tags,
                    followed_collection_tags.c.collection_id == FollowedCollection.id,
                )
                .where(followed_collection_tags.c.tag_id == tag_id)
                .order_by(FollowedCollection.id.desc())
                .limit(limit)
            )
        ).scalars()
    ]
    astmt = (
        select(PrintArchive)
        .join(print_archive_tags, print_archive_tags.c.archive_id == PrintArchive.id)
        .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
        .where(print_archive_tags.c.tag_id == tag_id)
        .order_by(PrintArchive.id.desc())
        .limit(limit)
    )
    visible = visible_printer_ids(ctx)
    if visible is not None:
        astmt = astmt.where(PrintJob.printer_id.in_(visible))
    # Archive count must match the visibility-scoped list, not the global tag count.
    cstmt = (
        select(func.count())
        .select_from(print_archive_tags)
        .join(PrintArchive, PrintArchive.id == print_archive_tags.c.archive_id)
        .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
        .where(print_archive_tags.c.tag_id == tag_id)
    )
    if visible is not None:
        cstmt = cstmt.where(PrintJob.printer_id.in_(visible))
    visible_archives = (await db.execute(cstmt)).scalar_one()
    counts = counts.model_copy(
        update={
            "archives": visible_archives,
            "total": counts.total - counts.archives + visible_archives,
        }
    )
    archives = [
        UsageArchive(
            id=a.id,
            name=a.subtask_name,
            result=str(a.result.value if hasattr(a.result, "value") else a.result),
            model_id=a.model_id,
            has_thumbnail=a.thumbnail_blob is not None,
        )
        for a in (await db.execute(astmt)).scalars()
    ]
    return TagUsageOut(
        counts=counts, models=models, projects=projects, collections=collections, archives=archives
    )


@router.patch("/tags/{tag_id}", response_model=TagOut, dependencies=_W)
async def update_tag(tag_id: int, payload: TagUpdate, db: AsyncSession = Depends(get_db)) -> TagOut:
    return await tags_svc.update_tag(db, tag_id, payload)


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_W)
async def delete_tag(tag_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await tags_svc.delete_tag(db, tag_id)


@router.post("/tags/{tag_id}/merge", response_model=TagOut, dependencies=_W)
async def merge_tag(tag_id: int, payload: TagMergeIn, db: AsyncSession = Depends(get_db)) -> TagOut:
    """Re-point every usage of ``tag_id`` to ``into_id`` (dupes collapse), then delete it."""
    return await tags_svc.merge_tags(db, tag_id, payload.into_id)


@router.delete(
    "/models/{model_id}/tags/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_W,
)
async def remove_tag(model_id: int, name: str, db: AsyncSession = Depends(get_db)) -> None:
    await library.remove_tag_from_model(db, model_id, name)


# -- per-entity tag sets (replace semantics) ---------------------------


@router.put("/models/{model_id}/tags", response_model=list[TagRef], dependencies=_W)
async def set_model_tags(
    model_id: int, payload: TagIdsIn, db: AsyncSession = Depends(get_db)
) -> list[TagRef]:
    model = await library.get_model_by_id(db, model_id)
    refs = await tags_svc.set_entity_tags(db, "model", model_id, payload.tag_ids)
    model.updated_at = func.now()
    await db.commit()
    return refs


@router.put("/models/{model_id}/files/tags", response_model=list[TagRef], dependencies=_W)
async def set_model_file_tags(
    model_id: int, payload: FileTagIdsIn, db: AsyncSession = Depends(get_db)
) -> list[TagRef]:
    await library.get_model_by_id(db, model_id)
    return await tags_svc.set_file_tags(db, model_id, payload.rel_path, payload.tag_ids)


@router.put("/projects/{project_id}/tags", response_model=list[TagRef], dependencies=_W)
async def set_project_tags(
    project_id: int,
    payload: TagIdsIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[TagRef]:
    await projects_svc.get_owned(db, ctx, project_id)
    return await tags_svc.set_entity_tags(db, "project", project_id, payload.tag_ids)


@router.put(
    "/projects/{project_id}/attachments/{attachment_id}/tags",
    response_model=list[TagRef],
    dependencies=_W,
)
async def set_attachment_tags(
    project_id: int,
    attachment_id: int,
    payload: TagIdsIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[TagRef]:
    await projects_svc.get_owned(db, ctx, project_id)
    att = await db.scalar(
        select(ProjectAttachment.id).where(
            ProjectAttachment.id == attachment_id, ProjectAttachment.project_id == project_id
        )
    )
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "attachment not found")
    return await tags_svc.set_entity_tags(db, "attachment", attachment_id, payload.tag_ids)


@router.put("/collections/{collection_id}/tags", response_model=list[TagRef], dependencies=_W)
async def set_collection_tags(
    collection_id: int,
    payload: TagIdsIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[TagRef]:
    row = await db.get(FollowedCollection, collection_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "collection not found")
    require_owner_or_admin(ctx, row.created_by_user_id)
    return await tags_svc.set_entity_tags(db, "collection", collection_id, payload.tag_ids)
