"""CSV export/import for the spool inventory (T5). Columns follow upstream
bambuddy's ``spool_csv`` names where our model has the equivalent field.

``parse`` never writes; the dry-run result is stashed in Redis and ``apply``
persists it on commit.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import ColorCatalogEntry, Spool, SpoolSupplier, StorageLocation, Supplier
from app.services.housekeeping import get_or_create_location, name_key

CSV_COLUMNS = [
    "id",
    "material",
    "brand",
    "color_name",
    "rgba",
    "label_weight",
    "weight_used",
    "remaining",
    "cost_per_kg",
    "empty_weight",
    "low_stock_g",
    "last_used",
    "storage_location",
    "tray_uuid",
    "spoolman_id",
    "suppliers",
    "purchase_supplier",
]
MAX_CSV_IMPORT_BYTES = 5 * 1024 * 1024
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_INT = {"id", "spoolman_id"}
_FLOAT = {"label_weight", "weight_used", "cost_per_kg", "empty_weight", "low_stock_g"}
# csv column -> Spool attribute
_ATTR = {
    "material": "material_type",
    "brand": "brand",
    "color_name": "color_name",
    "label_weight": "label_weight_g",
    "weight_used": "weight_used_g",
    "cost_per_kg": "cost_per_kg",
    "empty_weight": "empty_weight_g",
    "low_stock_g": "low_stock_g",
    "tray_uuid": "tray_uuid",
    "spoolman_id": "spoolman_id",
}


def _sanitize(v: str) -> str:
    return "'" + v if v and v[0] in _FORMULA_PREFIXES else v


def _desanitize(v: str) -> str:
    if len(v) >= 2 and v[0] == "'" and v[1] in _FORMULA_PREFIXES:
        return v[1:]
    return v


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


async def export_csv(db: AsyncSession, spools: list[Spool]) -> bytes:
    ids = [s.id for s in spools]
    links: dict[int, list[tuple[str, bool]]] = {}
    if ids:
        for sid, name, purchase in (
            await db.execute(
                select(SpoolSupplier.spool_id, Supplier.name, SpoolSupplier.is_purchase_source)
                .join(Supplier, Supplier.id == SpoolSupplier.supplier_id)
                .where(SpoolSupplier.spool_id.in_(ids))
                .order_by(SpoolSupplier.id)
            )
        ).all():
            links.setdefault(sid, []).append((name, purchase))
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    for s in spools:
        sl = links.get(s.id, [])
        color = (s.color_hex or "").lstrip("#")
        vals: dict[str, Any] = {
            "id": s.id,
            "rgba": (color.lower() + "ff") if len(color) == 6 else color.lower(),
            "remaining": max(0, round(s.label_weight_g - s.weight_used_g)),
            "last_used": s.last_used_at,
            "storage_location": s.location,
            "suppliers": "; ".join(n for n, _ in sl),
            "purchase_supplier": next((n for n, p in sl if p), ""),
        }
        row = []
        for col in CSV_COLUMNS:
            v = vals[col] if col in vals else getattr(s, _ATTR[col])
            row.append(_sanitize(_fmt(v)))
        w.writerow(row)
    return out.getvalue().encode("utf-8")


def _norm_header(h: str) -> str:
    return h.strip().lower().replace(" ", "_").replace("-", "_")


def _rgba_to_hex(v: str) -> str | None:
    raw = v.strip().lstrip("#")
    if len(raw) not in (6, 8):
        return None
    try:
        int(raw, 16)
    except ValueError:
        return None
    return "#" + raw[:6].upper()


def _parse_dt(v: str) -> datetime | None:
    raw = v.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


async def parse(raw: bytes, db: AsyncSession) -> dict:
    """Return ``{rows:[...], warnings:[...]}``; each row carries ``data`` (spool
    attribute dict, JSON-safe), ``location`` name, ``suppliers`` links."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"rows": [], "warnings": ["file is not valid UTF-8 text"]}
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return {"rows": [], "warnings": ["CSV is empty"]}
    norm = [_norm_header(h) for h in header]
    warnings: list[str] = []
    unknown = [h for h in norm if h and h not in CSV_COLUMNS]
    if unknown:
        warnings.append(f"ignoring unknown columns: {', '.join(unknown)}")
    idx: dict[str, int] = {}
    for i, h in enumerate(norm):
        if h in CSV_COLUMNS and h not in idx:
            idx[h] = i
    if "material" not in idx and "id" not in idx:
        return {"rows": [], "warnings": warnings + ["header needs a 'material' or 'id' column"]}

    spools = (await db.execute(select(Spool))).scalars().all()
    by_id = {s.id: s for s in spools}
    by_tray = {s.tray_uuid: s for s in spools if s.tray_uuid}
    by_sm = {s.spoolman_id: s for s in spools if s.spoolman_id is not None}
    suppliers = {k: i for i, k in (await db.execute(select(Supplier.id, Supplier.name_key))).all()}
    loc_keys = set((await db.execute(select(StorageLocation.name_key))).scalars().all())
    catalog = (await db.execute(select(ColorCatalogEntry))).scalars().all()
    unknown_sup: set[str] = set()
    seen_ids: set[int] = set()

    rows: list[dict] = []
    prev_line = reader.line_num
    for raw_row in reader:
        line = prev_line + 1
        prev_line = reader.line_num

        def cell(f: str, _r: list[str] = raw_row) -> str:
            i = idx.get(f)
            return _desanitize(_r[i].strip()) if i is not None and i < len(_r) else ""

        row: dict[str, Any] = {
            "line": line,
            "action": "create",
            "spool_id": None,
            "data": {},
            "location": None,
            "suppliers": None,
            "errors": [],
            "warnings": [],
            "material": cell("material") or None,
            "brand": cell("brand") or None,
            "color_name": cell("color_name") or None,
        }
        rows.append(row)
        if not any(c.strip() for c in raw_row):
            row["action"] = "skipped"
            continue
        errs: list[str] = row["errors"]
        data: dict[str, Any] = row["data"]

        for col in _INT | _FLOAT:
            v = cell(col)
            if not v:
                continue
            try:
                parsed = int(v) if col in _INT else float(v)
            except ValueError:
                errs.append(f"{col} must be a number (got '{v}')")
                continue
            if col in _FLOAT and parsed < 0:
                errs.append(f"{col} must be >= 0")
            if col in _ATTR:
                data[_ATTR[col]] = parsed
        for col in ("material", "brand", "color_name", "tray_uuid"):
            if cell(col):
                data[_ATTR[col]] = cell(col)

        # Match an existing spool.
        match: Spool | None = None
        sid = None
        if cell("id") and not errs:
            try:
                sid = int(cell("id"))
            except ValueError:
                sid = None
            match = by_id.get(sid) if sid is not None else None
            if match is None:
                errs.append(f"spool id {cell('id')} not found")
        if match is None and not errs:
            if data.get("tray_uuid") in by_tray:
                match = by_tray[data["tray_uuid"]]
            elif data.get("spoolman_id") in by_sm:
                match = by_sm[data["spoolman_id"]]
        if match is not None:
            row["action"] = "update"
            row["spool_id"] = match.id
            if match.id in seen_ids:
                errs.append(f"spool {match.id} appears more than once")
            seen_ids.add(match.id)
        elif not data.get("material_type") and not errs:
            errs.append("material is required")

        label = data.get("label_weight_g", match.label_weight_g if match else 1000.0)
        used = data.get("weight_used_g", match.weight_used_g if match else 0.0)
        if "weight_used_g" in data and used > label:
            errs.append(f"weight_used ({used:g}) exceeds label_weight ({label:g})")

        if cell("last_used"):
            d = _parse_dt(cell("last_used"))
            if d is None:
                errs.append(f"last_used must be ISO date/time (got '{cell('last_used')}')")
            else:
                data["last_used_at"] = d.isoformat()

        if cell("rgba"):
            h = _rgba_to_hex(cell("rgba"))
            if h is None:
                errs.append(f"rgba must be 6/8-char hex (got '{cell('rgba')}')")
            else:
                data["color_hex"] = h
        elif not errs and match is None and data.get("brand") and data.get("color_name"):
            b, n, m = (
                data["brand"].lower(),
                data["color_name"].lower(),
                data["material_type"].lower(),
            )
            cands = [
                e for e in catalog if e.manufacturer.lower() == b and e.color_name.lower() == n
            ]
            hit = next((e for e in cands if (e.material or "").lower() == m), None) or (
                cands[0] if cands else None
            )
            if hit is not None and len(hit.hex) in (7, 9):
                data["color_hex"] = hit.hex[:7].upper()
                row["warnings"].append("colour resolved from the catalog")

        loc = cell("storage_location")
        if loc:
            row["location"] = loc
            if name_key(loc) not in loc_keys:
                row["warnings"].append(f"location '{loc}' will be created")

        if "suppliers" in idx or "purchase_supplier" in idx:
            names = [n.strip() for n in cell("suppliers").split(";") if n.strip()]
            purchase = cell("purchase_supplier")
            if purchase and name_key(purchase) not in {name_key(n) for n in names}:
                names.append(purchase)
            links: list[dict] = []
            for n in names:
                sup = suppliers.get(name_key(n))
                if sup is None:
                    row["warnings"].append(f"unknown supplier '{n}' - assignment dropped")
                    if name_key(n) not in unknown_sup:
                        unknown_sup.add(name_key(n))
                        warnings.append(f"unknown supplier '{n}' - assignments dropped")
                elif sup not in [x["supplier_id"] for x in links]:
                    links.append(
                        {
                            "supplier_id": sup,
                            "is_purchase_source": bool(purchase)
                            and name_key(n) == name_key(purchase),
                        }
                    )
            row["suppliers"] = links

        if errs:
            row["action"] = "error"
    return {"rows": rows, "warnings": warnings}


def summarize(rows: list[dict]) -> dict[str, int]:
    s = {"create": 0, "update": 0, "error": 0, "skipped": 0}
    for r in rows:
        s[r["action"]] += 1
    return s


async def apply(db: AsyncSession, rows: list[dict], user_id: int | None) -> dict[str, int]:
    created = updated = errors = 0
    for r in rows:
        if r["action"] in ("error", "skipped"):
            errors += r["action"] == "error"
            continue
        data = dict(r["data"])
        if "last_used_at" in data:
            data["last_used_at"] = datetime.fromisoformat(data["last_used_at"])
        if r["location"]:
            data["location_id"] = (await get_or_create_location(db, r["location"], user_id)).id
        if r["action"] == "update":
            spool = await db.get(Spool, r["spool_id"])
            if spool is None:
                errors += 1
                continue
            for k, v in data.items():
                setattr(spool, k, v)
            updated += 1
        else:
            spool = Spool(**data, created_by_user_id=user_id)
            db.add(spool)
            await db.flush()
            created += 1
        if r["suppliers"] is not None:
            for old in (
                await db.execute(select(SpoolSupplier).where(SpoolSupplier.spool_id == spool.id))
            ).scalars():
                await db.delete(old)
            await db.flush()
            for link in r["suppliers"]:
                db.add(SpoolSupplier(spool_id=spool.id, created_by_user_id=user_id, **link))
    await db.commit()
    return {"created": created, "updated": updated, "errors": errors}
