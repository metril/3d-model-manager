"""T5: bulk spool ops, supplier assignment, CSV export/import and labels.

Mounted BEFORE ``spools.router`` so the literal paths (``/spools/export.csv``,
``/spools/bulk-update``...) win over ``/spools/{spool_id}``.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime

import redis.asyncio as aioredis
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuditLog
from app.models.inventory import Spool, SpoolSupplier, SpoolUsage, StorageLocation, Supplier
from app.schemas.housekeeping import (
    BulkIdsIn,
    BulkItem,
    BulkOut,
    BulkUpdateIn,
    ImportCommitIn,
    ImportPreviewOut,
    ImportResultOut,
    ImportRow,
    ImportSummary,
    LabelsIn,
    LabelsPreviewIn,
)
from app.schemas.inventory import SpoolSupplierIn, SpoolSupplierOut, SpoolUpdate
from app.services import spool_csv, spool_labels
from app.services.housekeeping import spool_suppliers
from app.services.server_config import get_server_config

router = APIRouter(prefix="/spools", tags=["spools"])
_write = Depends(require_perm("inventory:write"))
IMPORT_TTL_S = 15 * 60
_KEY = "spools:import:"


async def _load(db: AsyncSession, ids: list[int]) -> tuple[dict[int, Spool], list[int]]:
    uniq = list(dict.fromkeys(ids))
    rows = (await db.execute(select(Spool).where(Spool.id.in_(uniq)))).scalars().all()
    found = {s.id: s for s in rows}
    return found, [i for i in uniq if i not in found]


# ------------------------------------------------------------- suppliers


@router.put("/{spool_id}/suppliers", response_model=list[SpoolSupplierOut], dependencies=[_write])
async def replace_spool_suppliers(
    spool_id: int,
    links: list[SpoolSupplierIn],
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[SpoolSupplierOut]:
    if await db.get(Spool, spool_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "spool not found")
    sids = [x.supplier_id for x in links]
    if len(set(sids)) != len(sids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "duplicate supplier")
    if sum(x.is_purchase_source for x in links) > 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only one purchase source allowed")
    if sids:
        have = set((await db.execute(select(Supplier.id).where(Supplier.id.in_(sids)))).scalars())
        if missing := set(sids) - have:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"supplier(s) not found: {sorted(missing)}"
            )
    for old in (
        await db.execute(select(SpoolSupplier).where(SpoolSupplier.spool_id == spool_id))
    ).scalars():
        await db.delete(old)
    await db.flush()
    for x in links:
        db.add(SpoolSupplier(spool_id=spool_id, created_by_user_id=ctx.user.id, **x.model_dump()))
    await db.commit()
    return await spool_suppliers(db, spool_id)


# ------------------------------------------------------------------ bulk


@router.post("/bulk-update", response_model=BulkOut, dependencies=[_write])
async def bulk_update(payload: BulkUpdateIn, db: AsyncSession = Depends(get_db)) -> BulkOut:
    fields = payload.update.model_dump(exclude_unset=True)
    try:
        SpoolUpdate.model_validate(fields)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    archived = fields.pop("archived", None)
    if fields.get("location_id") is not None and (
        await db.get(StorageLocation, fields["location_id"]) is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "location not found")
    for k in ("material_type", "label_weight_g", "weight_used_g", "low_stock_g"):
        if k in fields and fields[k] is None:
            fields.pop(k)
    found, missing = await _load(db, payload.ids)
    results: list[BulkItem] = []
    for sid, spool in found.items():
        if "weight_used_g" in fields or "label_weight_g" in fields:
            used = fields.get("weight_used_g", spool.weight_used_g)
            label = fields.get("label_weight_g", spool.label_weight_g)
            if used > label:
                results.append(BulkItem(id=sid, ok=False, error="weight_used exceeds label_weight"))
                continue
        for k, v in fields.items():
            setattr(spool, k, v)
        if archived is True and spool.archived_at is None:
            spool.archived_at = datetime.now(UTC)
        elif archived is False:
            spool.archived_at = None
        results.append(BulkItem(id=sid, ok=True))
    await db.commit()
    return BulkOut(results=results, not_found=missing)


@router.post("/bulk-delete", response_model=BulkOut, dependencies=[_write])
async def bulk_delete(
    payload: BulkIdsIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> BulkOut:
    found, missing = await _load(db, payload.ids)
    used = set(
        (
            await db.execute(
                select(SpoolUsage.spool_id).where(SpoolUsage.spool_id.in_(list(found))).distinct()
            )
        ).scalars()
    )
    results: list[BulkItem] = []
    deleted: list[int] = []
    for sid, spool in found.items():
        try:
            require_owner_or_admin(ctx, spool.created_by_user_id)
        except HTTPException:
            results.append(
                BulkItem(id=sid, ok=False, error="only the creator or an admin can delete")
            )
            continue
        if sid in used:
            results.append(
                BulkItem(id=sid, ok=False, error="has usage history; archive it instead")
            )
            continue
        await db.delete(spool)
        deleted.append(sid)
        results.append(BulkItem(id=sid, ok=True))
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            action="spool.bulk_delete",
            payload={"requested": payload.ids, "deleted": deleted},
        )
    )
    await db.commit()
    return BulkOut(results=results, not_found=missing)


async def _bulk_archive(db: AsyncSession, ids: list[int], archive: bool) -> BulkOut:
    found, missing = await _load(db, ids)
    for spool in found.values():
        if archive:
            spool.archived_at = spool.archived_at or datetime.now(UTC)
        else:
            spool.archived_at = None
    await db.commit()
    return BulkOut(results=[BulkItem(id=i, ok=True) for i in found], not_found=missing)


@router.post("/bulk-archive", response_model=BulkOut, dependencies=[_write])
async def bulk_archive(payload: BulkIdsIn, db: AsyncSession = Depends(get_db)) -> BulkOut:
    return await _bulk_archive(db, payload.ids, True)


@router.post("/bulk-restore", response_model=BulkOut, dependencies=[_write])
async def bulk_restore(payload: BulkIdsIn, db: AsyncSession = Depends(get_db)) -> BulkOut:
    return await _bulk_archive(db, payload.ids, False)


# ------------------------------------------------------------------- CSV


@router.get("/export.csv")
async def export_csv(
    archived: bool | None = None,
    ids: str | None = Query(default=None, description="comma-separated spool ids"),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> Response:
    stmt = select(Spool)
    if archived is not None:
        stmt = stmt.where(
            Spool.archived_at.is_not(None) if archived else Spool.archived_at.is_(None)
        )
    if ids:
        try:
            stmt = stmt.where(Spool.id.in_([int(i) for i in ids.split(",") if i.strip()]))
        except ValueError as e:
            raise HTTPException(422, "ids must be integers") from e
    spools = list((await db.execute(stmt.order_by(Spool.id))).scalars().all())
    body = await spool_csv.export_csv(db, spools)
    return Response(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="spools.csv"'},
    )


def _preview(parsed: dict, token: str | None) -> ImportPreviewOut:
    return ImportPreviewOut(
        token=token,
        rows=[
            ImportRow(**{k: r[k] for k in ImportRow.model_fields if k in r}) for r in parsed["rows"]
        ],
        summary=ImportSummary(**spool_csv.summarize(parsed["rows"])),
        warnings=parsed["warnings"],
    )


async def _commit(db: AsyncSession, rows: list[dict], user_id: int | None) -> ImportResultOut:
    try:
        return ImportResultOut(**await spool_csv.apply(db, rows, user_id))
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "import conflicts with an existing spool (tray_uuid/spoolman_id)",
        ) from e


@router.post("/import", dependencies=[_write])
async def import_csv(
    file: UploadFile,
    dry_run: bool = True,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> ImportPreviewOut | ImportResultOut:
    raw = await file.read(spool_csv.MAX_CSV_IMPORT_BYTES + 1)
    if len(raw) > spool_csv.MAX_CSV_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "CSV too large")
    parsed = await spool_csv.parse(raw, db)
    if not dry_run:
        return await _commit(db, parsed["rows"], ctx.user.id)
    token = None
    if any(r["action"] in ("create", "update") for r in parsed["rows"]):
        token = secrets.token_urlsafe(18)
        client = aioredis.Redis.from_url(settings.redis_url)
        try:
            await client.set(
                _KEY + token,
                json.dumps({"user_id": ctx.user.id, "rows": parsed["rows"]}),
                ex=IMPORT_TTL_S,
            )
        finally:
            await client.aclose()
    return _preview(parsed, token)


@router.post("/import/commit", response_model=ImportResultOut, dependencies=[_write])
async def import_commit(
    payload: ImportCommitIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> ImportResultOut:
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        raw = await client.getdel(_KEY + payload.token)
    finally:
        await client.aclose()
    stash = json.loads(raw) if raw else None
    if stash is None or stash["user_id"] != ctx.user.id:
        raise HTTPException(status.HTTP_410_GONE, "import preview expired; upload the file again")
    return await _commit(db, stash["rows"], ctx.user.id)


# ---------------------------------------------------------------- labels


async def _label_items(
    db: AsyncSession, ids: list[int], request_base: str
) -> list[spool_labels.LabelData]:
    if not ids:
        raise HTTPException(422, "no spools selected")
    found, missing = await _load(db, ids)
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"spool(s) not found: {missing}")
    cfg = await get_server_config(db)
    base = (cfg.public_url or request_base).rstrip("/")
    return [
        spool_labels.LabelData(
            spool_id=i,
            material=found[i].material_type,
            brand=found[i].brand,
            color_name=found[i].color_name,
            color_hex=found[i].color_hex,
            location=found[i].location,
            weight_g=found[i].label_weight_g,
            url=f"{base}/scan/spool/{i}",
        )
        for i in dict.fromkeys(ids)
    ]


@router.post("/labels", dependencies=[_write])
async def labels(
    payload: LabelsIn, request: Request, db: AsyncSession = Depends(get_db)
) -> Response:
    items = await _label_items(db, payload.ids, str(request.base_url))
    pdf = await run_in_threadpool(
        spool_labels.render_pdf,
        items,
        payload.template,
        payload.page,
        frozenset(payload.fields),
        payload.offset,
    )
    body, media, name = await run_in_threadpool(
        spool_labels.encode_output, pdf, payload.format, "spool-labels"
    )
    return Response(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.post("/labels/preview", dependencies=[_write])
async def labels_preview(
    payload: LabelsPreviewIn, request: Request, db: AsyncSession = Depends(get_db)
) -> Response:
    items = await _label_items(db, payload.ids[:60], str(request.base_url))
    pdf = await run_in_threadpool(
        spool_labels.render_pdf,
        items,
        payload.template,
        payload.page,
        frozenset(payload.fields),
        payload.offset,
    )
    pngs = await run_in_threadpool(spool_labels.pdf_to_pngs, pdf, 2.0, True)
    return Response(pngs[0], media_type="image/png")
