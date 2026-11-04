"""Per-plate geometry for UNSLICED Bambu/Orca project 3MFs (MakerWorld).

A project 3MF lays every plate out on one big virtual grid: the build items'
transforms are in that world space, so the merged mesh is a row of beds side
by side. ``Metadata/model_settings.config`` says which objects live on which
plate (``<plate>`` -> ``plater_id`` / ``plater_name`` / ``model_instance``
``object_id``). This module parses that mapping, loads each build item via
lib3mf (``meshload.load_3mf_build_items``) and moves every plate back onto a
single bed so the viewer/bed-fit can treat plates independently.

Grid (Bambu Studio ``PartPlateList::compute_origin``): plate ``i`` (0-based)
sits at column ``i % cols`` / row ``i // cols`` with ``cols = ceil(sqrt(n))``;
the stride is ``bed * (1 + 0.2)`` (``LOGICAL_PART_PLATE_GAP``) and rows go
toward -Y.
"""

from __future__ import annotations

import json
import logging
import math
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import trimesh

from app.pipeline import meshload, slicedmeta

logger = logging.getLogger(__name__)

MODEL_SETTINGS = "Metadata/model_settings.config"
PROJECT_SETTINGS = "Metadata/project_settings.config"
ROOT_MODEL = "3D/3dmodel.model"
DEFAULT_BED = (256.0, 256.0)
PLATE_GAP = 0.2
MAX_PLATE_GLBS = 16
_EDGE_TOL_MM = 1.0


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_unsliced_plates(zf: zipfile.ZipFile) -> list[dict]:
    """``[{index, name, object_ids, instance_ids, thumb_member}]`` in plate
    order, from ``model_settings.config``. ``[]`` when absent/unparseable."""
    data = slicedmeta.read_zip_member(zf, MODEL_SETTINGS)
    if data is None:
        return []
    try:
        root = ET.fromstring(data)  # noqa: S314 - same trust level as slicedmeta
    except ET.ParseError:
        return []
    names = set(zf.namelist())
    plates: list[dict] = []
    for position, plate_el in enumerate(root.findall("plate"), start=1):
        meta = slicedmeta._plate_metadata(plate_el)
        index = slicedmeta._parse_int(meta.get("plater_id")) or position
        object_ids: list[int] = []
        instance_ids: list[int] = []
        for inst in plate_el.findall("model_instance"):
            im = slicedmeta._plate_metadata(inst)
            oid = slicedmeta._parse_int(im.get("object_id"))
            if oid is None:
                continue
            object_ids.append(oid)
            instance_ids.append(slicedmeta._parse_int(im.get("instance_id")) or 0)
        thumb = f"Metadata/plate_{index}.png"
        if thumb not in names:
            declared = meta.get("thumbnail_file")
            thumb = declared if declared in names else None
        plates.append(
            {
                "index": index,
                "name": (meta.get("plater_name") or "").strip(),
                "object_ids": object_ids,
                "instance_ids": instance_ids,
                "thumb_member": thumb,
            }
        )
    plates.sort(key=lambda p: p["index"])
    return plates


def read_bed(zf: zipfile.ZipFile) -> tuple[float, float] | None:
    """Bed (x, y) mm from ``project_settings.config`` ``printable_area``."""
    from app.services.bed_fit import parse_printable_area

    data = slicedmeta.read_zip_member(zf, PROJECT_SETTINGS)
    if data is None:
        return None
    try:
        cfg = json.loads(data)
    except ValueError:
        return None
    return parse_printable_area(cfg) if isinstance(cfg, dict) else None


def _build_item_object_ids(zf: zipfile.ZipFile) -> list[int | None]:
    data = slicedmeta.read_zip_member(zf, ROOT_MODEL)
    if data is None:
        return []
    out: list[int | None] = []
    import io

    for _, el in ET.iterparse(io.BytesIO(data)):  # noqa: S314
        name = _local(el.tag)
        if name == "item":
            out.append(slicedmeta._parse_int(el.get("objectid")))
        elif name in ("vertices", "triangles", "object"):
            el.clear()
    return out


def plate_origin(index: int, n: int, bed: tuple[float, float]) -> tuple[float, float]:
    """Virtual-grid origin (bed corner) of 1-based plate ``index`` of ``n``."""
    cols = max(1, math.ceil(math.sqrt(n)))
    i = index - 1
    row, col = divmod(i, cols)
    return col * bed[0] * (1 + PLATE_GAP), -row * bed[1] * (1 + PLATE_GAP)


def _recentre(mesh: trimesh.Trimesh, index: int, n: int, bed: tuple[float, float] | None) -> None:
    """Move ``mesh`` (in place) onto a single bed with its corner at (0, 0)."""
    eff = bed or DEFAULT_BED
    if bed is not None:
        ox, oy = plate_origin(index, n, bed)
        lo, hi = mesh.bounds
        if (
            lo[0] - ox >= -_EDGE_TOL_MM
            and lo[1] - oy >= -_EDGE_TOL_MM
            and hi[0] - ox <= bed[0] + _EDGE_TOL_MM
            and hi[1] - oy <= bed[1] + _EDGE_TOL_MM
        ):
            mesh.apply_translation([-ox, -oy, 0.0])
            return
    lo, hi = mesh.bounds
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    mesh.apply_translation([eff[0] / 2 - cx, eff[1] / 2 - cy, 0.0])


def plate_meshes(
    path: Path, plates: list[dict], bed: tuple[float, float] | None = None
) -> dict[int, trimesh.Trimesh]:
    """``{plate index: mesh}`` recentred onto one bed. Plates with no
    geometry are omitted. Build items not claimed by any plate go on plate 1
    (logged)."""
    if not plates:
        return {}
    with zipfile.ZipFile(path) as zf:
        item_oids = _build_item_object_ids(zf)
    items = meshload.load_3mf_build_items(path)
    if len(items) != len(item_oids):
        logger.warning("plates3mf: %d lib3mf items vs %d xml items", len(items), len(item_oids))
        return {}

    by_object: dict[int, list[int]] = {}
    for pos, oid in enumerate(item_oids):
        if oid is not None:
            by_object.setdefault(oid, []).append(pos)

    assigned: dict[int, list[int]] = {p["index"]: [] for p in plates}
    claimed: set[int] = set()
    for plate in plates:
        for oid, inst in zip(plate["object_ids"], plate["instance_ids"], strict=False):
            slots = by_object.get(oid) or []
            if not slots:
                continue
            pos = slots[inst] if inst < len(slots) else slots[0]
            assigned[plate["index"]].append(pos)
            claimed.add(pos)
    first = plates[0]["index"]
    for pos in range(len(items)):
        if pos not in claimed:
            logger.info("plates3mf: build item %d on no plate; placing on plate %d", pos, first)
            assigned[first].append(pos)

    n = max(max(p["index"] for p in plates), len(plates))
    out: dict[int, trimesh.Trimesh] = {}
    for plate in plates:
        parts = [items[pos] for pos in assigned[plate["index"]] if items[pos] is not None]
        if not parts:
            continue
        merged = trimesh.util.concatenate(parts)
        _recentre(merged, plate["index"], n, bed)
        out[plate["index"]] = merged
    return out


def plate_extents(mesh: trimesh.Trimesh) -> list[float]:
    return [round(float(x), 2) for x in mesh.extents]
