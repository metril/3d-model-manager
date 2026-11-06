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

import html
import json
import logging
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import trimesh

from app.pipeline import meshload, slicedmeta, threemf_colour

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


def plate_filament_info(zf: zipfile.ZipFile, plates: list[dict]) -> dict[int, dict]:
    """``{plate index: {"filaments": [...], "colour_changes": [...]}}``; empty
    lists when the file has no Bambu/Orca filament data."""
    info = threemf_colour.read_colour_info(zf)
    return {
        p["index"]: {
            "filaments": info.plate_filaments(p["object_ids"]) if info else [],
            "colour_changes": info.plate_colour_changes(p["index"]) if info else [],
        }
        for p in plates
    }


_TAG_RE = re.compile(r"<[^>]*>")


def clean_meta_text(text: str | None) -> str | None:
    """Decode (possibly double) entity-escaped HTML to plain text."""
    if not text:
        return None
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = _TAG_RE.sub(" ", text)
    return " ".join(text.split()) or None


def read_3mf_profile_meta(zf: zipfile.ZipFile) -> dict | None:
    """MakerWorld provenance from ``3D/3dmodel.model`` ``<metadata>``, in the
    shape the MakerWorld importer stores in ``files.source_meta``; ``None``
    when the file carries no profile/design id."""
    data = slicedmeta.read_zip_member(zf, ROOT_MODEL)
    if data is None:
        return None
    try:
        root = ET.fromstring(data)  # noqa: S314 - same trust level as slicedmeta
    except ET.ParseError:
        return None
    md = {
        el.get("name"): (el.text or "").strip()
        for el in root.iter()
        if _local(el.tag) == "metadata" and el.get("name")
    }
    profile_id = slicedmeta._parse_int(md.get("DesignProfileId") or md.get("ProfileId"))
    design_id = md.get("DesignModelId") or None
    if profile_id is None and not design_id:
        return None
    out = {
        "profile_id": profile_id,
        "title": clean_meta_text(md.get("ProfileTitle") or md.get("Title")),
        "design_id": design_id,
        "design_title": clean_meta_text(md.get("Title")),
        "description": clean_meta_text(md.get("ProfileDescription")),
        "author": md.get("ProfileUserName") or md.get("Designer") or None,
        "origin": "3mf",
    }
    return {k: v for k, v in out.items() if v is not None}


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


def _assign_items(
    items: list[trimesh.Trimesh | None],
    item_oids: list[int | None],
    plates: list[dict],
    bed: tuple[float, float] | None,
) -> dict[int, list[int]]:
    """``{plate index: [build item positions]}``. Items not claimed by any
    plate go on the plate whose grid cell holds them (logged)."""
    if not plates:
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
    n = max(max(p["index"] for p in plates), len(plates))
    eff = bed or DEFAULT_BED
    cols = max(1, math.ceil(math.sqrt(n)))
    for pos in range(len(items)):
        if pos in claimed or items[pos] is None:
            continue
        lo, hi = items[pos].bounds
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        col = round((cx - eff[0] / 2) / (eff[0] * (1 + PLATE_GAP)))
        row = round(-(cy - eff[1] / 2) / (eff[1] * (1 + PLATE_GAP)))
        target = row * cols + col + 1
        if col < 0 or col >= cols or row < 0 or target not in assigned:
            logger.info("plates3mf: build item %d on no plate and no grid cell; dropped", pos)
            continue
        logger.info("plates3mf: build item %d on no plate; placing on plate %d", pos, target)
        assigned[target].append(pos)
    return assigned


def _load_items(
    path: Path, plates: list[dict], bed: tuple[float, float] | None, *, colour: bool = True
) -> tuple[list[trimesh.Trimesh | None], dict[int, list[int]], list[int | None]] | None:
    """Build items (painted by filament colour when the file is a
    multi-filament Bambu/Orca project) plus plate assignment. ``None`` when
    lib3mf and the XML disagree about the item count."""
    with zipfile.ZipFile(path) as zf:
        item_oids = _build_item_object_ids(zf)
        info = threemf_colour.read_colour_info(zf) if colour else None
    if info is not None and not info.is_multicolour:
        info = None
    pieces = meshload.load_3mf_build_pieces(path)
    if len(pieces) != len(item_oids):
        logger.warning("plates3mf: %d lib3mf items vs %d xml items", len(pieces), len(item_oids))
        return None
    plain = [trimesh.util.concatenate(p) if p else None for p in pieces]
    assigned = _assign_items(plain, item_oids, plates, bed)
    if info is None:
        return plain, assigned, item_oids
    plate_of = {pos: idx for idx, poss in assigned.items() for pos in poss}
    items: list[trimesh.Trimesh | None] = []
    for pos, parts in enumerate(pieces):
        oid = item_oids[pos]
        changes = info.changes.get(plate_of.get(pos, -1))
        painted: list[trimesh.Trimesh] = []
        for piece in parts:
            rgba = (
                info.part_rgba(oid, piece.metadata.get("part_index")) if oid is not None else None
            )
            if rgba is None:
                continue  # modifier / negative part / support blocker
            painted.append(threemf_colour.paint_faces(piece, rgba, changes))
        items.append(trimesh.util.concatenate(painted) if painted else None)
    return items, assigned, item_oids


def plate_meshes(
    path: Path, plates: list[dict], bed: tuple[float, float] | None = None
) -> dict[int, trimesh.Trimesh]:
    """``{plate index: mesh}`` recentred onto one bed, painted by filament
    colour for multi-filament projects. Plates with no geometry are omitted.
    Build items not claimed by any plate go on plate 1 (logged)."""
    if not plates:
        return {}
    loaded = _load_items(path, plates, bed)
    if loaded is None:
        return {}
    items, assigned, _ = loaded
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


def whole_file_mesh(path: Path) -> trimesh.Trimesh | None:
    """The whole-file mesh (all plates on the virtual grid, as sliced), painted
    by filament colour -- or ``None`` when the file is not a multi-filament
    Bambu/Orca project (the caller keeps its plain mesh)."""
    with zipfile.ZipFile(path) as zf:
        info = threemf_colour.read_colour_info(zf)
        if info is None or not info.is_multicolour:
            return None
        plates = parse_unsliced_plates(zf)
        bed = read_bed(zf)
    loaded = _load_items(path, plates, bed)
    if loaded is None:
        return None
    parts = [m for m in loaded[0] if m is not None]
    return trimesh.util.concatenate(parts) if parts else None


def plate_extents(mesh: trimesh.Trimesh) -> list[float]:
    return [round(float(x), 2) for x in mesh.extents]


# An editor can place at most this many items; past it a file is not split.
MAX_OBJECTS = 100


@dataclass(frozen=True)
class ObjectPiece:
    """One build item of a 3MF as its own mesh, on a single bed (``plate`` is
    the 1-based plate it came from, ``None`` when the file has no plates)."""

    index: int  # position among the file's <build><item>s (stable, 0-based)
    plate: int | None
    name: str
    mesh: trimesh.Trimesh


def _object_names(zf: zipfile.ZipFile) -> dict[int, str]:
    data = slicedmeta.read_zip_member(zf, MODEL_SETTINGS)
    if data is None:
        return {}
    try:
        root = ET.fromstring(data)  # noqa: S314 - same trust level as slicedmeta
    except ET.ParseError:
        return {}
    out: dict[int, str] = {}
    for obj in root.findall("object"):
        oid = slicedmeta._parse_int(obj.get("id"))
        for meta in obj.findall("metadata"):
            if oid is not None and meta.get("key") == "name" and meta.get("value"):
                out[oid] = meta.get("value", "").strip()
                break
    return out


def object_meshes(path: Path) -> list[ObjectPiece]:
    """Each build item of a 3MF as an independent mesh (painted by filament
    colour for multi-filament projects). Items of a multi-plate project are
    moved from the virtual plate grid onto the single bed, exactly as
    :func:`plate_meshes` does per plate. ``[]`` when the file is not
    splittable: fewer than two items, more than ``MAX_OBJECTS``, or lib3mf
    and the XML disagree."""
    with zipfile.ZipFile(path) as zf:
        plates = parse_unsliced_plates(zf)
        bed = read_bed(zf)
        names = _object_names(zf)
    loaded = _load_items(path, plates, bed)
    if loaded is None:
        return []
    items, assigned, item_oids = loaded
    live = [pos for pos, m in enumerate(items) if m is not None]
    if len(live) < 2 or len(live) > MAX_OBJECTS:
        return []
    plate_of: dict[int, int] = {}
    n = max([p["index"] for p in plates] + [len(plates)]) if plates else 0
    for plate in plates:
        group = [pos for pos in assigned.get(plate["index"], []) if items[pos] is not None]
        if not group:
            continue
        merged = trimesh.util.concatenate([items[pos] for pos in group])
        before = merged.bounds[0].copy()
        _recentre(merged, plate["index"], n, bed)
        shift = merged.bounds[0] - before
        shift[2] = 0.0
        for pos in group:
            items[pos].apply_translation(shift)
            plate_of[pos] = plate["index"]
    out: list[ObjectPiece] = []
    for pos in live:
        oid = item_oids[pos]
        name = (names.get(oid) if oid is not None else None) or f"Object {pos + 1}"
        out.append(ObjectPiece(pos, plate_of.get(pos), name, items[pos]))
    return out
