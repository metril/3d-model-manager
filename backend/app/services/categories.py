"""Categories: single-valued, exclusive model grouping (R13b) -- distinct
from `app.services.library`'s tag CRUD (many-to-many): a model has at most
one category, a plain FK column (`Model.category_id`, ON DELETE SET NULL).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.library import Category, Model
from app.schemas.categories import CategoryOut


async def list_categories(db: AsyncSession) -> list[CategoryOut]:
    """One `GROUP BY`/`COUNT` query for every category's `model_count`,
    never one per category."""
    rows = (
        await db.execute(
            select(Category, func.count(Model.id))
            .outerjoin(Model, (Model.category_id == Category.id) & Model.deleted_at.is_(None))
            .group_by(Category.id)
            .order_by(Category.name)
        )
    ).all()
    return [
        CategoryOut(id=c.id, name=c.name, color=c.color, model_count=count) for c, count in rows
    ]


async def create_category(db: AsyncSession, *, name: str, color: str | None) -> CategoryOut:
    category = Category(name=name, color=color)
    db.add(category)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"category {name!r} already exists") from exc
    return CategoryOut(id=category.id, name=category.name, color=category.color, model_count=0)


async def _get_category_or_404(db: AsyncSession, category_id: int) -> Category:
    category = await db.get(Category, category_id)
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"category {category_id} not found")
    return category


async def update_category(
    db: AsyncSession, category_id: int, changes: dict[str, object]
) -> CategoryOut:
    """Apply `changes` (already `exclude_unset`-filtered by the caller) --
    only fields present in the request body change."""
    category = await _get_category_or_404(db, category_id)
    if "name" in changes:
        category.name = changes["name"]
    if "color" in changes:
        category.color = changes["color"]
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"category {changes.get('name')!r} already exists"
        ) from exc
    model_count = (
        await db.scalar(
            select(func.count(Model.id)).where(
                Model.category_id == category.id, Model.deleted_at.is_(None)
            )
        )
    ) or 0
    return CategoryOut(
        id=category.id, name=category.name, color=category.color, model_count=model_count
    )


async def delete_category(db: AsyncSession, category_id: int) -> None:
    """Deletes the category row; `Model.category_id`'s `ON DELETE SET NULL`
    un-categorizes every model that referenced it (no application-level
    fixup needed here)."""
    category = await _get_category_or_404(db, category_id)
    await db.delete(category)
    await db.commit()
