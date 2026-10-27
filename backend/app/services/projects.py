"""V4 projects: CRUD, BOM, progress/cost rollups, timeline, templates,
export and "queue remaining"."""

from __future__ import annotations

import csv
import io
import json
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, visible_printer_ids
from app.config import Settings
from app.models.audit_log import AuditLog
from app.models.enums import ArchiveResult, QueueState
from app.models.library import File, Model, Print, PrintQueueEntry, Revision
from app.models.printing import PrintArchive, PrintJob
from app.models.projects import Project, ProjectAttachment, ProjectEvent, ProjectItem
from app.schemas import projects as s
from app.schemas.queue import QueueEnqueueIn
from app.services import library
from app.services import queue as queue_service
from app.services import tags as tags_svc

_TERMINAL = (QueueState.DONE, QueueState.FAILED, QueueState.SKIPPED)
MAX_ATTACHMENT = 50 * 1024 * 1024


async def get_or_404(db: AsyncSession, project_id: int) -> Project:
    p = await db.get(Project, project_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"project {project_id} not found")
    return p


async def get_owned(db: AsyncSession, ctx: AuthContext, project_id: int) -> Project:
    """Project for a MUTATION: creator or admin only (like models)."""
    p = await get_or_404(db, project_id)
    require_owner_or_admin(ctx, p.created_by_user_id)
    return p


async def log(
    db: AsyncSession,
    ctx: AuthContext | None,
    project: Project,
    kind: str,
    message: str,
    payload: dict | None = None,
) -> None:
    uid = ctx.user.id if ctx else None
    db.add(
        ProjectEvent(
            project_id=project.id, user_id=uid, kind=kind, message=message, payload=payload
        )
    )
    db.add(
        AuditLog(
            user_id=uid,
            created_by_user_id=uid,
            action=f"project_{kind}",
            payload={"project_id": project.id, "message": message, **(payload or {})},
        )
    )


# ---------------------------------------------------------------- rollups


@dataclass
class _Arc:
    id: int
    model_id: int | None
    plate: int | None
    duration_s: int | None
    filament_g: float | None
    total_cost: float | None
    explicit: bool
    result: str
    created_at: datetime
    print_job_id: int


@dataclass
class _Rollup:
    items: list[ProjectItem]
    models: dict[int, Model]
    files: dict[int, File]
    done: dict[int, int] = field(default_factory=dict)
    queued: dict[int, int] = field(default_factory=dict)
    arcs: dict[int, list[_Arc]] = field(default_factory=dict)  # item id -> archives


def _matches(item: ProjectItem, a: _Arc) -> bool:
    return item.model_id == a.model_id and (item.plate is None or item.plate == a.plate)


async def _completed_archives(
    db: AsyncSession, project: Project, model_ids: set[int], ctx: AuthContext | None = None
) -> list[_Arc]:
    eff = func.coalesce(PrintArchive.result_override, PrintArchive.result)
    cond = PrintArchive.project_id == project.id
    if model_ids:
        cond = cond | (
            PrintArchive.project_id.is_(None)
            & PrintArchive.model_id.in_(model_ids)
            & (PrintArchive.created_at >= project.created_at)
        )
    stmt = (
        select(PrintArchive, Print.filament_g, Print.total_cost)
        .outerjoin(Print, Print.print_job_id == PrintArchive.print_job_id)
        .where(cond)
        .order_by(PrintArchive.created_at.desc(), PrintArchive.id.desc())
        .add_columns(eff.label("eff"))
    )
    visible = visible_printer_ids(ctx) if ctx is not None else None
    if visible is not None:
        stmt = stmt.join(PrintJob, PrintJob.id == PrintArchive.print_job_id).where(
            PrintJob.printer_id.in_(visible)
        )
    rows = await db.execute(stmt)
    return [
        _Arc(
            a.id,
            a.model_id,
            a.plate,
            a.duration_s,
            g,
            c,
            a.project_id is not None,
            str(res.value if hasattr(res, "value") else res),
            a.created_at,
            a.print_job_id,
        )
        for a, g, c, res in rows
    ]


async def _rollup(db: AsyncSession, project: Project, ctx: AuthContext | None = None) -> _Rollup:
    items = list(
        (
            await db.execute(
                select(ProjectItem)
                .where(ProjectItem.project_id == project.id)
                .order_by(ProjectItem.position, ProjectItem.id)
            )
        )
        .scalars()
        .all()
    )
    parts = [i for i in items if i.kind == "part" and i.model_id is not None]
    mids = {i.model_id for i in parts if i.model_id is not None}
    models = (
        {
            m.id: m
            for m in (
                await db.execute(
                    select(Model).where(Model.id.in_(mids), Model.deleted_at.is_(None))
                )
            ).scalars()
        }
        if mids
        else {}
    )
    fids = {i.file_id for i in parts if i.file_id is not None}
    files = (
        {f.id: f for f in (await db.execute(select(File).where(File.id.in_(fids)))).scalars()}
        if fids
        else {}
    )
    r = _Rollup(items, models, files)
    for a in await _completed_archives(db, project, {m for m in mids if m is not None}, ctx):
        if a.result != ArchiveResult.COMPLETED.value:
            continue
        cands = [i for i in parts if _matches(i, a)]
        if not cands:
            continue
        # Prefer the plate-specific line, then the first with room left.
        cands.sort(key=lambda i: (i.plate is None, r.done.get(i.id, 0) >= i.quantity))
        tgt = cands[0]
        r.done[tgt.id] = r.done.get(tgt.id, 0) + 1
        r.arcs.setdefault(tgt.id, []).append(a)
    if mids:
        rows = await db.execute(
            select(PrintQueueEntry.model_id, PrintQueueEntry.file_id, PrintQueueEntry.copies).where(
                PrintQueueEntry.model_id.in_(mids), PrintQueueEntry.state.notin_(_TERMINAL)
            )
        )
        qrows = rows.all()
        for i in parts:
            r.queued[i.id] = sum(
                c
                for mid, fid, c in qrows
                if mid == i.model_id and (i.file_id is None or fid == i.file_id)
            )
    return r


def _item_out(i: ProjectItem, r: _Rollup) -> s.ItemOut:
    m = r.models.get(i.model_id) if i.model_id else None
    f = r.files.get(i.file_id) if i.file_id else None
    done = r.done.get(i.id, 0)
    queued = r.queued.get(i.id, 0)
    unit = float(i.unit_cost) if i.unit_cost is not None else None
    return s.ItemOut(
        id=i.id,
        kind=i.kind,
        position=i.position,
        name=i.name or (m.name if m else None),
        model_id=i.model_id,
        model_slug=m.slug if m else None,
        model_name=m.name if m else None,
        file_id=i.file_id,
        file_name=f.rel_path.rsplit("/", 1)[-1] if f else None,
        plate=i.plate,
        quantity=i.quantity,
        done=done if i.kind == "part" else 0,
        queued=queued,
        outstanding=max(0, i.quantity - done - queued) if i.kind == "part" else 0,
        acquired=i.acquired,
        unit_cost=unit,
        line_cost=None if unit is None or i.kind == "part" else round(unit * i.quantity, 2),
        url=i.url,
        notes=i.notes,
    )


def _progress(r: _Rollup) -> s.ProgressOut:
    parts = [i for i in r.items if i.kind == "part"]
    buy = [i for i in r.items if i.kind == "purchased"]
    pt = sum(i.quantity for i in parts)
    pd = sum(min(r.done.get(i.id, 0), i.quantity) for i in parts)
    bt = sum(i.quantity for i in buy)
    ba = sum(min(i.acquired, i.quantity) for i in buy)
    total, got = pt + bt, pd + ba
    return s.ProgressOut(
        parts_total=pt,
        parts_done=pd,
        parts_queued=sum(r.queued.get(i.id, 0) for i in parts),
        purchased_total=bt,
        purchased_acquired=ba,
        percent=round(100 * got / total) if total else 0,
    )


def _cost(r: _Rollup) -> s.CostRollup:
    g = t = cost = 0.0
    est = 0.0
    for i in r.items:
        if i.kind != "part":
            continue
        arcs = r.arcs.get(i.id, [])
        g += sum(a.filament_g or 0 for a in arcs)
        t += sum(a.duration_s or 0 for a in arcs)
        c = sum(a.total_cost or 0 for a in arcs)
        cost += c
        priced = [a for a in arcs if a.total_cost is not None]
        if priced:
            avg = sum(a.total_cost or 0 for a in priced) / len(priced)
            est += avg * max(0, i.quantity - len(arcs))
    buy = sum(float(i.unit_cost or 0) * i.quantity for i in r.items if i.kind == "purchased")
    return s.CostRollup(
        filament_g=round(g, 1),
        print_time_s=int(t),
        print_cost=round(cost, 2),
        purchased_cost=round(buy, 2),
        total_cost=round(cost + buy, 2),
        estimated_remaining_cost=round(est, 2),
    )


async def _attachments(db: AsyncSession, project_id: int) -> list[s.AttachmentOut]:
    rows = (
        (
            await db.execute(
                select(ProjectAttachment)
                .where(ProjectAttachment.project_id == project_id)
                .order_by(ProjectAttachment.id)
            )
        )
        .scalars()
        .all()
    )
    refs = await tags_svc.refs_for(db, "attachment", [a.id for a in rows])
    return [
        s.AttachmentOut(
            id=a.id,
            filename=a.filename,
            content_type=a.content_type,
            size=a.size,
            created_at=a.created_at,
            tag_refs=refs.get(a.id, []),
        )
        for a in rows
    ]


async def _summary_fields(db: AsyncSession, p: Project, r: _Rollup) -> dict:
    cover_id = p.cover_model_id
    if cover_id is None:
        cover_id = next((i.model_id for i in r.items if i.kind == "part" and i.model_id), None)
    slug = None
    if cover_id is not None:
        cm = r.models.get(cover_id) or await db.get(Model, cover_id)
        slug = cm.slug if cm else None
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "status": p.status,
        "due_date": p.due_date,
        "overdue": bool(
            p.due_date and p.status not in ("completed", "archived") and p.due_date < date.today()
        ),
        "cover_model_id": cover_id,
        "cover_model_slug": slug,
        "tags": list(p.tags or []),
        "tag_refs": (await tags_svc.refs_for(db, "project", [p.id])).get(p.id, []),
        "is_template": p.is_template,
        "completed_at": p.completed_at,
        "created_by_user_id": p.created_by_user_id,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "progress": _progress(r),
        "cost": _cost(r),
    }


async def to_summary(
    db: AsyncSession, p: Project, ctx: AuthContext | None = None
) -> s.ProjectSummary:
    r = await _rollup(db, p, ctx)
    return s.ProjectSummary(**await _summary_fields(db, p, r))


async def to_out(db: AsyncSession, p: Project, ctx: AuthContext | None = None) -> s.ProjectOut:
    r = await _rollup(db, p, ctx)
    return s.ProjectOut(
        **await _summary_fields(db, p, r),
        notes=p.notes,
        items=[_item_out(i, r) for i in r.items],
        attachments=await _attachments(db, p.id),
    )


# ---------------------------------------------------------------- CRUD


async def list_projects(
    db: AsyncSession,
    *,
    status_: str | None,
    q: str | None,
    template: bool,
    tag: str | None,
    ctx: AuthContext | None = None,
    tag_ids: Sequence[int] = (),
    tag_mode: tags_svc.TagMode = "any",
) -> list[s.ProjectSummary]:
    stmt = select(Project).where(Project.is_template.is_(template))
    if status_:
        stmt = stmt.where(Project.status == status_)
    elif not template:
        pass
    if q:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        stmt = stmt.where(Project.name.ilike(like) | Project.description.ilike(like))
    if tag:
        stmt = stmt.where(Project.tags.any(tag))
    if tag_ids:
        stmt = stmt.where(tags_svc.tag_filter("project", Project.id, tag_ids, tag_mode))
    stmt = stmt.order_by(Project.updated_at.desc(), Project.id.desc())
    return [await to_summary(db, p, ctx) for p in (await db.execute(stmt)).scalars().all()]


async def _check_model(db: AsyncSession, model_id: int | None) -> None:
    if model_id is not None:
        await library.get_model_by_id(db, model_id)  # 404s on missing / trashed


async def create_project(db: AsyncSession, ctx: AuthContext, payload: s.ProjectIn) -> Project:
    await _check_model(db, payload.cover_model_id)
    p = Project(
        name=payload.name.strip(),
        description=payload.description,
        status=payload.status,
        due_date=payload.due_date,
        cover_model_id=payload.cover_model_id,
        tags=s._clean_tags(payload.tags),
        notes=payload.notes,
        created_by_user_id=ctx.user.id,
        completed_at=datetime.now(UTC) if payload.status == "completed" else None,
    )
    db.add(p)
    await db.flush()
    await log(db, ctx, p, "create", f"Created project {p.name}")
    await db.commit()
    await db.refresh(p)
    return p


async def patch_project(
    db: AsyncSession, ctx: AuthContext, project_id: int, payload: s.ProjectPatch
) -> Project:
    p = await get_owned(db, ctx, project_id)
    sent = payload.model_fields_set
    changed: list[str] = []
    if "cover_model_id" in sent:
        await _check_model(db, payload.cover_model_id)
    for f in ("name", "description", "notes", "due_date", "cover_model_id"):
        if f in sent:
            v = getattr(payload, f)
            if f in ("name", "description", "notes") and v is None:
                continue
            setattr(p, f, v.strip() if f == "name" else v)
            changed.append(f)
    if "tags" in sent and payload.tags is not None:
        p.tags = s._clean_tags(payload.tags)
        changed.append("tags")
    if "status" in sent and payload.status and payload.status != p.status:
        old = p.status
        p.status = payload.status
        p.completed_at = datetime.now(UTC) if p.status == "completed" else None
        await log(
            db, ctx, p, "status", f"Status {old} -> {p.status}", {"from": old, "to": p.status}
        )
    elif changed:
        await log(db, ctx, p, "update", f"Updated {', '.join(changed)}", {"fields": changed})
    p.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(p)
    return p


def _attachment_dir(settings: Settings, project_id: int) -> Path:
    return settings.data_dir / "projects" / str(project_id)


async def delete_project(
    db: AsyncSession, settings: Settings, ctx: AuthContext, project_id: int
) -> None:
    p = await get_owned(db, ctx, project_id)
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            action="project_delete",
            payload={"project_id": p.id, "name": p.name},
        )
    )
    await db.delete(p)
    await db.commit()
    shutil.rmtree(_attachment_dir(settings, project_id), ignore_errors=True)


# ---------------------------------------------------------------- BOM


async def _next_position(db: AsyncSession, project_id: int) -> int:
    mx = (
        await db.execute(
            select(func.max(ProjectItem.position)).where(ProjectItem.project_id == project_id)
        )
    ).scalar()
    return 0 if mx is None else mx + 1


async def _validate_part(db: AsyncSession, model_id: int | None, file_id: int | None) -> None:
    if model_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "part needs model_id")
    await _check_model(db, model_id)
    if file_id is not None:
        row = (
            await db.execute(
                select(Revision.model_id)
                .join(File, File.revision_id == Revision.id)
                .where(File.id == file_id)
            )
        ).scalar()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"file {file_id} not found")
        if row != model_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "file is not on that model")


async def add_item(
    db: AsyncSession, ctx: AuthContext, project_id: int, payload: s.ItemIn
) -> ProjectItem:
    p = await get_owned(db, ctx, project_id)
    if payload.kind == "part":
        await _validate_part(db, payload.model_id, payload.file_id)
    elif not (payload.name or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "purchased item needs a name")
    item = ProjectItem(
        project_id=p.id,
        kind=payload.kind,
        position=await _next_position(db, p.id),
        name=(payload.name or "").strip() or None,
        model_id=payload.model_id if payload.kind == "part" else None,
        file_id=payload.file_id if payload.kind == "part" else None,
        plate=payload.plate if payload.kind == "part" else None,
        quantity=payload.quantity,
        acquired=payload.acquired if payload.kind == "purchased" else 0,
        unit_cost=payload.unit_cost if payload.kind == "purchased" else None,
        url=payload.url if payload.kind == "purchased" else None,
        notes=payload.notes,
    )
    db.add(item)
    await db.flush()
    await log(
        db, ctx, p, "bom_add", f"Added BOM item {item.name or item.model_id}", {"item_id": item.id}
    )
    p.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(item)
    return item


async def add_models(
    db: AsyncSession, ctx: AuthContext, project_id: int, payload: s.AddModelsIn
) -> int:
    """Bulk "add to project": one whole-model part per model not already a
    whole-model line. Returns how many were added."""
    p = await get_owned(db, ctx, project_id)
    ids = list(dict.fromkeys(payload.model_ids))
    found = set(
        (
            await db.execute(select(Model.id).where(Model.id.in_(ids), Model.deleted_at.is_(None)))
        ).scalars()
    )
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"models not found: {missing}")
    existing = set(
        (
            await db.execute(
                select(ProjectItem.model_id).where(
                    ProjectItem.project_id == p.id,
                    ProjectItem.kind == "part",
                    ProjectItem.file_id.is_(None),
                    ProjectItem.plate.is_(None),
                )
            )
        ).scalars()
    )
    pos = await _next_position(db, p.id)
    added = []
    for mid in ids:
        if mid in existing:
            continue
        db.add(
            ProjectItem(
                project_id=p.id, kind="part", model_id=mid, quantity=payload.quantity, position=pos
            )
        )
        pos += 1
        added.append(mid)
    if added:
        await log(db, ctx, p, "models_add", f"Added {len(added)} model(s)", {"model_ids": added})
        p.updated_at = datetime.now(UTC)
    await db.commit()
    return len(added)


async def _item_or_404(db: AsyncSession, project_id: int, item_id: int) -> ProjectItem:
    i = await db.get(ProjectItem, item_id)
    if i is None or i.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"item {item_id} not found")
    return i


async def patch_item(
    db: AsyncSession, ctx: AuthContext, project_id: int, item_id: int, payload: s.ItemPatch
) -> ProjectItem:
    p = await get_owned(db, ctx, project_id)
    i = await _item_or_404(db, project_id, item_id)
    sent = payload.model_fields_set
    if "file_id" in sent and i.kind == "part":
        await _validate_part(db, i.model_id, payload.file_id)
    for f in ("name", "quantity", "notes", "file_id", "plate", "acquired", "unit_cost", "url"):
        if f not in sent:
            continue
        if f in ("file_id", "plate") and i.kind != "part":
            continue
        if f in ("acquired", "unit_cost", "url") and i.kind != "purchased":
            continue
        v = getattr(payload, f)
        if f in ("quantity", "acquired") and v is None:
            continue
        setattr(i, f, v)
    await log(
        db, ctx, p, "bom_update", f"Updated BOM item {i.name or i.model_id}", {"item_id": i.id}
    )
    p.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(i)
    return i


async def delete_item(db: AsyncSession, ctx: AuthContext, project_id: int, item_id: int) -> None:
    p = await get_owned(db, ctx, project_id)
    i = await _item_or_404(db, project_id, item_id)
    await log(
        db, ctx, p, "bom_remove", f"Removed BOM item {i.name or i.model_id}", {"item_id": i.id}
    )
    await db.delete(i)
    p.updated_at = datetime.now(UTC)
    await db.commit()


async def item_out(db: AsyncSession, project_id: int, item_id: int) -> s.ItemOut:
    p = await get_or_404(db, project_id)
    r = await _rollup(db, p)
    return _item_out(next(i for i in r.items if i.id == item_id), r)


# ---------------------------------------------------------------- archives


async def list_archives(
    db: AsyncSession, project_id: int, ctx: AuthContext | None = None
) -> list[s.ProjectArchiveOut]:
    p = await get_or_404(db, project_id)
    r = await _rollup(db, p, ctx)
    mids = {i.model_id for i in r.items if i.kind == "part" and i.model_id}
    arcs = await _completed_archives(db, p, mids, ctx)
    names = (
        {
            m.id: m.name
            for m in (
                await db.execute(
                    select(Model).where(Model.id.in_({a.model_id for a in arcs if a.model_id}))
                )
            ).scalars()
        }
        if arcs
        else {}
    )
    return [
        s.ProjectArchiveOut(
            id=a.id,
            model_id=a.model_id,
            model_name=names.get(a.model_id) if a.model_id else None,
            plate=a.plate,
            result=a.result,
            duration_s=a.duration_s,
            filament_g=a.filament_g,
            total_cost=a.total_cost,
            explicit=a.explicit,
            created_at=a.created_at,
        )
        for a in arcs
    ]


async def link_archives(
    db: AsyncSession, ctx: AuthContext, project_id: int, ids: list[int], *, unlink: bool
) -> int:
    p = await get_owned(db, ctx, project_id)
    astmt = select(PrintArchive).where(PrintArchive.id.in_(ids))
    visible = visible_printer_ids(ctx)
    if visible is not None:
        astmt = astmt.join(PrintJob, PrintJob.id == PrintArchive.print_job_id).where(
            PrintJob.printer_id.in_(visible)
        )
    arcs = (await db.execute(astmt)).scalars().all()
    n = 0
    for a in arcs:
        if unlink:
            if a.project_id == p.id:
                a.project_id = None
                n += 1
        elif a.project_id != p.id:
            a.project_id = p.id
            n += 1
    if n:
        await log(
            db,
            ctx,
            p,
            "archives_unlink" if unlink else "archives_link",
            f"{'Unlinked' if unlink else 'Linked'} {n} archive(s)",
            {"archive_ids": [a.id for a in arcs]},
        )
        p.updated_at = datetime.now(UTC)
    await db.commit()
    return n


async def timeline(
    db: AsyncSession, project_id: int, ctx: AuthContext | None = None
) -> list[s.TimelineEntry]:
    p = await get_or_404(db, project_id)
    evs = (
        (
            await db.execute(
                select(ProjectEvent)
                .where(ProjectEvent.project_id == p.id)
                .order_by(ProjectEvent.created_at.desc(), ProjectEvent.id.desc())
            )
        )
        .scalars()
        .all()
    )
    out = [
        s.TimelineEntry(
            at=e.created_at, kind=e.kind, message=e.message, user_id=e.user_id, payload=e.payload
        )
        for e in evs
    ]
    for a in await list_archives(db, p.id, ctx):
        out.append(
            s.TimelineEntry(
                at=a.created_at,
                kind="print",
                message=f"Print {a.result}: {a.model_name or 'unknown model'}",
                payload={"archive_id": a.id, "result": a.result},
            )
        )
    out.sort(key=lambda e: e.at, reverse=True)
    return out


# ---------------------------------------------------------------- attachments


def _safe_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name.strip()
    if not base or base in (".", ".."):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid filename")
    return base[:200]


async def add_attachment(
    db: AsyncSession,
    settings: Settings,
    ctx: AuthContext,
    project_id: int,
    filename: str,
    content_type: str | None,
    data: bytes,
) -> ProjectAttachment:
    p = await get_owned(db, ctx, project_id)
    name = _safe_filename(filename)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload body")
    if len(data) > MAX_ATTACHMENT:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "attachment too large")
    stored = f"{uuid.uuid4().hex}"
    d = _attachment_dir(settings, p.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / stored).write_bytes(data)
    a = ProjectAttachment(
        project_id=p.id,
        filename=name,
        stored_name=stored,
        content_type=content_type,
        size=len(data),
        created_by_user_id=ctx.user.id,
    )
    db.add(a)
    await db.flush()
    await log(db, ctx, p, "attachment_add", f"Attached {name}", {"attachment_id": a.id})
    await db.commit()
    await db.refresh(a)
    return a


async def get_attachment(
    db: AsyncSession, settings: Settings, project_id: int, attachment_id: int
) -> tuple[ProjectAttachment, Path]:
    a = await db.get(ProjectAttachment, attachment_id)
    if a is None or a.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "attachment not found")
    path = _attachment_dir(settings, project_id) / a.stored_name
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "attachment file missing")
    return a, path


async def delete_attachment(
    db: AsyncSession, settings: Settings, ctx: AuthContext, project_id: int, attachment_id: int
) -> None:
    p = await get_owned(db, ctx, project_id)
    a = await db.get(ProjectAttachment, attachment_id)
    if a is None or a.project_id != p.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "attachment not found")
    (_attachment_dir(settings, p.id) / a.stored_name).unlink(missing_ok=True)
    await log(db, ctx, p, "attachment_remove", f"Removed attachment {a.filename}")
    await db.delete(a)
    await db.commit()


# ---------------------------------------------------------------- templates


async def _copy_items(db: AsyncSession, src: Project, dst: Project) -> None:
    items = (
        (
            await db.execute(
                select(ProjectItem)
                .where(ProjectItem.project_id == src.id)
                .order_by(ProjectItem.position, ProjectItem.id)
            )
        )
        .scalars()
        .all()
    )
    for i in items:
        db.add(
            ProjectItem(
                project_id=dst.id,
                kind=i.kind,
                position=i.position,
                name=i.name,
                model_id=i.model_id,
                file_id=i.file_id,
                plate=i.plate,
                quantity=i.quantity,
                acquired=0,
                unit_cost=i.unit_cost,
                url=i.url,
                notes=i.notes,
            )
        )


async def create_template(
    db: AsyncSession, ctx: AuthContext, project_id: int, name: str
) -> Project:
    src = await get_or_404(db, project_id)
    t = Project(
        name=name.strip(),
        description=src.description,
        status="planning",
        cover_model_id=src.cover_model_id,
        tags=list(src.tags or []),
        notes=src.notes,
        is_template=True,
        created_by_user_id=ctx.user.id,
    )
    db.add(t)
    await db.flush()
    await _copy_items(db, src, t)
    await log(
        db, ctx, t, "create_template", f"Template from project {src.name}", {"source": src.id}
    )
    await db.commit()
    await db.refresh(t)
    return t


async def from_template(
    db: AsyncSession, ctx: AuthContext, template_id: int, payload: s.FromTemplateIn
) -> Project:
    t = await get_or_404(db, template_id)
    if not t.is_template:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "not a template")
    p = Project(
        name=payload.name.strip(),
        description=t.description,
        status="planning",
        due_date=payload.due_date,
        cover_model_id=t.cover_model_id,
        tags=list(t.tags or []),
        notes=t.notes,
        created_by_user_id=ctx.user.id,
    )
    db.add(p)
    await db.flush()
    await _copy_items(db, t, p)
    await log(db, ctx, p, "create", f"Created from template {t.name}", {"template": t.id})
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------- queue remaining


async def queue_remaining(
    db: AsyncSession,
    settings: Settings,
    ctx: AuthContext,
    project_id: int,
    payload: s.QueueRemainingIn,
) -> s.QueueRemainingOut:
    p = await get_owned(db, ctx, project_id)
    r = await _rollup(db, p)
    todo = [
        (i, max(0, i.quantity - r.done.get(i.id, 0) - r.queued.get(i.id, 0)))
        for i in r.items
        if i.kind == "part" and i.model_id in r.models  # trashed models drop out
    ]
    todo = [(i, n) for i, n in todo if n > 0]
    # Whole-model lines pick the model's newest sliced file.
    rev_ids = [
        r.models[i.model_id].current_revision_id
        for i, _ in todo
        if i.file_id is None and r.models[i.model_id].current_revision_id
    ]
    newest = await library.newest_printable_files(db, settings, rev_ids)
    enqueued: list[dict] = []
    skipped: list[dict] = []
    for i, n in todo:
        m = r.models[i.model_id]
        file_id = i.file_id
        if file_id is None and m.current_revision_id in newest:
            file_id = newest[m.current_revision_id].id
        if file_id is None:
            skipped.append({"item_id": i.id, "model": m.name, "reason": "no sliced file"})
            continue
        left = n
        try:
            while left > 0:
                copies = min(left, 50)
                entry = await queue_service.enqueue_file(
                    db,
                    settings,
                    QueueEnqueueIn(
                        file_id=file_id,
                        plate=i.plate,
                        copies=copies,
                        printer_id=payload.printer_id,
                        recipe_id=payload.recipe_id,
                    ),
                    ctx,
                )
                enqueued.append({"item_id": i.id, "queue_id": entry.id, "copies": copies})
                left -= copies
        except HTTPException as e:
            skipped.append({"item_id": i.id, "model": m.name, "reason": str(e.detail)})
    if enqueued:
        if p.status == "planning":
            p.status = "active"
        await log(
            db,
            ctx,
            p,
            "queue",
            f"Queued {sum(e['copies'] for e in enqueued)} outstanding print(s)",
            {"entries": [e["queue_id"] for e in enqueued]},
        )
        p.updated_at = datetime.now(UTC)
    await db.commit()
    return s.QueueRemainingOut(enqueued=enqueued, skipped=skipped)


# ---------------------------------------------------------------- export


def bom_csv(out: s.ProjectOut) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "kind",
            "name",
            "model",
            "file",
            "plate",
            "quantity",
            "done",
            "acquired",
            "unit_cost",
            "line_cost",
            "url",
            "notes",
        ]
    )
    for i in out.items:
        w.writerow(
            [
                i.kind,
                i.name or "",
                i.model_name or "",
                i.file_name or "",
                i.plate or "",
                i.quantity,
                i.done if i.kind == "part" else "",
                i.acquired if i.kind == "purchased" else "",
                "" if i.unit_cost is None else i.unit_cost,
                "" if i.line_cost is None else i.line_cost,
                i.url or "",
                i.notes or "",
            ]
        )
    return buf.getvalue()


def export_json(out: s.ProjectOut) -> str:
    return json.dumps(out.model_dump(mode="json"), indent=2)
