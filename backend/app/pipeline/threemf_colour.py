"""Filament colours for Bambu/Orca project 3MFs.

The geometry in these files carries no colour; the slicer keeps it as
settings: ``filament_colour`` (``project_settings.config``), the ``extruder``
of each object/part and the part ``subtype`` (``model_settings.config``), and
per-plate colour changes (``custom_gcode_per_layer.xml``). This module reads
them and paints face colours onto the display meshes (modifiers, negative
parts and support blockers/enforcers are not display geometry).
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

import numpy as np
import trimesh

from app.pipeline import slicedmeta

logger = logging.getLogger(__name__)

MODEL_SETTINGS = "Metadata/model_settings.config"
PROJECT_SETTINGS = "Metadata/project_settings.config"
GCODE_PER_LAYER = "Metadata/custom_gcode_per_layer.xml"
ROOT_MODEL = "3D/3dmodel.model"
NORMAL_PART = "normal_part"
MODIFIER_PART = "modifier_part"
# Subtypes that put material down (a modifier prints with its own extruder);
# negative parts and support blockers/enforcers never do. Only NORMAL_PART is
# display geometry.
PRINTING_SUBTYPES = (NORMAL_PART, MODIFIER_PART)
PAINT_ATTR = "paint_color"
# <layer type=N> per libslic3r CustomGCode.hpp: 0 ColorChange, 1 PausePrint,
# 2 ToolChange, 3 Template, 4 Custom. Only 0 and 2 repaint; 1 is a pause marker.
COLOUR_CHANGE = 0
PAUSE_PRINT = 1
TOOL_CHANGE = 2
MODE_MULTI_EXTRUDER = "MultiExtruder"
MODE_MULTI_AS_SINGLE = "MultiAsSingle"
MULTI_MODES = (MODE_MULTI_EXTRUDER, MODE_MULTI_AS_SINGLE)
KIND_COLOUR = "colour"
KIND_TOOL = "tool"
DEFAULT_RGBA = (191, 191, 191, 255)
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")


def parse_hex(value: str | None) -> tuple[int, int, int, int] | None:
    m = _HEX.match((value or "").strip())
    if not m:
        return None
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255


def hex_rgb(rgba: tuple[int, int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgba[:3])


@dataclass
class PartInfo:
    extruder: int
    subtype: str = NORMAL_PART


@dataclass
class ObjectInfo:
    extruder: int = 1
    parts: list[PartInfo] = field(default_factory=list)


@dataclass
class LayerEvent:
    z: float
    rgba: tuple[int, int, int, int]
    extruder: int | None
    kind: int  # COLOUR_CHANGE | TOOL_CHANGE


@dataclass
class ColourInfo:
    palette: list[tuple[int, int, int, int]]
    objects: dict[int, ObjectInfo]
    # plate index -> colour/tool changes sorted by z (pauses excluded)
    events: dict[int, list[LayerEvent]]
    # plate index -> slicer mode ("SingleExtruder" when absent)
    modes: dict[int, str] = field(default_factory=dict)
    # plate index -> sorted pause z's
    pauses: dict[int, list[float]] = field(default_factory=dict)
    # plate index -> object ids placed on it (``model_settings`` <plate>)
    plate_objects: dict[int, list[int]] = field(default_factory=dict)
    # (object id, part index | None) -> per-triangle ``paint_color`` state (0 = default)
    paint: dict[tuple[int, int | None], np.ndarray] = field(default_factory=dict)

    # -- painting ---------------------------------------------------------

    def event_applies(self, plate_index: int, ev: LayerEvent, part_extruder: int | None) -> bool:
        """Whether ``ev`` repaints a part printed by ``part_extruder``
        (``None`` = any part: only the mode decides). A colour change is aimed
        at one extruder in every multi-filament mode; SingleExtruder repaints
        everything; a tool change only repaints in MultiAsSingle."""
        mode = self.modes.get(plate_index)
        if ev.kind == TOOL_CHANGE:
            return mode == MODE_MULTI_AS_SINGLE
        if mode in MULTI_MODES:
            return part_extruder is None or ev.extruder == part_extruder
        return True

    def painting_changes(
        self, plate_index: int, part_extruder: int | None
    ) -> list[tuple[float, tuple[int, int, int, int]]]:
        """``[(z, rgba)]`` ascending that repaint a part printed by ``part_extruder``."""
        return [
            (ev.z, ev.rgba)
            for ev in self.events.get(plate_index, [])
            if self.event_applies(plate_index, ev, part_extruder)
        ]

    def live_events(self, plate_index: int, object_ids=None) -> list[LayerEvent]:
        """Events of the plate that would repaint something. With
        ``object_ids`` a multi-filament colour change must also be aimed at an
        extruder that prints display geometry there."""
        display = set(self.display_extruders(object_ids)) if object_ids is not None else None
        out = []
        for ev in self.events.get(plate_index, []):
            if not self.event_applies(plate_index, ev, None):
                continue
            if (
                display is not None
                and ev.kind == COLOUR_CHANGE
                and self.modes.get(plate_index) in MULTI_MODES
                and ev.extruder not in display
            ):
                continue
            out.append(ev)
        return out

    def extruder_rgba(self, extruder: int) -> tuple[int, int, int, int] | None:
        return self.palette[extruder - 1] if 1 <= extruder <= len(self.palette) else None

    def part_extruder(self, oid: int, part_index: int | None) -> int | None:
        """Extruder slot of a display part, or ``None`` when the part is not
        display geometry (modifier, negative part, support blocker/enforcer)."""
        obj = self.objects.get(oid)
        if obj is None:
            return 1
        # Part lists are positional; a mismatch (e.g. a single mesh holding
        # several parts) falls back to the object-level colour.
        if part_index is not None and 0 <= part_index < len(obj.parts):
            part = obj.parts[part_index]
            return part.extruder if part.subtype == NORMAL_PART else None
        return obj.extruder

    def part_rgba(self, oid: int, part_index: int | None) -> tuple[int, int, int, int] | None:
        """Colour of a display part, or ``None`` when it is not display geometry."""
        extruder = self.part_extruder(oid, part_index)
        if extruder is None:
            return None
        if oid not in self.objects:
            return self.extruder_rgba(1) or DEFAULT_RGBA
        return self.extruder_rgba(extruder) or DEFAULT_RGBA

    def painted_extruders(self, oid: int) -> set[int]:
        """Extruders that ``paint_color`` states put on display parts of ``oid``."""
        out: set[int] = set()
        for (o, part), states in self.paint.items():
            if o != oid:
                continue
            if self.part_extruder(o, part) is None:
                continue
            out.update(int(s) for s in np.unique(states) if s > 0)
        return out

    def _extruders(self, object_ids, subtypes) -> set[int]:
        used: set[int] = set()
        for oid in object_ids:
            obj = self.objects.get(oid)
            if obj is None:
                continue
            if obj.parts:
                used.update(p.extruder for p in obj.parts if p.subtype in subtypes)
            else:
                used.add(obj.extruder)
            used |= self.painted_extruders(oid)
        return used

    def display_extruders(self, object_ids) -> list[int]:
        return sorted(self._extruders(object_ids, (NORMAL_PART,)))

    def used_extruders(self, object_ids, plate_index: int | None = None) -> list[int]:
        """Sorted unique extruder slots that print on ``object_ids``: display
        parts, modifier parts (own extruder), ``paint_color`` states and, for
        ``plate_index``, the targets of live tool changes."""
        used = self._extruders(object_ids, PRINTING_SUBTYPES)
        if plate_index is not None:
            used.update(
                ev.extruder
                for ev in self.live_events(plate_index, object_ids)
                if ev.kind == TOOL_CHANGE and ev.extruder
            )
        return sorted(used)

    def plate_filaments(self, object_ids, plate_index: int | None = None) -> list[dict]:
        return [
            {"slot": e, "colour": hex_rgb(self.extruder_rgba(e) or DEFAULT_RGBA)}
            for e in self.used_extruders(object_ids, plate_index)
        ]

    def plate_colour_changes(self, plate_index: int, object_ids=None) -> list[dict]:
        return [
            {
                "z": ev.z,
                "slot": ev.extruder,
                "colour": hex_rgb(ev.rgba),
                "kind": KIND_TOOL if ev.kind == TOOL_CHANGE else KIND_COLOUR,
            }
            for ev in self.live_events(plate_index, object_ids)
        ]

    def plate_pauses(self, plate_index: int) -> list[dict]:
        return [{"z": z} for z in self.pauses.get(plate_index, [])]

    @property
    def is_multicolour(self) -> bool:
        """Only multi-filament files are painted: a single-colour project
        keeps the viewer's own default material instead of the slicer's
        (arbitrary) default filament colour. Events that would never repaint
        anything do not count."""
        for idx in self.events:
            if self.live_events(idx, self.plate_objects.get(idx)):
                return True
        used = self._extruders(self.objects, (NORMAL_PART,))
        return len({self.extruder_rgba(e) or DEFAULT_RGBA for e in used}) > 1


def resolve_plate_indices(plate_els: list[ET.Element]) -> list[int]:
    """1-based index of each ``<plate>``: its ``plater_id`` when every plate has
    a unique positive one, else renumbered by position (a duplicate or missing
    id would otherwise merge or drop plates)."""
    ids = [
        slicedmeta._parse_int(slicedmeta._plate_metadata(el).get("plater_id")) for el in plate_els
    ]
    if all(i is not None and i > 0 for i in ids) and len(set(ids)) == len(ids):
        return [i for i in ids if i is not None]
    if plate_els:
        logger.info("3mf: duplicate/missing plater_id; renumbering %d plates by position", len(ids))
    return list(range(1, len(plate_els) + 1))


def _parse_objects(root: ET.Element) -> dict[int, ObjectInfo]:
    objects: dict[int, ObjectInfo] = {}
    for obj_el in root.findall("object"):
        oid = slicedmeta._parse_int(obj_el.get("id"))
        if oid is None:
            continue
        meta = slicedmeta._plate_metadata(obj_el)
        info = ObjectInfo(extruder=slicedmeta._parse_int(meta.get("extruder")) or 1)
        for part_el in obj_el.findall("part"):
            pm = slicedmeta._plate_metadata(part_el)
            info.parts.append(
                PartInfo(
                    extruder=slicedmeta._parse_int(pm.get("extruder")) or info.extruder,
                    subtype=part_el.get("subtype") or NORMAL_PART,
                )
            )
        objects[oid] = info
    return objects


def _model_settings_root(zf: zipfile.ZipFile) -> ET.Element | None:
    data = slicedmeta.read_zip_member(zf, MODEL_SETTINGS)
    if data is None:
        return None
    try:
        return ET.fromstring(data)  # noqa: S314 - same trust level as slicedmeta
    except ET.ParseError:
        return None


def read_object_infos(zf: zipfile.ZipFile) -> dict[int, ObjectInfo]:
    """Per-object part list/subtypes from ``model_settings.config`` -- available
    even when the project carries no filament palette. ``{}`` when absent."""
    root = _model_settings_root(zf)
    return _parse_objects(root) if root is not None else {}


def is_display_part(objects: dict[int, ObjectInfo], oid: int | None, part_index: int | None):
    """False for modifier/negative/support-blocker parts (not display geometry)."""
    obj = objects.get(oid) if oid is not None else None
    if obj is None or part_index is None or not (0 <= part_index < len(obj.parts)):
        return True
    return obj.parts[part_index].subtype == NORMAL_PART


def has_non_display_parts(objects: dict[int, ObjectInfo]) -> bool:
    return any(p.subtype != NORMAL_PART for o in objects.values() for p in o.parts)


def layer_kind(layer: ET.Element) -> int | None:
    """``type`` of a ``<layer>``; layers without one fall back to the ``gcode``
    attribute (M600 colour change, M601 / ``M400 U1`` pause, ``tool_change`` / ``Tn``)."""
    kind = slicedmeta._parse_int(layer.get("type"))
    if kind is not None:
        return kind
    g = (layer.get("gcode") or "").strip().lower()
    if g.startswith("m600"):
        return COLOUR_CHANGE
    if g.startswith("m601") or re.match(r"m400\s+u1\b", g):
        return PAUSE_PRINT
    if g == "tool_change" or re.fullmatch(r"t\d+", g):
        return TOOL_CHANGE
    return None


def read_colour_info(zf: zipfile.ZipFile, *, scan_paint: bool = True) -> ColourInfo | None:
    """``None`` when the file is not a Bambu/Orca project with filament data."""
    raw = slicedmeta.read_zip_member(zf, PROJECT_SETTINGS)
    root = _model_settings_root(zf)
    if raw is None or root is None:
        return None
    try:
        cfg = json.loads(raw)
    except ValueError:
        return None
    colours = cfg.get("filament_colour") if isinstance(cfg, dict) else None
    if not isinstance(colours, list) or not colours:
        return None
    palette = [parse_hex(c if isinstance(c, str) else None) or DEFAULT_RGBA for c in colours]

    objects = _parse_objects(root)
    if not objects:
        return None

    out = ColourInfo(palette=palette, objects=objects, events={})
    plate_els = root.findall("plate")
    for idx, plate_el in zip(resolve_plate_indices(plate_els), plate_els, strict=True):
        out.plate_objects[idx] = [
            oid
            for inst in plate_el.findall("model_instance")
            if (oid := slicedmeta._parse_int(slicedmeta._plate_metadata(inst).get("object_id")))
            is not None
        ]
    gcodes = slicedmeta.read_zip_member(zf, GCODE_PER_LAYER)
    if gcodes is not None:
        try:
            groot = ET.fromstring(gcodes)  # noqa: S314
        except ET.ParseError:
            groot = None
        for plate_el in groot.findall("plate") if groot is not None else []:
            info_el = plate_el.find("plate_info")
            idx = slicedmeta._parse_int(info_el.get("id")) if info_el is not None else None
            if idx is None:
                continue
            events: list[LayerEvent] = []
            pauses: list[float] = []
            for layer in plate_el.findall("layer"):
                kind = layer_kind(layer)
                if kind not in (COLOUR_CHANGE, PAUSE_PRINT, TOOL_CHANGE):
                    continue
                try:
                    top_z = float(layer.get("top_z", ""))
                except ValueError:
                    continue
                if kind == PAUSE_PRINT:
                    pauses.append(top_z)
                    continue
                ext = slicedmeta._parse_int(layer.get("extruder"))
                slot_rgba = out.extruder_rgba(ext) if ext else None
                rgba = (
                    (parse_hex(layer.get("color")) or slot_rgba)
                    if kind == COLOUR_CHANGE
                    else slot_rgba or parse_hex(layer.get("color"))
                )
                if rgba is not None:
                    events.append(LayerEvent(top_z, rgba, ext, kind))
            events.sort(key=lambda e: e.z)
            out.events[idx] = events
            out.pauses[idx] = sorted(pauses)
            mode_el = plate_el.find("mode")
            if mode_el is not None and mode_el.get("value"):
                out.modes[idx] = mode_el.get("value", "")
    if scan_paint:
        try:
            out.paint = read_paint(zf)
        except (ET.ParseError, zipfile.BadZipFile, ValueError):
            logger.warning("3mf: paint_color scan failed", exc_info=True)
    return out


# -- MMU painting (``paint_color`` on <triangle>) ---------------------------------


def has_paint(zf: zipfile.ZipFile) -> bool:
    """Whether any ``*.model`` member mentions ``paint_color`` (streamed, stops at a hit)."""
    for name in zf.namelist():
        if not name.lower().endswith(".model"):
            continue
        with zf.open(name) as fh:
            tail = b""
            while chunk := fh.read(1 << 20):
                if PAINT_ATTR.encode() in tail + chunk:
                    return True
                tail = chunk[-len(PAINT_ATTR) :]
    return False


def decode_paint_state(code: str) -> int:
    """Dominant extruder state of one ``paint_color`` value.

    Bambu/Orca/PrusaSlicer ``TriangleSelector::serialize``: the hex string is a
    nibble stream stored back to front. A nibble is ``state << 2`` for a leaf
    (state 3 = ``0xC`` followed by a nibble holding ``state - 3``) or
    ``special_side << 2 | n`` with ``n`` in 1..3 for a triangle split into
    ``n + 1`` children (pre-order). A split triangle collapses to its largest
    leaf (area approximated by equal shares), ties to the first; display only.
    """
    try:
        nibbles = [int(c, 16) for c in reversed(code.strip())]
    except ValueError:
        return 0
    pos = 0
    weights: dict[int, float] = {}

    def rec(weight: float, depth: int) -> None:
        nonlocal pos
        if pos >= len(nibbles) or depth > 12:
            return
        nib = nibbles[pos]
        pos += 1
        sides = nib & 3
        if sides:
            for _ in range(sides + 1):
                rec(weight / (sides + 1), depth + 1)
            return
        state = nib >> 2
        if state == 3:
            # extended: runs of 0xF nibbles add 15 each, the final nibble the rest
            extra = 0
            while pos < len(nibbles) and nibbles[pos] == 0xF:
                extra += 15
                pos += 1
            if pos < len(nibbles):
                extra += nibbles[pos]
                pos += 1
            state = 3 + extra
        weights[state] = weights.get(state, 0.0) + weight

    rec(1.0, 0)
    if not weights:
        return 0
    return max(weights, key=lambda k: weights[k])  # dict order = first-seen on ties


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _scan_model_paint(fh) -> dict[int, np.ndarray]:
    """``{object id: per-triangle state}`` for objects of one model stream that
    carry any ``paint_color`` (others omitted)."""
    out: dict[int, np.ndarray] = {}
    cache: dict[str, int] = {}
    cur_id: int | None = None
    states: list[int] = []
    any_paint = False
    container: ET.Element | None = None
    seen = 0
    for ev, el in ET.iterparse(fh, events=("start", "end")):  # noqa: S314
        name = _local(el.tag)
        if ev == "start":
            if name == "object":
                cur_id = slicedmeta._parse_int(el.get("id"))
                states, any_paint = [], False
            elif name in ("vertices", "triangles"):
                container = el
            continue
        if name == "triangle":
            code = el.get(PAINT_ATTR)
            if code:
                state = cache.get(code)
                if state is None:
                    state = cache[code] = decode_paint_state(code)
                any_paint = any_paint or state > 0
                states.append(state)
            else:
                states.append(0)
        elif name == "object":
            if cur_id is not None and any_paint:
                out[cur_id] = np.minimum(np.asarray(states, dtype=np.int64), 255).astype(np.uint8)
            cur_id, states = None, []
            el.clear()
            continue
        if name in ("vertex", "triangle"):
            el.clear()
            seen += 1
            if container is not None and seen % 8192 == 0:
                container.clear()
    return out


def read_paint(zf: zipfile.ZipFile) -> dict[tuple[int, int | None], np.ndarray]:
    """``{(build object id, part index | None): per-triangle states}`` for parts
    with ``paint_color`` (state N = extruder N, 0 = the part's own extruder).
    ``{}`` when the file has none. Keys follow ``meshload``'s part order: the
    index of the object's top-level ``<component>``."""
    if not has_paint(zf):
        return {}
    root_bytes = slicedmeta.read_zip_member(zf, ROOT_MODEL)
    if root_bytes is None:
        return {}
    # root object id -> [(model path | None, sub-object id)] (None = root's own mesh)
    layout: dict[int, list[tuple[str | None, int]]] = {}
    cur: int | None = None
    import io

    for ev, el in ET.iterparse(io.BytesIO(root_bytes), events=("start", "end")):  # noqa: S314
        name = _local(el.tag)
        if ev == "start" and name == "object":
            cur = slicedmeta._parse_int(el.get("id"))
            if cur is not None:
                layout[cur] = []
        elif ev == "end" and name == "component" and cur is not None:
            sub = slicedmeta._parse_int(el.get("objectid"))
            path = next((v for k, v in el.attrib.items() if _local(k) == "path"), None)
            if sub is not None:
                layout[cur].append((path.lstrip("/") if path else None, sub))
        elif ev == "end" and name == "object":
            cur = None
        if ev == "end" and name in ("vertex", "triangle"):
            el.clear()
    scanned: dict[str | None, dict[int, np.ndarray]] = {}

    def scan(path: str | None) -> dict[int, np.ndarray]:
        if path not in scanned:
            try:
                with zf.open(path or ROOT_MODEL) as fh:
                    scanned[path] = _scan_model_paint(fh)
            except KeyError:
                scanned[path] = {}
        return scanned[path]

    out: dict[tuple[int, int | None], np.ndarray] = {}
    for oid, comps in layout.items():
        if not comps:
            arr = scan(None).get(oid)
            if arr is not None:
                out[(oid, None)] = arr
            continue
        for i, (path, sub) in enumerate(comps):
            arr = scan(path).get(sub)
            if arr is not None:
                out[(oid, i)] = arr
    return out


# -- painting -----------------------------------------------------------------------


def _clip_poly(tri: np.ndarray, h: float, lower: bool) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    for i in range(3):
        p, q = tri[i], tri[(i + 1) % 3]
        pin = p[2] <= h if lower else p[2] >= h
        qin = q[2] <= h if lower else q[2] >= h
        if pin:
            out.append(p)
        if pin != qin:
            t = (h - p[2]) / (q[2] - p[2])
            out.append(p + t * (q - p))
    dedup: list[np.ndarray] = []
    for pt in out:
        if not dedup or not np.allclose(dedup[-1], pt, atol=1e-9):
            dedup.append(pt)
    if len(dedup) > 1 and np.allclose(dedup[0], dedup[-1], atol=1e-9):
        dedup.pop()
    return dedup


def split_at_z(
    mesh: trimesh.Trimesh, zs, attr: np.ndarray | None = None
) -> tuple[trimesh.Trimesh, np.ndarray | None]:
    """Cut every triangle that strictly crosses a plane ``z = h`` into pieces on
    either side (display only: result is not watertight/merged). ``attr`` is a
    per-face array carried to the pieces. The input is not modified."""
    verts, faces = np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces)
    for h in sorted({float(z) for z in zs}):
        tz = verts[faces][:, :, 2]
        cross = (tz.min(axis=1) < h) & (tz.max(axis=1) > h)
        if not cross.any():
            continue
        new_v: list[np.ndarray] = []
        new_f: list[tuple[int, int, int]] = []
        new_a: list = []
        nv = len(verts)
        for fi in np.nonzero(cross)[0]:
            tri = verts[faces[fi]]
            for lower in (True, False):
                poly = _clip_poly(tri, h, lower)
                if len(poly) < 3:
                    continue
                base = nv + len(new_v)
                new_v.extend(poly)
                for k in range(1, len(poly) - 1):
                    new_f.append((base, base + k, base + k + 1))
                    if attr is not None:
                        new_a.append(attr[fi])
        faces_out = np.vstack([faces[~cross], np.asarray(new_f, dtype=faces.dtype)])
        if attr is not None:
            attr = np.concatenate([attr[~cross], np.asarray(new_a, dtype=attr.dtype)])
        verts = np.vstack([verts, np.asarray(new_v)])
        faces = faces_out
    return trimesh.Trimesh(vertices=verts, faces=faces, process=False), attr


def paint_part(
    mesh: trimesh.Trimesh, info: ColourInfo, plate_index: int, oid: int, part_index: int | None
) -> trimesh.Trimesh | None:
    """Coloured copy of one display part (filament colour, ``paint_color``
    regions, colour changes aimed at the faces' extruder), or ``None`` for a
    non-display part."""
    part_ext = info.part_extruder(oid, part_index)
    if part_ext is None:
        return None
    face_ext = np.full(len(mesh.faces), part_ext, dtype=np.int32)
    states = info.paint.get((oid, part_index))
    if states is not None and len(states) == len(mesh.faces):
        face_ext = np.where(states > 0, states.astype(np.int32), face_ext)
    changes = {e: info.painting_changes(plate_index, int(e)) for e in np.unique(face_ext)}
    zs = {z for ch in changes.values() for z, _ in ch}
    out, face_ext = split_at_z(mesh, zs, face_ext) if zs else (mesh.copy(), face_ext)
    colours = np.empty((len(out.faces), 4), dtype=np.uint8)
    zc = out.triangles_center[:, 2] if len(out.faces) else np.empty(0)
    for e, ch in changes.items():
        sel = face_ext == e
        base = (
            info.part_rgba(oid, part_index)
            if e == part_ext
            else info.extruder_rgba(int(e)) or DEFAULT_RGBA
        )
        col = np.tile(np.array(base, dtype=np.uint8), (int(sel.sum()), 1))
        z = zc[sel]
        for top_z, rgba in ch:
            col[z > top_z] = rgba
        colours[sel] = col
    out.visual = trimesh.visual.ColorVisuals(out, face_colors=colours)
    return out


def bake_vertex_colours(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Face colours -> vertex colours WITHOUT blending: vertices are split only
    where adjacent faces differ in colour (glTF only carries per-vertex
    colour; trimesh would otherwise average across shared vertices). Returns
    ``mesh`` itself unless it has face colours."""
    if mesh.visual.kind != "face" or len(mesh.faces) == 0:
        return mesh
    fc = np.asarray(mesh.visual.face_colors, dtype=np.uint8)
    packed = fc.view(np.uint32).reshape(-1).astype(np.uint64)
    corner_v = mesh.faces.reshape(-1).astype(np.uint64)
    key = (corner_v << np.uint64(32)) | np.repeat(packed, 3)
    uniq, inverse = np.unique(key, return_inverse=True)
    src_v = (uniq >> np.uint64(32)).astype(np.int64)
    vcol = (uniq & np.uint64(0xFFFFFFFF)).astype(np.uint32).view(np.uint8).reshape(-1, 4)
    return trimesh.Trimesh(
        vertices=mesh.vertices[src_v],
        faces=inverse.reshape(-1, 3),
        vertex_colors=vcol,
        process=False,
    )
