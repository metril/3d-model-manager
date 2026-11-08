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
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
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
    plate_els = root.findall("plate")
    for plate_el, index in zip(
        plate_els, threemf_colour.resolve_plate_indices(plate_els), strict=True
    ):
        meta = slicedmeta._plate_metadata(plate_el)
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


def plate_filament_info(
    zf: zipfile.ZipFile, plates: list[dict], *, scan_paint: bool = True
) -> dict[int, dict]:
    """``{plate index: {"filaments": [...], "colour_changes": [...], "pauses": [...]}}``; empty
    lists when the file has no Bambu/Orca filament data."""
    info = threemf_colour.read_colour_info(zf, scan_paint=scan_paint)
    return {
        p["index"]: {
            "filaments": info.plate_filaments(p["object_ids"], p["index"]) if info else [],
            "colour_changes": (
                info.plate_colour_changes(p["index"], p["object_ids"]) if info else []
            ),
            "pauses": info.plate_pauses(p["index"]) if info else [],
        }
        for p in plates
    }


_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_PARA_RE = re.compile(r"</p\s*>", re.IGNORECASE)


def clean_meta_text(text: str | None) -> str | None:
    """Decode (possibly double) entity-escaped HTML to plain text. Only real tag
    shapes are stripped (a bare ``<`` survives); ``<br>``/``</p>`` become line /
    paragraph breaks."""
    if not text:
        return None
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = _PARA_RE.sub("\n\n", _BREAK_RE.sub("\n", text))
    text = _TAG_RE.sub(" ", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out or None


_PROFILE_KEYS = frozenset(
    {
        "DesignProfileId",
        "ProfileId",
        "DesignModelId",
        "ProfileTitle",
        "Title",
        "ProfileDescription",
        "ProfileUserName",
        "Designer",
    }
)


def read_3mf_profile_meta(zf: zipfile.ZipFile) -> dict | None:
    """MakerWorld provenance from ``3D/3dmodel.model`` ``<metadata>``, in the
    shape the MakerWorld importer stores in ``files.source_meta``; ``None``
    when the file carries no profile/design id. Streams the head of the model
    and stops at ``<resources>`` (metadata precedes it)."""
    md: dict[str, str] = {}
    try:
        with zf.open(ROOT_MODEL) as fh:
            for ev, el in ET.iterparse(fh, events=("start", "end")):  # noqa: S314
                name = _local(el.tag)
                if ev == "start" and name == "resources":
                    break
                if ev == "end" and name == "metadata" and el.get("name") in _PROFILE_KEYS:
                    md[el.get("name")] = (el.text or "").strip()
    except (KeyError, ET.ParseError):
        if not md:
            return None
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
        "author": clean_meta_text(md.get("ProfileUserName") or md.get("Designer")),
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


# A real ``project_settings.config`` is ~50 KB; refuse anything absurd on a
# user-triggered read.
MAX_PROJECT_SETTINGS_BYTES = 8 * 1024 * 1024


def _read_project_settings(zf: zipfile.ZipFile) -> dict | None:
    try:
        if zf.getinfo(PROJECT_SETTINGS).file_size > MAX_PROJECT_SETTINGS_BYTES:
            return None
    except KeyError:
        return None
    data = slicedmeta.read_zip_member(zf, PROJECT_SETTINGS)
    if data is None:
        return None
    try:
        cfg = json.loads(data)
    except ValueError:
        return None
    return cfg if isinstance(cfg, dict) else None


def _finite(value: object) -> float | None:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _str_id(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def read_embedded_process(zf: zipfile.ZipFile) -> dict | None:
    """The 3MF's own process settings summary from ``project_settings.config``:
    ``{layer_height, nozzle_mm, print_settings_id, printer_settings_id}``.
    ``None`` when the file has no (parsable, sanely sized) project settings.
    Values of the wrong type come back as ``None``."""
    cfg = _read_project_settings(zf)
    if cfg is None:
        return None
    nozzle = cfg.get("nozzle_diameter")
    return {
        "layer_height": _finite(cfg.get("layer_height")),
        "nozzle_mm": _finite(nozzle[0] if isinstance(nozzle, list) and nozzle else nozzle),
        "print_settings_id": _str_id(cfg.get("print_settings_id")),
        "printer_settings_id": _str_id(cfg.get("printer_settings_id")),
    }


def clamp_project_settings(path: Path, ranges: dict[str, tuple[float, float]]) -> list[str]:
    """Clamp/drop out-of-range keys inside ``path``'s ``project_settings.config``
    (rewrites the zip in place when anything changed). Returns the changed keys."""
    from app.services.profile_sanitize import clamp_key

    with zipfile.ZipFile(path) as zf:
        cfg = _read_project_settings(zf)
        if cfg is None:
            return []
        changed = [k for k, (lo, hi) in ranges.items() if clamp_key(cfg, k, lo, hi)]
        if not changed:
            return []
        tmp = path.with_suffix(path.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for item in zf.infolist():
                data = (
                    json.dumps(cfg).encode()
                    if item.filename == PROJECT_SETTINGS
                    else zf.read(item.filename)
                )
                out.writestr(item, data)
    tmp.replace(path)
    return changed


def sliced_object_count(path: Path) -> int | None:
    """Objects listed across the plates of a sliced ``.gcode.3mf``'s
    ``slice_info.config`` (``None`` if unreadable)."""
    try:
        with zipfile.ZipFile(path) as zf:
            data = slicedmeta.read_zip_member(zf, "Metadata/slice_info.config")
    except (zipfile.BadZipFile, OSError):
        return None
    if data is None:
        return None
    return len(re.findall(rb"<object\b", data))


def source_object_counts(path: Path) -> dict[int, int] | None:
    """``{plate index: object count}`` of an unsliced project 3MF (``None`` when
    the file carries no plate data)."""
    try:
        with zipfile.ZipFile(path) as zf:
            plates = parse_unsliced_plates(zf)
    except (zipfile.BadZipFile, OSError):
        return None
    return {p["index"]: len(p["object_ids"]) for p in plates} or None


_ITEM_RE = re.compile(rb"<(?:[\w.-]+:)?item\b([^>]*)>")
_OBJECTID_RE = re.compile(rb"\bobjectid\s*=\s*[\"'](\d+)[\"']")


def _build_item_object_ids(zf: zipfile.ZipFile) -> list[int | None]:
    """``objectid`` of each ``<build><item>`` in document order, found without
    building a tree (the model can hold millions of vertices)."""
    data = slicedmeta.read_zip_member(zf, ROOT_MODEL)
    if data is None:
        return []
    out: list[int | None] = []
    for m in _ITEM_RE.finditer(data):
        oid = _OBJECTID_RE.search(m.group(1))
        out.append(int(oid.group(1)) if oid else None)
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
    bounds: list[np.ndarray | None],
    item_oids: list[int | None],
    plates: list[dict],
    bed: tuple[float, float] | None,
) -> dict[int, list[int]]:
    """``{plate index: [build item positions]}`` from per-item ``(lo, hi)``
    bounds. Items not claimed by any plate go on the plate whose grid cell
    holds them (logged); an instance that does not exist, or an item already
    claimed, is skipped (logged)."""
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
            if not 0 <= inst < len(slots):
                logger.warning(
                    "plates3mf: plate %s object %s instance %s out of range (%d items); skipped",
                    plate["index"],
                    oid,
                    inst,
                    len(slots),
                )
                continue
            pos = slots[inst]
            if pos in claimed:
                logger.warning("plates3mf: build item %d claimed twice; skipped", pos)
                continue
            assigned[plate["index"]].append(pos)
            claimed.add(pos)
    n = max(max(p["index"] for p in plates), len(plates))
    eff = bed or DEFAULT_BED
    cols = max(1, math.ceil(math.sqrt(n)))
    for pos in range(len(bounds)):
        if pos in claimed or bounds[pos] is None:
            continue
        lo, hi = bounds[pos]
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


@dataclass
class ParsedProject:
    """A 3MF parsed once: lib3mf pieces per build item plus everything needed
    to build whole-file, per-plate and per-object meshes from them."""

    pieces: list[list[trimesh.Trimesh]]
    item_oids: list[int | None]
    plates: list[dict]
    bed: tuple[float, float] | None
    objects: dict[int, threemf_colour.ObjectInfo] = field(default_factory=dict)
    info: threemf_colour.ColourInfo | None = None  # only for multi-filament projects
    names: dict[int, str] = field(default_factory=dict)
    _assigned: dict[int, list[int]] | None = field(default=None, repr=False)

    @property
    def has_non_display(self) -> bool:
        return threemf_colour.has_non_display_parts(self.objects)

    def assigned(self) -> dict[int, list[int]]:
        if self._assigned is None:
            bounds = [
                (
                    np.vstack([p.bounds[0] for p in parts]).min(axis=0),
                    np.vstack([p.bounds[1] for p in parts]).max(axis=0),
                )
                if parts
                else None
                for parts in self.pieces
            ]
            self._assigned = _assign_items(bounds, self.item_oids, self.plates, self.bed)
        return self._assigned

    def build_items(self) -> list[trimesh.Trimesh | None]:
        """One mesh per build item: multi-filament projects painted, every
        project without modifier / negative / blocker parts."""
        plate_of = {pos: idx for idx, poss in self.assigned().items() for pos in poss}
        items: list[trimesh.Trimesh | None] = []
        for pos, parts in enumerate(self.pieces):
            oid = self.item_oids[pos]
            out: list[trimesh.Trimesh] = []
            for piece in parts:
                part_index = piece.metadata.get("part_index")
                if self.info is not None and oid is not None:
                    painted = threemf_colour.paint_part(
                        piece, self.info, plate_of.get(pos, -1), oid, part_index
                    )
                    if painted is not None:
                        out.append(painted)
                elif threemf_colour.is_display_part(self.objects, oid, part_index):
                    out.append(piece.copy())
            items.append(trimesh.util.concatenate(out) if out else None)
        return items


def load_project(path: Path, *, only_if_needed: bool = False) -> ParsedProject | None:
    """Parse ``path`` once. ``None`` when lib3mf and the XML disagree about the
    item count, or (``only_if_needed``) when the file is neither multi-filament
    nor carries non-display parts -- the caller's plain mesh is already right."""
    with zipfile.ZipFile(path) as zf:
        item_oids = _build_item_object_ids(zf)
        plates = parse_unsliced_plates(zf)
        bed = read_bed(zf)
        colour = threemf_colour.read_colour_info(zf)
        objects = colour.objects if colour is not None else threemf_colour.read_object_infos(zf)
        names = _object_names(zf)
    if colour is not None and not colour.is_multicolour:
        colour = None
    if only_if_needed and colour is None and not threemf_colour.has_non_display_parts(objects):
        return None
    pieces = meshload.load_3mf_build_pieces(path)
    if len(pieces) != len(item_oids):
        logger.warning("plates3mf: %d lib3mf items vs %d xml items", len(pieces), len(item_oids))
        return None
    return ParsedProject(pieces, item_oids, plates, bed, objects, colour, names)


def plate_meshes(
    path: Path | None,
    plates: list[dict],
    bed: tuple[float, float] | None = None,
    *,
    project: ParsedProject | None = None,
) -> dict[int, trimesh.Trimesh]:
    """``{plate index: mesh}`` recentred onto one bed, painted by filament
    colour for multi-filament projects. Plates with no geometry are omitted.
    Build items not claimed by any plate go on the plate whose grid cell holds them."""
    if not plates:
        return {}
    project = project or (load_project(path) if path is not None else None)
    if project is None:
        return {}
    plates, bed = project.plates, project.bed
    items = project.build_items()
    assigned = project.assigned()
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


def whole_file_mesh(
    path: Path | None, *, project: ParsedProject | None = None
) -> trimesh.Trimesh | None:
    """The whole-file mesh (all plates on the virtual grid, as sliced), painted
    by filament colour and without modifier/negative/blocker parts -- or
    ``None`` when the file needs neither (the caller keeps its plain mesh)."""
    if project is None:
        project = load_project(path, only_if_needed=True) if path is not None else None
    if project is None or (project.info is None and not project.has_non_display):
        return None
    parts = [m for m in project.build_items() if m is not None]
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


def object_meshes(path: Path | None, *, project: ParsedProject | None = None) -> list[ObjectPiece]:
    """Each build item of a 3MF as an independent mesh (painted by filament
    colour for multi-filament projects). Items of a multi-plate project are
    moved from the virtual plate grid onto the single bed, exactly as
    :func:`plate_meshes` does per plate. ``[]`` when the file is not
    splittable: fewer than two items, more than ``MAX_OBJECTS``, or lib3mf
    and the XML disagree."""
    project = project or (load_project(path) if path is not None else None)
    if project is None:
        return []
    plates, bed = project.plates, project.bed
    names = project.names
    items = project.build_items()
    assigned = project.assigned()
    item_oids = project.item_oids
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
