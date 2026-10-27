"""Shared tag vocabulary: CRUD/merge, per-entity tag sets, ``tag_refs`` loaders
and any/all filter clauses over the per-entity join tables.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import Table, delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.library import File, Revision, Tag, model_tags
from app.models.tag_links import (
    followed_collection_tags,
    model_file_tags,
    print_archive_tags,
    project_attachment_tags,
    project_tags,
)
from app.schemas.library import TagCounts, TagCreate, TagOut, TagRef, TagUpdate

TagMode = Literal["any", "all"]
Kind = Literal["model", "project", "attachment", "archive", "collection"]

# kind -> (join table, entity-id column name)
_KINDS: dict[str, tuple[Table, str]] = {
    "model": (model_tags, "model_id"),
    "project": (project_tags, "project_id"),
    "attachment": (project_attachment_tags, "attachment_id"),
    "archive": (print_archive_tags, "archive_id"),
    "collection": (followed_collection_tags, "collection_id"),
}
# Every join table, for merge (file tags included).
_ALL_JOINS: tuple[Table, ...] = (*(t for t, _ in _KINDS.values()), model_file_tags)
_COUNT_KEYS: dict[str, Table] = {
    "models": model_tags,
    "model_files": model_file_tags,
    "projects": project_tags,
    "attachments": project_attachment_tags,
    "archives": print_archive_tags,
    "collections": followed_collection_tags,
}


def to_ref(t: Tag) -> TagRef:
    return TagRef(id=t.id, name=t.name, color=t.color, icon=t.icon, description=t.description)


def _norm_color(color: str | None) -> str | None:
    return color.lower() if color else None


# -- vocabulary -------------------------------------------------------


async def find_by_name(db: AsyncSession, name: str) -> Tag | None:
    return (
        await db.execute(select(Tag).where(func.lower(Tag.name) == name.lower()))
    ).scalar_one_or_none()


async def get_or_create_tag(db: AsyncSession, name: str, color: str | None = None) -> Tag:
    """Case-insensitive get-or-create by name (flushes, never commits)."""
    tag = await find_by_name(db, name)
    if tag is None:
        tag = Tag(name=name, color=_norm_color(color))
        db.add(tag)
        await db.flush()
    return tag


async def _get_tag(db: AsyncSession, tag_id: int) -> Tag:
    tag = await db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"tag {tag_id} not found")
    return tag


async def tag_counts(db: AsyncSession) -> dict[int, TagCounts]:
    out: dict[int, TagCounts] = {}
    for key, table in _COUNT_KEYS.items():
        rows = (
            await db.execute(select(table.c.tag_id, func.count()).group_by(table.c.tag_id))
        ).all()
        for tag_id, n in rows:
            c = out.setdefault(tag_id, TagCounts())
            setattr(c, key, n)
            c.total += n
    return out


async def list_tags(db: AsyncSession, *, with_counts: bool = False) -> list[TagOut]:
    tags = (await db.execute(select(Tag).order_by(func.lower(Tag.name)))).scalars().all()
    counts = await tag_counts(db) if with_counts else {}
    return [
        TagOut(
            **to_ref(t).model_dump(),
            counts=(counts.get(t.id) or TagCounts()) if with_counts else None,
        )
        for t in tags
    ]


async def create_tag(db: AsyncSession, payload: TagCreate) -> TagOut:
    if await find_by_name(db, payload.name) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"tag {payload.name!r} already exists")
    tag = Tag(
        name=payload.name,
        color=_norm_color(payload.color),
        icon=payload.icon,
        description=payload.description,
    )
    db.add(tag)
    await db.commit()
    return TagOut(**to_ref(tag).model_dump())


async def update_tag(db: AsyncSession, tag_id: int, payload: TagUpdate) -> TagOut:
    tag = await _get_tag(db, tag_id)
    sent = payload.model_fields_set
    if "name" in sent and payload.name is not None and payload.name != tag.name:
        other = await find_by_name(db, payload.name)
        if other is not None and other.id != tag.id:
            raise HTTPException(status.HTTP_409_CONFLICT, f"tag {payload.name!r} already exists")
        tag.name = payload.name
    if "color" in sent:
        tag.color = _norm_color(payload.color)
    if "icon" in sent:
        tag.icon = payload.icon
    if "description" in sent:
        tag.description = payload.description
    await db.commit()
    return TagOut(**to_ref(tag).model_dump())


async def delete_tag(db: AsyncSession, tag_id: int) -> None:
    tag = await _get_tag(db, tag_id)
    await db.delete(tag)  # join rows go via ON DELETE CASCADE
    await db.commit()


async def merge_tags(db: AsyncSession, source_id: int, into_id: int) -> TagOut:
    if source_id == into_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot merge a tag into itself")
    source = await _get_tag(db, source_id)
    target = await _get_tag(db, into_id)
    for table in _ALL_JOINS:
        other_cols = [c for c in table.c if c.name != "tag_id"]
        sel = select(*other_cols, literal(target.id).label("tag_id")).where(
            table.c.tag_id == source.id
        )
        await db.execute(
            pg_insert(table)
            .from_select([c.name for c in other_cols] + ["tag_id"], sel)
            .on_conflict_do_nothing()
        )
    await db.delete(source)
    await db.commit()
    return TagOut(**to_ref(target).model_dump())


# -- per-entity sets --------------------------------------------------


async def _validate_tag_ids(db: AsyncSession, tag_ids: Iterable[int]) -> list[int]:
    ids = list(dict.fromkeys(tag_ids))
    if ids:
        found = set((await db.execute(select(Tag.id).where(Tag.id.in_(ids)))).scalars())
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"tag(s) not found: {missing}")
    return ids


async def _replace(
    db: AsyncSession, table: Table, where: dict[str, object], tag_ids: list[int]
) -> None:
    conds = [table.c[k] == v for k, v in where.items()]
    await db.execute(delete(table).where(*conds, table.c.tag_id.not_in(tag_ids)))
    if tag_ids:
        await db.execute(
            pg_insert(table)
            .values([{**where, "tag_id": t} for t in tag_ids])
            .on_conflict_do_nothing()
        )


async def set_entity_tags(
    db: AsyncSession, kind: Kind, entity_id: int, tag_ids: Sequence[int]
) -> list[TagRef]:
    """Replace the entity's tag set (caller has already 404-checked the entity)."""
    table, col = _KINDS[kind]
    ids = await _validate_tag_ids(db, tag_ids)
    await _replace(db, table, {col: entity_id}, ids)
    await db.commit()
    return (await refs_for(db, kind, [entity_id])).get(entity_id, [])


async def set_file_tags(
    db: AsyncSession, model_id: int, rel_path: str, tag_ids: Sequence[int]
) -> list[TagRef]:
    """Replace one file path's tags. The path must exist in some revision of the model."""
    exists = await db.scalar(
        select(File.id)
        .join(Revision, Revision.id == File.revision_id)
        .where(Revision.model_id == model_id, File.rel_path == rel_path)
        .limit(1)
    )
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"file {rel_path!r} not found")
    ids = await _validate_tag_ids(db, tag_ids)
    await _replace(db, model_file_tags, {"model_id": model_id, "rel_path": rel_path}, ids)
    await db.commit()
    return (await file_refs_for(db, model_id)).get(rel_path, [])


# -- tag_refs loaders -------------------------------------------------


async def refs_for(db: AsyncSession, kind: Kind, ids: Sequence[int]) -> dict[int, list[TagRef]]:
    if not ids:
        return {}
    table, col = _KINDS[kind]
    rows = (
        await db.execute(
            select(table.c[col], Tag)
            .join(Tag, Tag.id == table.c.tag_id)
            .where(table.c[col].in_(list(ids)))
            .order_by(func.lower(Tag.name))
        )
    ).all()
    out: dict[int, list[TagRef]] = {}
    for eid, tag in rows:
        out.setdefault(eid, []).append(to_ref(tag))
    return out


async def file_refs_for(db: AsyncSession, model_id: int) -> dict[str, list[TagRef]]:
    rows = (
        await db.execute(
            select(model_file_tags.c.rel_path, Tag)
            .join(Tag, Tag.id == model_file_tags.c.tag_id)
            .where(model_file_tags.c.model_id == model_id)
            .order_by(func.lower(Tag.name))
        )
    ).all()
    out: dict[str, list[TagRef]] = {}
    for path, tag in rows:
        out.setdefault(path, []).append(to_ref(tag))
    return out


# -- filters ----------------------------------------------------------


def tag_filter(
    kind: Kind, id_col: ColumnElement, tag_ids: Sequence[int], mode: TagMode
) -> ColumnElement[bool]:
    """``id_col IN (entities having any/all of tag_ids)``."""
    table, col = _KINDS[kind]
    ids = list(dict.fromkeys(tag_ids))
    sub = select(table.c[col]).where(table.c.tag_id.in_(ids))
    if mode == "all":
        sub = sub.group_by(table.c[col]).having(
            func.count(func.distinct(table.c.tag_id)) == len(ids)
        )
    return id_col.in_(sub)


def file_tag_filter(
    model_id_col: ColumnElement, tag_ids: Sequence[int], mode: TagMode
) -> ColumnElement[bool]:
    """Models having a file path (any revision) tagged with any/all of ``tag_ids``;
    for ``all`` a single file path must carry every tag."""
    t = model_file_tags
    ids = list(dict.fromkeys(tag_ids))
    sub = select(t.c.model_id).where(t.c.tag_id.in_(ids))
    if mode == "all":
        sub = sub.group_by(t.c.model_id, t.c.rel_path).having(
            func.count(func.distinct(t.c.tag_id)) == len(ids)
        )
    return model_id_col.in_(sub)


def file_paths_with_tags(model_id: int, tag_ids: Sequence[int], mode: TagMode):
    """Subquery of rel_paths of one model carrying any/all of ``tag_ids``."""
    t = model_file_tags
    ids = list(dict.fromkeys(tag_ids))
    sub = select(t.c.rel_path).where(t.c.model_id == model_id, t.c.tag_id.in_(ids))
    if mode == "all":
        sub = sub.group_by(t.c.rel_path).having(func.count(func.distinct(t.c.tag_id)) == len(ids))
    return sub
