"""Pre-flight bed-fit check: does a part's bounding box fit a printer bed?

Pure math (``check_fit``) plus the bed lookups shared with ``mesh_combine``
(``parse_printable_area``) and the slice guard. Rotation is 0/90 degrees only.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

FitStatus = Literal["fits", "rotated", "too_large", "unknown"]
Bed = tuple[tuple[float, float], float | None]


def parse_printable_area(data: dict) -> tuple[float, float] | None:
    """Bed (x, y) mm from a slicer printer profile's ``printable_area``
    (``["0x0", "180x0", "180x180", "0x180"]``), or None when absent."""
    area = data.get("printable_area") or data.get("bed_shape")
    if not isinstance(area, list | tuple) or not area:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for pt in area:
        m = re.fullmatch(r"\s*(-?[\d.]+)\s*[x,]\s*(-?[\d.]+)\s*", str(pt))
        if not m:
            return None
        xs.append(float(m.group(1)))
        ys.append(float(m.group(2)))
    x, y = max(xs) - min(xs), max(ys) - min(ys)
    return (x, y) if x > 0 and y > 0 else None


def parse_printable_height(data: dict) -> float | None:
    """Max build height mm (``printable_height``, else ``max_print_height``)."""
    for key in ("printable_height", "max_print_height"):
        try:
            v = float(data.get(key))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return None


class FitResult(BaseModel):
    status: FitStatus
    fits: bool
    rotated: bool = False  # fits only when turned 90 degrees
    overflow: dict[str, float] = {}  # axis -> mm over the usable bed
    required_bed: tuple[float, float] | None = None
    required_height: float | None = None
    bed: tuple[float, float] | None = None
    height: float | None = None


def unknown_fit() -> FitResult:
    return FitResult(status="unknown", fits=True)


def _overflow(x: float, y: float, z: float = 0.0) -> dict[str, float]:
    out = {"x": x, "y": y, "z": z}
    return {k: round(v, 2) for k, v in out.items() if v > 0}


def check_fit(
    dims_mm: tuple[float, float, float],
    bed: tuple[float, float],
    height: float | None = None,
    margin_mm: float = 1.0,
) -> FitResult:
    """``margin_mm`` is the total clearance kept across each bed axis."""
    dx, dy, dz = dims_mm
    ux, uy = bed[0] - margin_mm, bed[1] - margin_mm
    over_z = max(0.0, dz - height) if height is not None else 0.0
    direct = (max(0.0, dx - ux), max(0.0, dy - uy))
    turned = (max(0.0, dy - ux), max(0.0, dx - uy))
    flip = sum(turned) < sum(direct)
    best = turned if flip else direct
    w, d = (dy, dx) if flip else (dx, dy)
    common = {
        "required_bed": (round(w + margin_mm, 2), round(d + margin_mm, 2)),
        "required_height": round(dz, 2),
        "bed": bed,
        "height": height,
    }
    if over_z > 0 or sum(best) > 0:
        return FitResult(
            status="too_large", fits=False, overflow=_overflow(*best, over_z), **common
        )
    if flip:
        return FitResult(status="rotated", fits=True, rotated=True, **common)
    return FitResult(status="fits", fits=True, **common)


async def bed_from_profile(db: AsyncSession, profile_id: int) -> Bed | None:
    from app.models.enums import ProfileKind
    from app.models.slicing import SlicerProfile

    profile = await db.get(SlicerProfile, profile_id)
    if profile is None or profile.kind is not ProfileKind.PRINTER:
        return None
    bed = parse_printable_area(profile.data)
    return (bed, parse_printable_height(profile.data)) if bed else None


async def bed_for_printer(db: AsyncSession, printer_id: int) -> Bed | None:
    """Bed + height for a printer: the printer profile of a recipe targeting
    it, else the printer's own ``build_volume_mm``; None when neither."""
    from app.models.printing import Printer
    from app.models.slicing import PrintRecipe

    pid = (
        await db.execute(
            select(PrintRecipe.printer_profile_id)
            .where(PrintRecipe.target_printer_id == printer_id)
            .order_by(PrintRecipe.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if pid is not None and (found := await bed_from_profile(db, pid)):
        return found
    printer = await db.get(Printer, printer_id)
    vol = printer.build_volume_mm if printer else None
    if isinstance(vol, dict):
        try:
            x, y = float(vol["x"]), float(vol["y"])
        except (KeyError, TypeError, ValueError):
            return None
        z = vol.get("z")
        if x > 0 and y > 0:
            return (x, y), float(z) if isinstance(z, int | float) and z > 0 else None
    return None


async def fit_for_file(db: AsyncSession, blob_hash: str, bed: Bed | None) -> FitResult:
    """``unknown`` when there is no bed or no dims, or the blob is already
    sliced (its geometry is baked into the plate)."""
    from app.models.processing import BlobMeta

    if bed is None:
        return unknown_fit()
    meta = await db.get(BlobMeta, blob_hash)
    if meta is None or meta.print_time_s is not None or not meta.dims_mm or len(meta.dims_mm) < 3:
        return unknown_fit()
    d = meta.dims_mm
    return check_fit((d[0], d[1], d[2]), bed[0], bed[1])
