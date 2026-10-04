"""Projects: grouping models/parts with workflow progress tracking.
CRUD operations and aggregation calculations.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.library import Model, Project
from app.schemas.projects import ProjectOut
from app.services import layout


async def _unique_project_slug(db: AsyncSession, name: str, exclude_id: int | None = None) -> str:
    base = layout.slug_for(name)
    slug = base
    suffix = 2
    while True:
        stmt = select(Project.id).where(Project.slug == slug)
        if exclude_id is not None:
            stmt = stmt.where(Project.id != exclude_id)
        existing = await db.scalar(stmt)
        if existing is None:
            break
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


async def list_projects(db: AsyncSession) -> list[ProjectOut]:
    """List all projects with aggregated model count, target and printed quantities."""
    rows = (
        await db.execute(
            select(
                Project,
                func.count(Model.id).label("model_count"),
                func.coalesce(func.sum(Model.quantity_target), 0).label("total_quantity_target"),
                func.coalesce(func.sum(Model.quantity_printed), 0).label("total_quantity_printed"),
            )
            .outerjoin(Model, Model.project_id == Project.id)
            .group_by(Project.id)
            .order_by(Project.name)
        )
    ).all()

    items: list[ProjectOut] = []
    for project, count, target, printed in rows:
        target_int = int(target)
        printed_int = int(printed)
        progress = (
            round(min(100.0, (printed_int / target_int) * 100.0), 1) if target_int > 0 else 0.0
        )
        items.append(
            ProjectOut(
                id=project.id,
                name=project.name,
                slug=project.slug,
                description=project.description,
                color=project.color,
                icon=project.icon,
                parent_id=project.parent_id,
                created_at=project.created_at,
                updated_at=project.updated_at,
                model_count=count,
                total_quantity_target=target_int,
                total_quantity_printed=printed_int,
                progress_pct=progress,
            )
        )
    return items


async def _get_project_or_404(db: AsyncSession, project_id: int) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"project {project_id} not found")
    return project


async def get_project(db: AsyncSession, project_id: int) -> ProjectOut:
    project = await _get_project_or_404(db, project_id)
    row = (
        await db.execute(
            select(
                func.count(Model.id),
                func.coalesce(func.sum(Model.quantity_target), 0),
                func.coalesce(func.sum(Model.quantity_printed), 0),
            ).where(Model.project_id == project.id)
        )
    ).one()
    count, target, printed = row
    target_int = int(target)
    printed_int = int(printed)
    progress = round(min(100.0, (printed_int / target_int) * 100.0), 1) if target_int > 0 else 0.0
    return ProjectOut(
        id=project.id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        color=project.color,
        icon=project.icon,
        parent_id=project.parent_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
        model_count=count,
        total_quantity_target=target_int,
        total_quantity_printed=printed_int,
        progress_pct=progress,
    )


async def create_project(
    db: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    color: str | None = None,
    icon: str | None = None,
    parent_id: int | None = None,
) -> ProjectOut:
    if parent_id is not None:
        parent = await db.get(Project, parent_id)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"parent project {parent_id} not found")

    slug = await _unique_project_slug(db, name)
    project = Project(
        name=name, slug=slug, description=description, color=color, icon=icon, parent_id=parent_id
    )
    db.add(project)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"project {name!r} already exists") from exc
    return ProjectOut(
        id=project.id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        color=project.color,
        icon=project.icon,
        parent_id=project.parent_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
        model_count=0,
        total_quantity_target=0,
        total_quantity_printed=0,
        progress_pct=0.0,
    )


async def update_project(
    db: AsyncSession, project_id: int, changes: dict[str, object]
) -> ProjectOut:
    project = await _get_project_or_404(db, project_id)
    if "name" in changes and changes["name"] != project.name:
        new_name = str(changes["name"])
        project.name = new_name
        project.slug = await _unique_project_slug(db, new_name, exclude_id=project.id)
    if "description" in changes:
        project.description = changes["description"]  # type: ignore[assignment]
    if "color" in changes:
        project.color = changes["color"]  # type: ignore[assignment]
    if "icon" in changes:
        project.icon = changes["icon"]  # type: ignore[assignment]
    if "parent_id" in changes:
        new_parent_id = changes["parent_id"]
        if new_parent_id is not None:
            if new_parent_id == project_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "A project cannot be its own parent"
                )
            parent = await db.get(Project, new_parent_id)
            if parent is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, f"parent project {new_parent_id} not found"
                )
            # Walk the new parent's ancestors: project_id among them is a cycle.
            seen: set[int] = set()
            ancestor: Project | None = parent
            while ancestor is not None and ancestor.id not in seen:
                if ancestor.id == project_id:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "A project cannot be moved under one of its own descendants",
                    )
                seen.add(ancestor.id)
                ancestor = (
                    await db.get(Project, ancestor.parent_id)
                    if ancestor.parent_id is not None
                    else None
                )
        project.parent_id = new_parent_id  # type: ignore[assignment]

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"project {changes.get('name')!r} already exists"
        ) from exc

    return await get_project(db, project.id)


async def delete_project(db: AsyncSession, project_id: int) -> None:
    """Deletes project; models.project_id ON DELETE SET NULL clears project reference on models."""
    project = await _get_project_or_404(db, project_id)
    await db.delete(project)
    await db.commit()
