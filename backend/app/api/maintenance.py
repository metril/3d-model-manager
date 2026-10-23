"""Per-printer maintenance tasks + maintenance mode (Q7). Reads need printer
visibility; every mutation needs ``printers:control`` on that printer."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    require_printer_access,
    require_printer_enabled,
    require_session,
    visible_printer_ids,
)
from app.db import get_db
from app.models import MaintenanceLog, MaintenanceTask, Printer
from app.schemas.ops import (
    MaintenanceDoneIn,
    MaintenanceLogOut,
    MaintenanceModeIn,
    MaintenanceModeOut,
    MaintenanceTaskIn,
    MaintenanceTaskOut,
    MaintenanceTaskPatch,
)
from app.services import maintenance as svc

router = APIRouter(
    prefix="/printers/{printer_id}/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(require_printer_enabled)],
)
_control = Depends(require_printer_access("control"))


async def _printer(db: AsyncSession, printer_id: int) -> Printer:
    p = await db.get(Printer, printer_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    return p


async def _task(db: AsyncSession, printer_id: int, task_id: int) -> MaintenanceTask:
    t = await db.get(MaintenanceTask, task_id)
    if t is None or t.printer_id != printer_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "maintenance task not found")
    return t


def _out(t: MaintenanceTask, hours: float) -> MaintenanceTaskOut:
    d = svc.due_status(t, hours)
    return MaintenanceTaskOut(
        id=t.id,
        printer_id=t.printer_id,
        name=t.name,
        interval_hours=t.interval_hours,
        interval_days=t.interval_days,
        last_done_at=t.last_done_at,
        notes=t.notes,
        status=d.status,
        hours_since=d.hours_since,
        days_since=d.days_since,
        hours_remaining=d.hours_remaining,
        days_remaining=d.days_remaining,
    )


@router.get("", response_model=list[MaintenanceTaskOut])
async def list_tasks(
    printer_id: int,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> list[MaintenanceTaskOut]:
    visible = visible_printer_ids(ctx)
    if visible is not None and printer_id not in visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    printer = await _printer(db, printer_id)
    hours = await svc.printer_hours(db, printer_id)
    tasks = (
        await db.scalars(
            select(MaintenanceTask)
            .where(MaintenanceTask.printer_id == printer.id)
            .order_by(MaintenanceTask.id)
        )
    ).all()
    return [_out(t, hours) for t in tasks]


@router.post("", response_model=MaintenanceTaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    printer_id: int,
    payload: MaintenanceTaskIn,
    ctx: AuthContext = _control,
    db: AsyncSession = Depends(get_db),
) -> MaintenanceTaskOut:
    await _printer(db, printer_id)
    t = MaintenanceTask(
        printer_id=printer_id,
        created_by_user_id=ctx.user.id,
        last_done_hours=await svc.printer_hours(db, printer_id),
        **payload.model_dump(),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _out(t, await svc.printer_hours(db, printer_id))


@router.put("/mode", response_model=MaintenanceModeOut)
async def set_mode(
    printer_id: int,
    payload: MaintenanceModeIn,
    _ctx: AuthContext = _control,
    db: AsyncSession = Depends(get_db),
) -> MaintenanceModeOut:
    printer = await _printer(db, printer_id)
    printer.maintenance_mode = payload.enabled
    await db.commit()
    return MaintenanceModeOut(maintenance_mode=printer.maintenance_mode)


@router.patch("/{task_id}", response_model=MaintenanceTaskOut)
async def update_task(
    printer_id: int,
    task_id: int,
    payload: MaintenanceTaskPatch,
    _ctx: AuthContext = _control,
    db: AsyncSession = Depends(get_db),
) -> MaintenanceTaskOut:
    t = await _task(db, printer_id, task_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None and key == "name":
            continue
        setattr(t, key, value)
    await db.commit()
    await db.refresh(t)
    return _out(t, await svc.printer_hours(db, printer_id))


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    printer_id: int,
    task_id: int,
    _ctx: AuthContext = _control,
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.delete(await _task(db, printer_id, task_id))
    await db.commit()


@router.post("/{task_id}/done", response_model=MaintenanceTaskOut)
async def mark_done(
    printer_id: int,
    task_id: int,
    payload: MaintenanceDoneIn,
    ctx: AuthContext = _control,
    db: AsyncSession = Depends(get_db),
) -> MaintenanceTaskOut:
    t = await _task(db, printer_id, task_id)
    hours = await svc.printer_hours(db, printer_id)
    now = datetime.now(UTC)
    db.add(
        MaintenanceLog(
            task_id=t.id,
            done_at=now,
            hours_at=hours,
            note=payload.note,
            created_by_user_id=ctx.user.id,
        )
    )
    t.last_done_at, t.last_done_hours = now, hours
    await db.commit()
    await db.refresh(t)
    return _out(t, hours)


@router.get("/{task_id}/logs", response_model=list[MaintenanceLogOut])
async def list_logs(
    printer_id: int,
    task_id: int,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> list[MaintenanceLog]:
    visible = visible_printer_ids(ctx)
    if visible is not None and printer_id not in visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    await _task(db, printer_id, task_id)
    return list(
        (
            await db.scalars(
                select(MaintenanceLog)
                .where(MaintenanceLog.task_id == task_id)
                .order_by(MaintenanceLog.done_at.desc(), MaintenanceLog.id.desc())
            )
        ).all()
    )
