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
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

import numpy as np
import trimesh

from app.pipeline import slicedmeta

MODEL_SETTINGS = "Metadata/model_settings.config"
PROJECT_SETTINGS = "Metadata/project_settings.config"
GCODE_PER_LAYER = "Metadata/custom_gcode_per_layer.xml"
NORMAL_PART = "normal_part"
# <layer type=N> per libslic3r CustomGCode.hpp: 0 ColorChange, 1 PausePrint,
# 2 ToolChange, 3 Template, 4 Custom. Only 0 and 2 repaint; 1 is a pause marker.
COLOUR_CHANGE = 0
PAUSE_PRINT = 1
TOOL_CHANGE = 2
MODE_MULTI_EXTRUDER = "MultiExtruder"
MODE_MULTI_AS_SINGLE = "MultiAsSingle"
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

    def painting_changes(
        self, plate_index: int, part_extruder: int | None
    ) -> list[tuple[float, tuple[int, int, int, int]]]:
        """``[(z, rgba)]`` ascending that repaint a part printed by
        ``part_extruder``. MultiExtruder: only that extruder's colour changes;
        otherwise everything above z (tool changes only in MultiAsSingle)."""
        mode = self.modes.get(plate_index)
        out = []
        for ev in self.events.get(plate_index, []):
            if mode == MODE_MULTI_EXTRUDER:
                if ev.kind == COLOUR_CHANGE and ev.extruder == part_extruder:
                    out.append((ev.z, ev.rgba))
            elif ev.kind == COLOUR_CHANGE or mode == MODE_MULTI_AS_SINGLE:
                out.append((ev.z, ev.rgba))
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

    def used_extruders(self, object_ids) -> list[int]:
        """Sorted unique extruder slots of the display parts of ``object_ids``
        (modifier/negative/blocker parts excluded, as in ``part_rgba``)."""
        used: set[int] = set()
        for oid in object_ids:
            obj = self.objects.get(oid)
            if obj is None:
                continue
            if obj.parts:
                used.update(p.extruder for p in obj.parts if p.subtype == NORMAL_PART)
            else:
                used.add(obj.extruder)
        return sorted(used)

    def plate_filaments(self, object_ids) -> list[dict]:
        return [
            {"slot": e, "colour": hex_rgb(self.extruder_rgba(e) or DEFAULT_RGBA)}
            for e in self.used_extruders(object_ids)
        ]

    def plate_colour_changes(self, plate_index: int) -> list[dict]:
        return [
            {"z": ev.z, "slot": ev.extruder, "colour": hex_rgb(ev.rgba)}
            for ev in self.events.get(plate_index, [])
        ]

    def plate_pauses(self, plate_index: int) -> list[dict]:
        return [{"z": z} for z in self.pauses.get(plate_index, [])]

    @property
    def is_multicolour(self) -> bool:
        """Only multi-filament files are painted: a single-colour project
        keeps the viewer's own default material instead of the slicer's
        (arbitrary) default filament colour."""
        if any(self.events.values()):
            return True
        used: set[int] = set()
        for obj in self.objects.values():
            if obj.parts:
                used.update(p.extruder for p in obj.parts if p.subtype == NORMAL_PART)
            else:
                used.add(obj.extruder)
        return len({self.extruder_rgba(e) or DEFAULT_RGBA for e in used}) > 1


def read_colour_info(zf: zipfile.ZipFile) -> ColourInfo | None:
    """``None`` when the file is not a Bambu/Orca project with filament data."""
    raw = slicedmeta.read_zip_member(zf, PROJECT_SETTINGS)
    settings_xml = slicedmeta.read_zip_member(zf, MODEL_SETTINGS)
    if raw is None or settings_xml is None:
        return None
    try:
        cfg = json.loads(raw)
        root = ET.fromstring(settings_xml)  # noqa: S314 - same trust level as slicedmeta
    except ValueError:  # JSONDecodeError and ParseError-adjacent
        return None
    except ET.ParseError:
        return None
    colours = cfg.get("filament_colour") if isinstance(cfg, dict) else None
    if not isinstance(colours, list) or not colours:
        return None
    palette = [parse_hex(c if isinstance(c, str) else None) or DEFAULT_RGBA for c in colours]

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
    if not objects:
        return None

    out = ColourInfo(palette=palette, objects=objects, events={})
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
                kind = slicedmeta._parse_int(layer.get("type"))
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
    return out


def paint_faces(
    mesh: trimesh.Trimesh,
    base: tuple[int, int, int, int],
    changes: list[tuple[float, tuple[int, int, int, int]]] | None,
) -> trimesh.Trimesh:
    """Set per-face colours: ``base``, overridden above each colour-change
    ``top_z`` by the face-centroid Z (mesh must be in world space, Z up)."""
    colours = np.tile(np.array(base, dtype=np.uint8), (len(mesh.faces), 1))
    if changes and len(mesh.faces):
        zc = mesh.triangles_center[:, 2]
        for top_z, rgba in changes:  # ascending: later (higher) changes win
            colours[zc > top_z] = rgba
    mesh.visual = trimesh.visual.ColorVisuals(mesh, face_colors=colours)
    return mesh


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
