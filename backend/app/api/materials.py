"""Materials: global listing (with print counts) + CRUD (R13c)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perm
from app.db import get_db
from app.schemas.materials import MaterialCreate, MaterialOut, MaterialUpdate
from app.services import materials as materials_service

router = APIRouter(tags=["materials"])


@router.get("/materials", response_model=list[MaterialOut])
async def list_materials(db: AsyncSession = Depends(get_db)) -> list[MaterialOut]:
    return await materials_service.list_materials(db)


@router.post(
    "/materials",
    status_code=status.HTTP_201_CREATED,
    response_model=MaterialOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def create_material(
    payload: MaterialCreate, db: AsyncSession = Depends(get_db)
) -> MaterialOut:
    return await materials_service.create_material(
        db,
        name=payload.name,
        kind=payload.kind,
        color=payload.color,
        vendor=payload.vendor,
        notes=payload.notes,
    )


@router.patch(
    "/materials/{material_id}",
    response_model=MaterialOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def update_material(
    material_id: int, payload: MaterialUpdate, db: AsyncSession = Depends(get_db)
) -> MaterialOut:
    return await materials_service.update_material(
        db, material_id, payload.model_dump(exclude_unset=True)
    )


@router.delete(
    "/materials/{material_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def delete_material(material_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await materials_service.delete_material(db, material_id)
