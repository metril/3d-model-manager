"""Tags: shared vocabulary CRUD/merge + per-entity tag sets (SPEC "API
surface"). Per-entity ``PUT .../tags`` routes for models, model files,
projects, attachments, archives and followed collections.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.db import get_db
from app.models.collections import FollowedCollection
from app.models.projects import Project, ProjectAttachment
from app.schemas.library import (
    FileTagIdsIn,
    TagCreate,
    TagIdsIn,
    TagMergeIn,
    TagOut,
    TagRef,
    TagUpdate,
)
from app.services import library
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
    project_id: int, payload: TagIdsIn, db: AsyncSession = Depends(get_db)
) -> list[TagRef]:
    if await db.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return await tags_svc.set_entity_tags(db, "project", project_id, payload.tag_ids)


@router.put(
    "/projects/{project_id}/attachments/{attachment_id}/tags",
    response_model=list[TagRef],
    dependencies=_W,
)
async def set_attachment_tags(
    project_id: int, attachment_id: int, payload: TagIdsIn, db: AsyncSession = Depends(get_db)
) -> list[TagRef]:
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
