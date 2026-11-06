"""``convert_to_glb`` pipeline step's per-format conversion (SPEC pipeline row
3; RESEARCH §2): stl/obj/3mf go through ``app.pipeline.meshload`` (already
handling the trimesh/lib3mf split); step/iges go through cascadio/OCP
respectively. This module is pure file-in, file-out conversion -- no DB, no
Celery -- exactly like ``meshload.load_mesh``; ``app.tasks.pipeline`` owns the
derivative/job bookkeeping around it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cascadio
import numpy as np
import trimesh

from app.models.enums import BlobFormat
from app.pipeline import cad, meshload, plates3mf, threemf_colour

logger = logging.getLogger(__name__)

_MESH_FORMATS = (BlobFormat.STL, BlobFormat.OBJ, BlobFormat.THREEMF)

# cascadio's STEP path goes through OCCT's own glTF writer, which -- per the
# glTF 2.0 spec's SI-meter convention -- always emits vertex coordinates in
# meters, regardless of the STEP file's own declared unit (verified
# empirically against this project's corpus: a `write.step.unit`-default
# STEP declaring millimeters comes back with every extent divided by 1000).
# Every OTHER branch here keeps the source's own numeric scale untouched --
# trimesh's GLB writer for stl/obj/3mf, and `cad.iges_to_glb`'s own
# trimesh-based export below, both pass numbers through as-is. Multiplying
# back by 1000 restores that same "1 GLB unit = 1 source unit" (mm, this
# project's working convention) for the STEP branch too. This matters
# concretely: `app.tasks.pipeline._cad_blob_meta` reads `BlobMeta.dims_mm`
# straight off this derivative's `mesh.extents` -- without this correction
# every STEP upload's recorded dimensions would be 1000x too small.
_CASCADIO_METERS_TO_MM = 1000.0

# Bump to regenerate every stored GLB derivative (``Derivative.version``).
# v1: GLBs are glTF Y-up (print-space Z-up meshes rotated -90 deg about X).
# v2: Bambu/Orca multi-filament 3MFs carry per-vertex filament colours.
# v3: layer type fix -- pauses no longer paint; MultiExtruder colour changes
#     only repaint that extruder's parts.
# v4: colour changes aimed at one extruder in every multi-filament mode, faces
#     split at change heights, modifier/negative/blocker parts always dropped,
#     per-triangle ``paint_color`` (MMU painting).
GLB_VERSION = 4
GLB_YUP_VERSION = 2  # first version with Y-up GLBs (older ones need a full rewrite)

# Z-up (print/CAD space) -> Y-up (glTF): (x, y, z) -> (x, z, -y).
_Z_UP_TO_Y_UP = trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0])
_Y_UP_TO_Z_UP = np.linalg.inv(_Z_UP_TO_Y_UP)


def to_y_up(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Rotated COPY of a print-space (Z-up) mesh, ready for glTF export.
    Every GLB writer goes through this; metadata/bbox stay in print space."""
    out = threemf_colour.bake_vertex_colours(mesh.copy())
    out.apply_transform(_Z_UP_TO_Y_UP)
    return out


def to_z_up(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Inverse of :func:`to_y_up` -- for code that reads a GLB derivative back
    and wants print-space dimensions."""
    out = mesh.copy()
    out.apply_transform(_Y_UP_TO_Z_UP)
    return out


def convert_to_glb_file(
    src: Path, fmt: BlobFormat, dst: Path, *, project: plates3mf.ParsedProject | None = None
) -> str:
    """Convert ``src`` (a temp file carrying its real extension) to a GLB at
    ``dst``, returning the name of the tool that did the conversion.

    Every branch is a deterministic, potentially-failing parse/convert step:
    trimesh/lib3mf exceptions (via ``meshload.load_mesh``), cascadio's own
    exceptions, and ``cad.iges_to_glb``'s ``ValueError``s all propagate to the
    caller uncaught -- ``app.tasks.pipeline``'s ``convert_to_glb`` step maps
    any of them to a failed derivative + failed job (Global Constraints
    "Failure semantics").
    """
    if fmt in _MESH_FORMATS:
        coloured = None
        if fmt is BlobFormat.THREEMF:
            try:
                coloured = plates3mf.whole_file_mesh(src, project=project)
            except Exception:  # noqa: BLE001 - colour is best-effort; keep the plain mesh
                logger.warning("3mf colouring failed for %s", src.name, exc_info=True)
        if coloured is not None:
            mesh, tool = coloured, "lib3mf"
        else:
            mesh, tool = meshload.load_mesh(src, fmt)
        to_y_up(mesh).export(dst, file_type="glb")
        return tool
    if fmt is BlobFormat.STEP:
        cascadio.step_to_glb(str(src), str(dst), tol_linear=0.1, tol_angular=0.5)
        _rescale_glb_in_place(dst, _CASCADIO_METERS_TO_MM)
        return "cascadio"
    if fmt is BlobFormat.IGES:
        cad.iges_to_glb(src, dst)  # raw OCCT coords (Z-up); rotated below
        _rescale_glb_in_place(dst, 1.0)
        return "cadquery-ocp"
    raise ValueError(f"convert_to_glb_file: unsupported format {fmt}")


def _rescale_glb_in_place(path: Path, factor: float) -> None:
    """Reload the Z-up GLB just written to ``path`` (cascadio and
    ``cad.iges_to_glb`` both emit raw Z-up coordinates), scale it by
    ``factor``, rotate it to Y-up, and re-export over the same path -- see
    ``_CASCADIO_METERS_TO_MM`` above.
    """
    mesh = meshload.to_single_mesh(trimesh.load(path))
    mesh.apply_scale(factor)
    to_y_up(mesh).export(path, file_type="glb")
