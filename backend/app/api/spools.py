"""Spool inventory CRUD + manual usage (P5 spool tray plan, Task 6).

Slot assignment (``PUT /printers/{id}/slots/{unit}/{slot}``) lives in
``app.api.printers`` instead, next to the other per-printer control routes --
see that module.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.db import get_db
from app.models import Model
from app.models.enums import SpoolUsageSource
from app.models.inventory import Spool, SpoolSlot, SpoolUsage
from app.models.printing import PrintArchive, PrintJob
from app.schemas.inventory import (
    ManualUsageIn,
    SeriesPoint,
    SpoolCreate,
    SpoolDetailOut,
    SpoolOut,
    SpoolSlotOut,
    SpoolUpdate,
    UsageRowOut,
)

router = APIRouter(prefix="/spools", tags=["spools"])


async def _get_or_404(db: AsyncSession, spool_id: int) -> Spool:
    spool = await db.get(Spool, spool_id)
    if spool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "spool not found")
    return spool


async def _slot_for(db: AsyncSession, spool_id: int) -> SpoolSlotOut | None:
    row = (
        (await db.execute(select(SpoolSlot).where(SpoolSlot.spool_id == spool_id)))
        .scalars()
        .first()
    )
    if row is None:
        return None
    return SpoolSlotOut(printer_id=row.printer_id, ams_unit=row.ams_unit, slot=row.slot)


async def _out(db: AsyncSession, s: Spool) -> SpoolOut:
    return SpoolOut.from_model(s, await _slot_for(db, s.id))


@router.get("", response_model=list[SpoolOut])
async def list_spools(
    archived: bool = False,
    low_stock: bool | None = None,
    material_type: str | None = None,
    q: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[SpoolOut]:
    stmt = select(Spool)
    stmt = stmt.where(Spool.archived_at.is_not(None) if archived else Spool.archived_at.is_(None))
    if material_type is not None:
        stmt = stmt.where(Spool.material_type == material_type)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Spool.brand.ilike(like), Spool.color_name.ilike(like)))
    rows = (await db.execute(stmt.order_by(Spool.id))).scalars().all()
    out = [await _out(db, s) for s in rows]
    if low_stock is not None:
        out = [o for o in out if o.low_stock == low_stock]
    return out


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SpoolOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def create_spool(
    payload: SpoolCreate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> SpoolOut:
    spool = Spool(**payload.model_dump(), created_by_user_id=ctx.user.id)
    db.add(spool)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "tray_uuid already in use") from e
    await db.refresh(spool)
    return await _out(db, spool)


@router.get("/{spool_id}", response_model=SpoolDetailOut)
async def get_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> SpoolDetailOut:
    spool = await _get_or_404(db, spool_id)
    rows = (
        await db.execute(
            select(SpoolUsage, PrintArchive, Model)
            .outerjoin(PrintJob, PrintJob.id == SpoolUsage.print_job_id)
            .outerjoin(PrintArchive, PrintArchive.print_job_id == PrintJob.id)
            .outerjoin(Model, Model.id == PrintArchive.model_id)
            .where(SpoolUsage.spool_id == spool_id)
            .order_by(SpoolUsage.created_at)
        )
    ).all()
    usage = [
        UsageRowOut(
            id=u.id,
            print_job_id=u.print_job_id,
            archive_id=arc.id if arc else None,
            model_name=model.name if model else None,
            grams=u.grams,
            source=u.source,
            created_at=u.created_at,
        )
        for u, arc, model in rows
    ]
    # `weight_used_g` can include usage never backed by a `spool_usage` row
    # (e.g. a direct PATCH) -- start the series at `label - (weight_used_g -
    # Σ(usage rows))` instead of flatly at `label_weight_g`, so the series'
    # first point lines up with the spool's actual current `remaining_g`
    # once every usage row below has been replayed.
    usage_sum = sum(u.grams for u, _arc, _model in rows)
    baseline_used = spool.weight_used_g - usage_sum
    start_remaining = spool.label_weight_g - baseline_used
    series: list[SeriesPoint] = [SeriesPoint(t=spool.created_at, remaining_g=start_remaining)]
    remaining = start_remaining
    for u, _arc, _model in rows:
        remaining = max(remaining - u.grams, 0.0)
        series.append(SeriesPoint(t=u.created_at, remaining_g=remaining))
    base = await _out(db, spool)
    return SpoolDetailOut(**base.model_dump(), usage=usage, series=series)


@router.patch(
    "/{spool_id}",
    response_model=SpoolOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def update_spool(
    spool_id: int,
    payload: SpoolUpdate,
    db: AsyncSession = Depends(get_db),
) -> SpoolOut:
    spool = await _get_or_404(db, spool_id)
    data = payload.model_dump(exclude_unset=True)
    archived = data.pop("archived", None)
    for key, value in data.items():
        setattr(spool, key, value)
    if archived is True and spool.archived_at is None:
        spool.archived_at = datetime.now(UTC)
    elif archived is False:
        spool.archived_at = None
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "tray_uuid already in use") from e
    await db.refresh(spool)
    return await _out(db, spool)


@router.delete(
    "/{spool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def delete_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    spool = await _get_or_404(db, spool_id)
    require_owner_or_admin(ctx, spool.created_by_user_id)
    has_usage = (
        await db.execute(select(SpoolUsage.id).where(SpoolUsage.spool_id == spool_id).limit(1))
    ).first()
    if has_usage is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "spool has usage history; archive it instead")
    await db.delete(spool)
    await db.commit()


@router.post(
    "/{spool_id}/usage",
    status_code=status.HTTP_201_CREATED,
    response_model=UsageRowOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def add_manual_usage(
    spool_id: int,
    payload: ManualUsageIn,
    db: AsyncSession = Depends(get_db),
) -> UsageRowOut:
    spool = await _get_or_404(db, spool_id)
    row = SpoolUsage(spool_id=spool_id, grams=payload.grams, source=SpoolUsageSource.MANUAL)
    spool.weight_used_g += payload.grams
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return UsageRowOut(
        id=row.id,
        print_job_id=row.print_job_id,
        archive_id=None,
        model_name=None,
        grams=row.grams,
        source=row.source,
        created_at=row.created_at,
    )
