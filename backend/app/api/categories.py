"""Categories: global listing (with model counts) + CRUD (R13b)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perm
from app.db import get_db
from app.schemas.categories import CategoryCreate, CategoryOut, CategoryUpdate
from app.services import categories as categories_service

router = APIRouter(tags=["categories"])


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(db: AsyncSession = Depends(get_db)) -> list[CategoryOut]:
    return await categories_service.list_categories(db)


@router.post(
    "/categories",
    status_code=status.HTTP_201_CREATED,
    response_model=CategoryOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def create_category(
    payload: CategoryCreate, db: AsyncSession = Depends(get_db)
) -> CategoryOut:
    return await categories_service.create_category(db, name=payload.name, color=payload.color)


@router.patch(
    "/categories/{category_id}",
    response_model=CategoryOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def update_category(
    category_id: int, payload: CategoryUpdate, db: AsyncSession = Depends(get_db)
) -> CategoryOut:
    return await categories_service.update_category(
        db, category_id, payload.model_dump(exclude_unset=True)
    )


@router.delete(
    "/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("library:write"))],
)
async def delete_category(category_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await categories_service.delete_category(db, category_id)
