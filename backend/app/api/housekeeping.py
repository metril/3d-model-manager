"""T5 spool housekeeping: suppliers, storage locations (+ HA sensors) and the
colour catalog. Reads: session. Writes: ``inventory:write``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_perm_on_write, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuditLog
from app.models.inventory import (
    ColorCatalogEntry,
    LocationHaSensor,
    Spool,
    SpoolSupplier,
    StorageLocation,
    Supplier,
)
from app.schemas.housekeeping import (
    CatalogIn,
    CatalogNearestOut,
    CatalogOut,
    CatalogPatch,
    HaEntityOut,
    LocationIn,
    LocationLabelsIn,
    LocationOut,
    LocationPatch,
    ReadingOut,
    SensorIn,
    SensorOut,
    SensorPatch,
    SupplierIn,
    SupplierOut,
    SupplierPatch,
)
from app.services import spool_labels
from app.services.ams_match import delta_e_ok
from app.services.ha_client import HaClient, HaUnreachable
from app.services.ha_config import get_ha_config
from app.services.housekeeping import load_default_catalog, name_key
from app.services.library import _escape_like
from app.services.server_config import get_server_config

_write = Depends(require_perm_on_write("inventory:write"))

suppliers_router = APIRouter(prefix="/suppliers", tags=["suppliers"], dependencies=[_write])
locations_router = APIRouter(
    prefix="/storage-locations", tags=["storage-locations"], dependencies=[_write]
)
catalog_router = APIRouter(prefix="/color-catalog", tags=["color-catalog"], dependencies=[_write])


def _404(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


# --------------------------------------------------------------- suppliers


async def _supplier_out(db: AsyncSession, s: Supplier) -> SupplierOut:
    n = await db.scalar(
        select(func.count()).select_from(SpoolSupplier).where(SpoolSupplier.supplier_id == s.id)
    )
    return SupplierOut(
        **{c: getattr(s, c) for c in SupplierOut.model_fields if c != "spool_count"},
        spool_count=n or 0,
    )


@suppliers_router.get("", response_model=list[SupplierOut])
async def list_suppliers(
    db: AsyncSession = Depends(get_db), ctx: AuthContext = Depends(require_session)
) -> list[SupplierOut]:
    counts = dict(
        (
            await db.execute(
                select(SpoolSupplier.supplier_id, func.count()).group_by(SpoolSupplier.supplier_id)
            )
        ).all()
    )
    rows = (await db.execute(select(Supplier).order_by(Supplier.name_key))).scalars().all()
    return [
        SupplierOut(
            **{c: getattr(s, c) for c in SupplierOut.model_fields if c != "spool_count"},
            spool_count=counts.get(s.id, 0),
        )
        for s in rows
    ]


@suppliers_router.post("", status_code=status.HTTP_201_CREATED, response_model=SupplierOut)
async def create_supplier(
    payload: SupplierIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> SupplierOut:
    s = Supplier(
        **payload.model_dump(), name_key=name_key(payload.name), created_by_user_id=ctx.user.id
    )
    db.add(s)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "supplier name already exists") from e
    await db.refresh(s)
    return await _supplier_out(db, s)


@suppliers_router.patch("/{supplier_id}", response_model=SupplierOut)
async def update_supplier(
    supplier_id: int, payload: SupplierPatch, db: AsyncSession = Depends(get_db)
) -> SupplierOut:
    s = await db.get(Supplier, supplier_id)
    if s is None:
        raise _404("supplier")
    data = payload.model_dump(exclude_unset=True)
    if data.get("name") is None:
        data.pop("name", None)
    for k, v in data.items():
        setattr(s, k, v)
    if "name" in data:
        s.name_key = name_key(s.name)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "supplier name already exists") from e
    await db.refresh(s)
    return await _supplier_out(db, s)


@suppliers_router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier(supplier_id: int, db: AsyncSession = Depends(get_db)) -> None:
    s = await db.get(Supplier, supplier_id)
    if s is None:
        raise _404("supplier")
    await db.delete(s)
    await db.commit()


# ------------------------------------------------------- storage locations


def _sensor_out(s: LocationHaSensor) -> SensorOut:
    d = {c: getattr(s, c) for c in SensorOut.model_fields}
    for k in ("alert_above", "alert_below"):
        d[k] = float(d[k]) if d[k] is not None else None
    return SensorOut(**d)


async def _location_outs(db: AsyncSession, locs: list[StorageLocation]) -> list[LocationOut]:
    ids = [loc.id for loc in locs]
    counts = dict(
        (
            await db.execute(
                select(Spool.location_id, func.count())
                .where(Spool.location_id.in_(ids), Spool.archived_at.is_(None))
                .group_by(Spool.location_id)
            )
        ).all()
    )
    sensors: dict[int, list[SensorOut]] = {}
    for s in (
        (
            await db.execute(
                select(LocationHaSensor)
                .where(LocationHaSensor.location_id.in_(ids))
                .order_by(LocationHaSensor.sort_order, LocationHaSensor.id)
            )
        )
        .scalars()
        .all()
    ):
        sensors.setdefault(s.location_id, []).append(_sensor_out(s))
    return [
        LocationOut(
            id=loc.id,
            name=loc.name,
            identifier=loc.identifier,
            created_at=loc.created_at,
            spool_count=counts.get(loc.id, 0),
            sensors=sensors.get(loc.id, []),
        )
        for loc in locs
    ]


@locations_router.get("", response_model=list[LocationOut])
async def list_locations(
    db: AsyncSession = Depends(get_db), ctx: AuthContext = Depends(require_session)
) -> list[LocationOut]:
    locs = (
        (await db.execute(select(StorageLocation).order_by(StorageLocation.name_key)))
        .scalars()
        .all()
    )
    return await _location_outs(db, list(locs))


@locations_router.post("", status_code=status.HTTP_201_CREATED, response_model=LocationOut)
async def create_location(
    payload: LocationIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> LocationOut:
    loc = StorageLocation(
        name=payload.name,
        name_key=name_key(payload.name),
        identifier=payload.identifier,
        created_by_user_id=ctx.user.id,
    )
    db.add(loc)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "location name already exists") from e
    await db.refresh(loc)
    return (await _location_outs(db, [loc]))[0]


@locations_router.get(
    "/sensors/entities",
    response_model=list[HaEntityOut],
    dependencies=[Depends(require_perm("settings:write"))],
)
async def ha_entities(
    q: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> list[HaEntityOut]:
    cfg = await get_ha_config(db, settings)
    if not cfg.ha_url or not cfg.ha_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "home assistant not configured")

    def _fetch() -> list[dict]:
        client = HaClient(cfg.ha_url, cfg.ha_token)
        try:
            return client.states()
        finally:
            client.close()

    try:
        states = await run_in_threadpool(_fetch)
    except HaUnreachable as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "could not reach home assistant"
        ) from e
    needle = q.lower() if q else None
    out: list[HaEntityOut] = []
    for st in states:
        eid = st.get("entity_id", "")
        attrs = st.get("attributes") or {}
        if not eid.startswith(("sensor.", "binary_sensor.")):
            continue
        fname = attrs.get("friendly_name")
        if needle and needle not in eid.lower() and needle not in str(fname or "").lower():
            continue
        out.append(
            HaEntityOut(
                entity_id=eid,
                friendly_name=fname,
                state=st.get("state"),
                unit=attrs.get("unit_of_measurement"),
                device_class=attrs.get("device_class"),
            )
        )
    return sorted(out, key=lambda e: e.entity_id)


@locations_router.patch("/sensors/{sensor_id}", response_model=SensorOut)
async def update_sensor(
    sensor_id: int, payload: SensorPatch, db: AsyncSession = Depends(get_db)
) -> SensorOut:
    s = await db.get(LocationHaSensor, sensor_id)
    if s is None:
        raise _404("sensor")
    data = payload.model_dump(exclude_unset=True)
    old_entity = s.entity_id
    for k in ("name", "entity_id", "kind", "notify_on_alert", "show_on_card", "sort_order"):
        if k in data and data[k] is None:
            data.pop(k)
    new_entity = data.get("entity_id")
    for k, v in data.items():
        setattr(s, k, v)
    if new_entity is not None and new_entity != old_entity:
        s.alerting = False
        s.last_state = s.last_changed_at = s.unit = s.device_class = None
        if "unit" in data:
            s.unit = data["unit"]
        if "device_class" in data:
            s.device_class = data["device_class"]
    if {"alert_above", "alert_below"} & data.keys():
        s.alerting = False  # re-evaluated on the next poll
    await db.commit()
    await db.refresh(s)
    return _sensor_out(s)


@locations_router.delete("/sensors/{sensor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sensor(sensor_id: int, db: AsyncSession = Depends(get_db)) -> None:
    s = await db.get(LocationHaSensor, sensor_id)
    if s is None:
        raise _404("sensor")
    await db.delete(s)
    await db.commit()


@locations_router.post("/labels")
async def location_labels(
    payload: LocationLabelsIn, request: Request, db: AsyncSession = Depends(get_db)
) -> Response:
    """Location labels whose QR encodes ``/scan/location/<id>`` (scan-to-assign)."""
    ids = list(dict.fromkeys(payload.ids))
    locs = {
        loc.id: loc
        for loc in (
            await db.execute(select(StorageLocation).where(StorageLocation.id.in_(ids)))
        ).scalars()
    }
    missing = [i for i in ids if i not in locs]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"location(s) not found: {missing}")
    cfg = await get_server_config(db)
    base = (cfg.public_url or str(request.base_url)).rstrip("/")
    items = [
        spool_labels.LabelData(
            spool_id=i,
            material="",
            brand=None,
            color_name=None,
            color_hex=None,
            location=locs[i].identifier,
            url=f"{base}/scan/location/{i}",
            kind="location",
            title=locs[i].name,
        )
        for i in ids
    ]
    pdf = await run_in_threadpool(
        spool_labels.render_pdf, items, payload.template, payload.page, None, payload.offset
    )
    body, media, name = await run_in_threadpool(
        spool_labels.encode_output, pdf, payload.format, "location-labels"
    )
    return Response(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@locations_router.patch("/{location_id}", response_model=LocationOut)
async def update_location(
    location_id: int, payload: LocationPatch, db: AsyncSession = Depends(get_db)
) -> LocationOut:
    loc = await db.get(StorageLocation, location_id)
    if loc is None:
        raise _404("location")
    data = payload.model_dump(exclude_unset=True)
    if data.get("name") is not None:
        loc.name = data["name"]
        loc.name_key = name_key(data["name"])
    if "identifier" in data:
        loc.identifier = data["identifier"]
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "location name already exists") from e
    await db.refresh(loc)
    return (await _location_outs(db, [loc]))[0]


@locations_router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(location_id: int, db: AsyncSession = Depends(get_db)) -> None:
    loc = await db.get(StorageLocation, location_id)
    if loc is None:
        raise _404("location")
    await db.delete(loc)
    await db.commit()


@locations_router.get("/{location_id}/sensors", response_model=list[SensorOut])
async def list_sensors(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[SensorOut]:
    if await db.get(StorageLocation, location_id) is None:
        raise _404("location")
    rows = (
        (
            await db.execute(
                select(LocationHaSensor)
                .where(LocationHaSensor.location_id == location_id)
                .order_by(LocationHaSensor.sort_order, LocationHaSensor.id)
            )
        )
        .scalars()
        .all()
    )
    return [_sensor_out(s) for s in rows]


@locations_router.post(
    "/{location_id}/sensors", status_code=status.HTTP_201_CREATED, response_model=SensorOut
)
async def create_sensor(
    location_id: int,
    payload: SensorIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> SensorOut:
    if await db.get(StorageLocation, location_id) is None:
        raise _404("location")
    s = LocationHaSensor(
        **payload.model_dump(), location_id=location_id, created_by_user_id=ctx.user.id
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _sensor_out(s)


@locations_router.get("/{location_id}/readings", response_model=list[ReadingOut])
async def location_readings(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[ReadingOut]:
    if await db.get(StorageLocation, location_id) is None:
        raise _404("location")
    rows = (
        (
            await db.execute(
                select(LocationHaSensor)
                .where(LocationHaSensor.location_id == location_id)
                .order_by(LocationHaSensor.sort_order, LocationHaSensor.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        ReadingOut(
            sensor_id=s.id,
            name=s.name,
            kind=s.kind,
            entity_id=s.entity_id,
            state=s.last_state,
            unit=s.unit,
            last_changed_at=s.last_changed_at,
            alerting=s.alerting,
        )
        for s in rows
    ]


# ----------------------------------------------------------- colour catalog


def _cat_out(e: ColorCatalogEntry) -> CatalogOut:
    return CatalogOut(**{c: getattr(e, c) for c in CatalogOut.model_fields})


@catalog_router.get("", response_model=list[CatalogOut])
async def list_catalog(
    manufacturer: str | None = None,
    material: str | None = None,
    q: str | None = None,
    limit: int = Query(default=2000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[CatalogOut]:
    stmt = select(ColorCatalogEntry)
    if manufacturer:
        stmt = stmt.where(func.lower(ColorCatalogEntry.manufacturer) == manufacturer.lower())
    if material:
        stmt = stmt.where(func.lower(ColorCatalogEntry.material) == material.lower())
    if q:
        like = f"%{_escape_like(q)}%"
        stmt = stmt.where(
            or_(
                ColorCatalogEntry.color_name.ilike(like, escape="\\"),
                ColorCatalogEntry.manufacturer.ilike(like, escape="\\"),
                ColorCatalogEntry.material.ilike(like, escape="\\"),
            )
        )
    rows = await db.execute(
        stmt.order_by(
            ColorCatalogEntry.manufacturer, ColorCatalogEntry.material, ColorCatalogEntry.color_name
        ).limit(limit)
    )
    return [_cat_out(e) for e in rows.scalars().all()]


@catalog_router.post("", status_code=status.HTTP_201_CREATED, response_model=CatalogOut)
async def create_catalog(
    payload: CatalogIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> CatalogOut:
    e = ColorCatalogEntry(**payload.model_dump(), is_default=False, created_by_user_id=ctx.user.id)
    db.add(e)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "catalog entry already exists") from exc
    await db.refresh(e)
    return _cat_out(e)


@catalog_router.get("/lookup", response_model=CatalogOut)
async def lookup_catalog(
    manufacturer: str,
    name: str,
    material: str | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> CatalogOut:
    rows = (
        (
            await db.execute(
                select(ColorCatalogEntry)
                .where(
                    func.lower(ColorCatalogEntry.manufacturer) == manufacturer.strip().lower(),
                    func.lower(ColorCatalogEntry.color_name) == name.strip().lower(),
                )
                .order_by(ColorCatalogEntry.id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise _404("catalog entry")
    if material:
        for e in rows:
            if (e.material or "").lower() == material.strip().lower():
                return _cat_out(e)
    return _cat_out(rows[0])


@catalog_router.get("/nearest", response_model=CatalogNearestOut)
async def nearest_catalog(
    hex: str,
    manufacturer: str | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> CatalogNearestOut:
    try:
        delta_e_ok(hex, hex)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "hex must be #RRGGBB") from e
    stmt = select(ColorCatalogEntry)
    if manufacturer:
        stmt = stmt.where(func.lower(ColorCatalogEntry.manufacturer) == manufacturer.lower())
    best: tuple[float, ColorCatalogEntry] | None = None
    for e in (await db.execute(stmt)).scalars().all():
        try:
            d = delta_e_ok(hex, e.hex)
        except ValueError:
            continue
        if best is None or d < best[0]:
            best = (d, e)
    if best is None:
        raise _404("catalog entry")
    return CatalogNearestOut(**_cat_out(best[1]).model_dump(), delta_e=round(best[0], 4))


@catalog_router.get("/search", response_model=list[CatalogOut])
async def search_catalog(
    q: str = Query(min_length=1),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[CatalogOut]:
    like = f"%{q}%"
    rows = await db.execute(
        select(ColorCatalogEntry)
        .where(
            or_(
                ColorCatalogEntry.color_name.ilike(like),
                ColorCatalogEntry.manufacturer.ilike(like),
            )
        )
        .order_by(ColorCatalogEntry.manufacturer, ColorCatalogEntry.color_name)
        .limit(50)
    )
    return [_cat_out(e) for e in rows.scalars().all()]


@catalog_router.get("/by-material/{material}", response_model=list[CatalogOut])
async def catalog_by_material(
    material: str,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[CatalogOut]:
    rows = await db.execute(
        select(ColorCatalogEntry)
        .where(func.lower(ColorCatalogEntry.material) == material.strip().lower())
        .order_by(ColorCatalogEntry.manufacturer, ColorCatalogEntry.color_name)
    )
    return [_cat_out(e) for e in rows.scalars().all()]


@catalog_router.post("/reset", dependencies=[Depends(require_perm("admin"))])
async def reset_catalog(
    db: AsyncSession = Depends(get_db), ctx: AuthContext = Depends(require_session)
) -> dict[str, int]:
    """Replace every default entry with the shipped list; custom rows stay
    (a custom row colliding with a default's key wins and the default is skipped)."""
    defaults = load_default_catalog()
    await db.execute(delete(ColorCatalogEntry).where(ColorCatalogEntry.is_default.is_(True)))
    taken = {
        (m, c, mat)
        for m, c, mat in (
            await db.execute(
                select(
                    ColorCatalogEntry.manufacturer,
                    ColorCatalogEntry.color_name,
                    ColorCatalogEntry.material,
                )
            )
        ).all()
    }
    n = 0
    for d in defaults:
        if (d["manufacturer"], d["color_name"], d["material"]) in taken:
            continue
        db.add(ColorCatalogEntry(**d, is_default=True))
        n += 1
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            action="color_catalog.reset",
            payload={"restored": n},
        )
    )
    await db.commit()
    return {"restored": n}


@catalog_router.patch("/{entry_id}", response_model=CatalogOut)
async def update_catalog(
    entry_id: int, payload: CatalogPatch, db: AsyncSession = Depends(get_db)
) -> CatalogOut:
    e = await db.get(ColorCatalogEntry, entry_id)
    if e is None:
        raise _404("catalog entry")
    data = payload.model_dump(exclude_unset=True)
    for k in ("manufacturer", "color_name", "hex"):
        if k in data and data[k] is None:
            data.pop(k)
    for k, v in data.items():
        setattr(e, k, v)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "catalog entry already exists") from exc
    await db.refresh(e)
    return _cat_out(e)


@catalog_router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_catalog(entry_id: int, db: AsyncSession = Depends(get_db)) -> None:
    e = await db.get(ColorCatalogEntry, entry_id)
    if e is None:
        raise _404("catalog entry")
    await db.delete(e)
    await db.commit()
