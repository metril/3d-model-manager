"""Pre-flight bed-fit check: does a part's bounding box fit a printer bed?

Pure math (``check_fit``) plus the bed lookups shared with ``mesh_combine``
(``parse_printable_area``) and the slice guard. Rotation is 0/90 degrees only.
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

import anyio
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
    dims: tuple[float, float, float] | None = None
    bed: tuple[float, float] | None = None
    height: float | None = None


def rotation_allowed(*options: dict | None) -> bool:
    """The slicer orients/arranges the part itself (recipe + overrides)."""
    merged: dict = {}
    for o in options:
        merged.update(o or {})
    return bool(merged.get("auto_orient") or merged.get("arrange"))


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
    allow_rotate: bool = True,
) -> FitResult:
    """``margin_mm`` is the total clearance kept across each bed axis.
    ``allow_rotate``: the slicer will orient/arrange the part itself; when
    False a part that only fits turned 90 degrees is ``too_large`` (with
    ``rotated`` set to say so)."""
    dx, dy, dz = dims_mm
    ux, uy = bed[0] - margin_mm, bed[1] - margin_mm
    over_z = max(0.0, dz - height) if height is not None else 0.0
    direct = (max(0.0, dx - ux), max(0.0, dy - uy))
    turned = (max(0.0, dy - ux), max(0.0, dx - uy))
    flip = sum(turned) < sum(direct)
    needs_turn = flip and sum(turned) == 0
    if flip and not allow_rotate:
        flip = False
    best = turned if flip else direct
    w, d = (dy, dx) if flip else (dx, dy)
    common = {
        "required_bed": (round(w + margin_mm, 2), round(d + margin_mm, 2)),
        "required_height": round(dz, 2),
        "dims": (dx, dy, dz),
        "bed": bed,
        "height": height,
    }
    if over_z > 0 or sum(best) > 0:
        return FitResult(
            status="too_large",
            fits=False,
            rotated=needs_turn and not allow_rotate,
            overflow=_overflow(*best, over_z),
            **common,
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


_PLATE_JSON = re.compile(r"^Metadata/plate_\d+\.json$")


def _count_plates(backend, storage_path: str) -> int:
    """Plates in a 3MF from its zip namelist (Bambu projects carry one
    ``Metadata/plate_<n>.json`` per plate; plain 3MFs count as 1)."""
    fd, tmp_name = tempfile.mkstemp(prefix="tdmm-platecount-", suffix=".3mf")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            for chunk in backend.read(storage_path):
                fh.write(chunk)
        with zipfile.ZipFile(tmp) as zf:
            n = sum(1 for name in zf.namelist() if _PLATE_JSON.match(name))
        return max(1, n)
    finally:
        tmp.unlink(missing_ok=True)


async def _ensure_plate_count(db: AsyncSession, meta, file, settings) -> int | None:
    """``meta.plate_count``, computed from the stored 3MF and persisted
    (flushed; best-effort) when it was never recorded."""
    if meta.plate_count is not None:
        return meta.plate_count
    if file is None or settings is None:
        return None
    try:
        from app.services.storage_backends import resolve_backend_for_file

        backend = await resolve_backend_for_file(db, settings, file)
        count = await anyio.to_thread.run_sync(_count_plates, backend, file.storage_path)
        meta.plate_count = count
        await db.flush()
        return count
    except Exception:
        return None


async def fit_for_file(
    db: AsyncSession,
    file,
    bed: Bed | None,
    allow_rotate: bool = False,
    settings=None,
) -> FitResult:
    """``unknown`` when there is no bed or no dims, the blob is already
    sliced, or the dims are not trustworthy: only STL/OBJ and single-plate
    3MFs are checked (a multi-plate 3MF's stored extent merges every plate,
    which sit side by side)."""
    from app.models.enums import BlobFormat
    from app.models.library import Blob
    from app.models.processing import BlobMeta

    blob_hash = file.blob_hash

    if bed is None:
        return unknown_fit()
    meta = await db.get(BlobMeta, blob_hash)
    if meta is None or meta.print_time_s is not None or not meta.dims_mm or len(meta.dims_mm) < 3:
        return unknown_fit()
    blob = await db.get(Blob, blob_hash)
    if blob is None:
        return unknown_fit()
    if blob.format is BlobFormat.THREEMF:
        if await _ensure_plate_count(db, meta, file, settings) != 1:
            return unknown_fit()
    elif blob.format not in (BlobFormat.STL, BlobFormat.OBJ):
        return unknown_fit()
    d = meta.dims_mm
    return check_fit((d[0], d[1], d[2]), bed[0], bed[1], allow_rotate=allow_rotate)


async def fit_for_selection(
    db: AsyncSession, file, printer_id: int | None, recipe_id: int | None, settings=None
) -> FitResult:
    """Chip lookup: the recipe's own printer profile (what the slice guards
    use) wins over the printer lookup, so chip and guard agree."""
    from app.models.slicing import PrintRecipe

    bed: Bed | None = None
    allow_rotate = False
    recipe = await db.get(PrintRecipe, recipe_id) if recipe_id is not None else None
    if recipe is not None:
        bed = await bed_from_profile(db, recipe.printer_profile_id)
        allow_rotate = rotation_allowed(recipe.options)
    elif printer_id is not None:
        bed = await bed_for_printer(db, printer_id)
    return await fit_for_file(db, file, bed, allow_rotate, settings) if bed else unknown_fit()


async def guard_recipe_fit(
    db: AsyncSession, recipe, file, options: dict | None = None, settings=None
) -> None:
    """422 ``does_not_fit`` when the part is too large for the recipe's printer."""
    from fastapi import HTTPException, status

    bed = await bed_from_profile(db, recipe.printer_profile_id)
    fit = await fit_for_file(db, file, bed, rotation_allowed(recipe.options, options), settings)
    if fit.status == "too_large":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "code": "does_not_fit",
                "message": "part does not fit the printer bed",
                **fit.model_dump(),
            },
        )


async def guard_files_fit(
    db: AsyncSession, recipe, files: list, options: dict | None = None, settings=None
) -> None:
    """Like ``guard_recipe_fit`` for several files: one 422 listing every
    offender as ``files: [{id, name, fit}]``."""
    from pathlib import PurePath

    from fastapi import HTTPException, status

    bed = await bed_from_profile(db, recipe.printer_profile_id)
    allow_rotate = rotation_allowed(recipe.options, options)
    bad = []
    for f in files:
        fit = await fit_for_file(db, f, bed, allow_rotate, settings)
        if fit.status == "too_large":
            bad.append({"id": f.id, "name": PurePath(f.rel_path).name, "fit": fit.model_dump()})
    if bad:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": "does_not_fit", "message": "part does not fit the printer bed", "files": bad},
        )
