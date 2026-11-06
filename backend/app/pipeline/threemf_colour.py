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
COLOUR_CHANGE = 1  # <layer type="1"> = filament colour change
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
class ColourInfo:
    palette: list[tuple[int, int, int, int]]
    objects: dict[int, ObjectInfo]
    # plate index -> [(top_z, rgba)] sorted by top_z
    changes: dict[int, list[tuple[float, tuple[int, int, int, int]]]]
    # plate index -> extruder slot per change (parallel to ``changes``; None when unknown)
    change_slots: dict[int, list[int | None]] = field(default_factory=dict)

    def extruder_rgba(self, extruder: int) -> tuple[int, int, int, int] | None:
        return self.palette[extruder - 1] if 1 <= extruder <= len(self.palette) else None

    def part_rgba(self, oid: int, part_index: int | None) -> tuple[int, int, int, int] | None:
        """Colour of a display part, or ``None`` when the part is not display
        geometry (modifier, negative part, support blocker/enforcer)."""
        obj = self.objects.get(oid)
        if obj is None:
            return self.extruder_rgba(1) or DEFAULT_RGBA
        extruder = obj.extruder
        # Part lists are positional; a mismatch (e.g. a single mesh holding
        # several parts) falls back to the object-level colour.
        if part_index is not None and 0 <= part_index < len(obj.parts):
            part = obj.parts[part_index]
            if part.subtype != NORMAL_PART:
                return None
            extruder = part.extruder
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
        rows = self.changes.get(plate_index, [])
        slots = self.change_slots.get(plate_index, [None] * len(rows))
        return [
            {"z": z, "slot": slot, "colour": hex_rgb(rgba)}
            for (z, rgba), slot in zip(rows, slots, strict=False)
        ]

    @property
    def is_multicolour(self) -> bool:
        """Only multi-filament files are painted: a single-colour project
        keeps the viewer's own default material instead of the slicer's
        (arbitrary) default filament colour."""
        if any(self.changes.values()):
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

    out = ColourInfo(palette=palette, objects=objects, changes={})
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
            rows: list[tuple[float, tuple[int, int, int, int], int | None]] = []
            for layer in plate_el.findall("layer"):
                if slicedmeta._parse_int(layer.get("type")) != COLOUR_CHANGE:
                    continue
                try:
                    top_z = float(layer.get("top_z", ""))
                except ValueError:
                    continue
                ext = slicedmeta._parse_int(layer.get("extruder"))
                rgba = parse_hex(layer.get("color")) or (out.extruder_rgba(ext) if ext else None)
                if rgba is not None:
                    rows.append((top_z, rgba, ext))
            rows.sort(key=lambda r: r[0])
            out.changes[idx] = [(z, c) for z, c, _ in rows]
            out.change_slots[idx] = [e for _, _, e in rows]
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
