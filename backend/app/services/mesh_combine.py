# ruff: noqa: E501
"""Combine several meshes onto one plate as a single multi-object 3MF.

The slicer takes one model file per slice, so slicing several separate STLs on
one plate means building that file first. A plain core-spec 3MF is enough:
each source mesh is one ``<object>`` and every copy is a ``<build><item>``
pointing at it with its own X/Y translation, so ten copies cost one mesh.

Layout (``plan_layout``) is pure math on footprints and the bed, so it is
testable without meshes: ``grid`` uses equal cells sized to the largest
footprint, ``auto`` shelf-packs tallest-first. When the copies cannot fit the
bed the plan reports the bed they would need.
"""

from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Every item is a full object for the slicer to arrange and slice; past this a
# single plate cannot hold them and the request is more likely a typo.
MAX_COMBINE_INSTANCES = 100
MAX_COMBINE_SOURCE_BYTES = 300 * 1024 * 1024
MAX_COMBINE_SOURCE_FACES = 5_000_000

_XML_CHUNK_ROWS = 20_000
_CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

LayoutMode = Literal["grid", "auto"]


class MeshCombineError(ValueError):
    """A source could not be combined; the message is user-facing."""


@dataclass(frozen=True)
class CombinePart:
    name: str
    path: Path
    copies: int = 1


@dataclass(frozen=True)
class Placement:
    """One instance: ``owner`` indexes the part list; ``x``/``y`` is the
    footprint's min corner in bed coordinates (origin at a bed corner)."""

    owner: int
    x: float
    y: float
    w: float
    d: float


@dataclass(frozen=True)
class LayoutPlan:
    fits: bool
    placements: list[Placement]
    required_bed: tuple[float, float]


def _shelf(
    items: list[tuple[int, float, float]], row_limit: float, gap: float
) -> tuple[dict[int, tuple[float, float]], float, float]:
    """Shelf-pack ``(index, w, d)`` tallest-first into rows at most
    ``row_limit`` wide; returns min-corner offsets and the used width/depth."""
    offsets: dict[int, tuple[float, float]] = {}
    x = y = row_depth = used_w = 0.0
    for idx, w, d in sorted(items, key=lambda it: it[2], reverse=True):
        if x > 0 and x + w > row_limit + 1e-9:
            y += row_depth + gap
            x = row_depth = 0.0
        offsets[idx] = (x, y)
        used_w = max(used_w, x + w)
        x += w + gap
        row_depth = max(row_depth, d)
    return offsets, used_w, y + row_depth


def _grid(
    footprints: list[tuple[float, float]], cols: int, gap: float
) -> tuple[list[tuple[float, float]], float, float]:
    cw = max(w for w, _ in footprints)
    cd = max(d for _, d in footprints)
    rows = math.ceil(len(footprints) / cols)
    offsets = [((i % cols) * (cw + gap), (i // cols) * (cd + gap)) for i in range(len(footprints))]
    used_cols = min(cols, len(footprints))
    return offsets, used_cols * cw + (used_cols - 1) * gap, rows * cd + (rows - 1) * gap


def plan_layout(
    footprints: list[tuple[float, float]],
    owners: list[int],
    bed: tuple[float, float],
    *,
    mode: LayoutMode = "auto",
    spacing: float = 5.0,
) -> LayoutPlan:
    """Place ``footprints`` (one per instance, ``owners`` = part index) on a
    ``bed`` of (x, y) mm. Centred on the bed when it fits; otherwise
    ``fits=False`` with the bed a roughly-square layout would need."""
    if not footprints:
        return LayoutPlan(True, [], (0.0, 0.0))
    bed_x, bed_y = bed
    n = len(footprints)

    def build(
        offsets: list[tuple[float, float]], used_w: float, used_d: float, fits: bool
    ) -> LayoutPlan:
        # Centre only when a layout was actually fitted to the bed.
        sx = (bed_x - used_w) / 2 if fits else 0.0
        sy = (bed_y - used_d) / 2 if fits else 0.0
        placed = [
            Placement(owners[i], ox + sx, oy + sy, footprints[i][0], footprints[i][1])
            for i, (ox, oy) in enumerate(offsets)
        ]
        return LayoutPlan(fits, placed, (round(used_w, 2), round(used_d, 2)))

    if mode == "grid":
        cw = max(w for w, _ in footprints)
        cols = int((bed_x + spacing + 1e-9) // (cw + spacing))
        if cols >= 1:
            offsets, used_w, used_d = _grid(footprints, cols, spacing)
            if used_d <= bed_y + 1e-9:
                return build(offsets, used_w, used_d, True)
        side = max(1, math.ceil(math.sqrt(n)))
        offsets, used_w, used_d = _grid(footprints, side, spacing)
        return build(offsets, used_w, used_d, False)

    items = [(i, w, d) for i, (w, d) in enumerate(footprints)]
    widest = max(w for w, _ in footprints)
    if widest <= bed_x + 1e-9:
        off, used_w, used_d = _shelf(items, bed_x, spacing)
        if used_d <= bed_y + 1e-9:
            return build([off[i] for i in range(n)], used_w, used_d, True)
    area = sum((w + spacing) * (d + spacing) for w, d in footprints)
    off, used_w, used_d = _shelf(items, max(widest, math.sqrt(area)), spacing)
    return build([off[i] for i in range(n)], used_w, used_d, False)


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


@dataclass(frozen=True)
class LoadedPart:
    name: str
    vertices: object  # (N, 3), footprint min at (0, 0), z min at 0
    faces: object
    width: float
    depth: float
    copies: int


def _object_name(filename: str) -> str:
    stem = Path(filename).stem or "object"
    return re.sub(r"[\x00-\x1f]", "", stem)[:200] or "object"


def _load_part(part: CombinePart) -> LoadedPart:
    import trimesh

    try:
        mesh = trimesh.load(str(part.path), force="mesh")
    except Exception as exc:
        raise MeshCombineError(f"Could not read {part.name}: {exc}") from exc
    if (
        mesh is None
        or not hasattr(mesh, "vertices")
        or len(mesh.vertices) == 0
        or len(mesh.faces) == 0
    ):
        raise MeshCombineError(f"{part.name} contains no printable geometry")
    vertices = mesh.vertices.copy()
    lo = vertices.min(axis=0)
    hi = vertices.max(axis=0)
    vertices -= lo
    return LoadedPart(
        name=_object_name(part.name),
        vertices=vertices,
        faces=mesh.faces,
        width=float(hi[0] - lo[0]),
        depth=float(hi[1] - lo[1]),
        copies=part.copies,
    )


def _load_parts(parts: list[CombinePart]) -> list[LoadedPart]:
    if not parts:
        raise MeshCombineError("Select at least one model to combine")
    for part in parts:
        if part.copies < 1:
            raise MeshCombineError(f"{part.name}: copies must be at least 1")
    total = sum(p.copies for p in parts)
    if total > MAX_COMBINE_INSTANCES:
        raise MeshCombineError(
            f"Too many objects: {total} requested, at most {MAX_COMBINE_INSTANCES} per plate"
        )
    size = 0
    for part in parts:
        try:
            size += part.path.stat().st_size
        except OSError as exc:
            raise MeshCombineError(f"Could not read {part.name}: {exc}") from exc
    if size > MAX_COMBINE_SOURCE_BYTES:
        raise MeshCombineError(
            f"The selected models are too large to combine: at most {MAX_COMBINE_SOURCE_BYTES // 1024**2} MB in total"
        )
    loaded: list[LoadedPart] = []
    faces = 0
    for part in parts:
        item = _load_part(part)
        faces += len(item.faces)
        if faces > MAX_COMBINE_SOURCE_FACES:
            raise MeshCombineError(
                f"The selected models are too detailed to combine: more than {MAX_COMBINE_SOURCE_FACES:,} triangles"
            )
        loaded.append(item)
    return loaded


def plan_parts(
    parts: list[CombinePart], bed: tuple[float, float], *, mode: LayoutMode, spacing: float
) -> tuple[list[LoadedPart], LayoutPlan]:
    """Load ``parts`` and lay their copies out on ``bed``. Blocking."""
    loaded = _load_parts(parts)
    footprints: list[tuple[float, float]] = []
    owners: list[int] = []
    for idx, part in enumerate(loaded):
        for _ in range(part.copies):
            footprints.append((part.width, part.depth))
            owners.append(idx)
    return loaded, plan_layout(footprints, owners, bed, mode=mode, spacing=spacing)


def _fmt(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _attr(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _write_rows(out, rows, render) -> None:
    for start in range(0, len(rows), _XML_CHUNK_ROWS):
        chunk = rows[start : start + _XML_CHUNK_ROWS].tolist()
        out.write("".join(render(*row) for row in chunk).encode("utf-8"))


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    "</Relationships>"
)


def write_3mf(loaded: list[LoadedPart], plan: LayoutPlan) -> bytes:
    """Zip bytes of a 3MF: one ``<object>`` per source, one build item per copy."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        with zf.open("3D/3dmodel.model", "w", force_zip64=True) as out:
            out.write(
                (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    f'<model unit="millimeter" xml:lang="en-US" xmlns="{_CORE_NS}">'
                    '<metadata name="Application">3d-model-manager</metadata><resources>'
                ).encode()
            )
            for idx, part in enumerate(loaded):
                out.write(
                    f'<object id="{idx + 1}" name="{_attr(part.name)}" type="model"><mesh><vertices>'.encode()
                )
                _write_rows(
                    out,
                    part.vertices,
                    lambda x, y, z: f'<vertex x="{_fmt(x)}" y="{_fmt(y)}" z="{_fmt(z)}"/>',
                )
                out.write(b"</vertices><triangles>")
                _write_rows(
                    out, part.faces, lambda a, b, c: f'<triangle v1="{a}" v2="{b}" v3="{c}"/>'
                )
                out.write(b"</triangles></mesh></object>")
            out.write(b"</resources><build>")
            out.write(
                "".join(
                    f'<item objectid="{p.owner + 1}" transform="1 0 0 0 1 0 0 0 1 {_fmt(p.x)} {_fmt(p.y)} 0"/>'
                    for p in plan.placements
                ).encode("utf-8")
            )
            out.write(b"</build></model>")
    return buf.getvalue()
