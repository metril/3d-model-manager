"""Mesh loading for the ``extract_metadata`` pipeline step (SPEC pipeline
row 1; RESEARCH §1): trimesh handles STL/OBJ/generic-3MF natively, but its
3MF reader only follows the Production Extension's ``p:path`` attribute on
``<component>`` elements, never on ``<build><item>`` -- exactly how Bambu
Studio/OrcaSlicer project files reference their geometry by default. lib3mf
(the 3MF Consortium's own reference implementation) is the fallback that
DOES understand that layout.

Both branches normalize the 3MF ``<model unit>`` attribute to millimetres
(U1, correctness map) -- trimesh parses it but never applies it, and lib3mf
exposes it as a raw ``ModelUnit`` enum the caller has to scale by itself.
Accepted tradeoff: a non-spec unit string now raises a clear ``ValueError``
(``guess=False``) at extract/convert time instead of silently mis-scaling --
a correctness improvement, but a previously-"succeeding" pathological upload
could newly fail (none observed in the corpus). This is a pure loader fix
with no migration: any meter-unit (or otherwise non-mm) 3MF ingested before
this change keeps its wrong ``BlobMeta``/GLB under the skip-if-exists
idempotency in ``app.tasks.pipeline`` -- re-upload, or manually delete its
``BlobMeta``/``Derivative`` rows, to reprocess it.

The trimesh branch has a second, sharper wrinkle: a multi-object 3MF (e.g. a
Bambu Studio project referencing more than one part via the Production
Extension's ``<component p:path=...>``, which trimesh's reader DOES follow)
loads as a multi-geometry ``Scene``. Flattening that ``Scene`` to one mesh
(``to_single_mesh``, below) goes through ``Scene.to_geometry()`` ->
``trimesh.util.concatenate``, and that function's metadata merge silently
drops ALL per-geometry ``metadata`` -- including the ``"units"`` tag trimesh
itself already set while parsing -- whenever there's more than one source
geometry (a trimesh bug, verified directly against this project's pinned
version). Post-flatten, the mesh looks unit-less even for a file whose unit
was never actually ambiguous, and ``guess=False`` would then raise on
every such file, single-object 3MFs (no flatten-induced loss) working fine.
``load_mesh`` below works around this by reading units off the ORIGINAL,
not-yet-flattened ``trimesh.load()`` result (a correct read even post-bug,
since ``Scene.units``/``Trimesh.units`` -- the un-flattened per-geometry
metadata -- is what's actually intact) and restoring that value onto the
flattened mesh before converting. A 3MF with truly no unit information at
all (root ``<model>`` missing ``unit``, which trimesh's OWN reader already
defaults to ``"millimeter"`` per the 3MF spec -- see
``trimesh.exchange.threemf.load_3MF``) still can't trip ``guess=False``'s
raise; the one remaining genuinely-unit-less case (an otherwise-empty
``Scene``) never reaches this code at all, since it has zero faces and
routes to the ``lib3mf`` fallback below instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np
import trimesh

from app.models.enums import BlobFormat


class MeshLoad(NamedTuple):
    mesh: trimesh.Trimesh
    tool: str


# lib3mf.ModelUnit codes (Lib3MF.py): MicroMeter=0 .. Meter=5 -> mm-per-unit.
_LIB3MF_UNIT_TO_MM = {0: 0.001, 1: 1.0, 2: 10.0, 3: 25.4, 4: 304.8, 5: 1000.0}


def to_single_mesh(loaded: trimesh.Trimesh | trimesh.Scene) -> trimesh.Trimesh:
    """Collapse a trimesh load result to one concatenated ``Trimesh``.

    Multi-object files (OBJ, 3MF, GLB) load as a ``Scene``; ``to_geometry()``
    bakes each geometry's transform and concatenates them into a single
    like-typed geometry. A bare ``Trimesh`` (the common case for
    single-object STL/OBJ) passes through unchanged.
    """
    if isinstance(loaded, trimesh.Scene):
        return loaded.to_geometry()
    return loaded


def load_mesh(path: Path, fmt: BlobFormat) -> MeshLoad:
    """Load ``path`` (a temp file for an stl/obj/3mf blob) into one
    concatenated mesh, reporting which tool actually produced it.

    stl/obj always go through trimesh. 3mf tries trimesh first and falls
    back to :func:`load_3mf_lib3mf` (RESEARCH §1's documented split) whenever
    trimesh's read either raises outright or comes back with zero faces --
    its current behavior on the Production-Extension layout Bambu
    Studio/OrcaSlicer write by default (trimesh returns an empty ``Scene``
    rather than erroring).
    """
    if fmt is not BlobFormat.THREEMF:
        mesh = to_single_mesh(trimesh.load(path))
        if len(mesh.faces) == 0:
            # trimesh doesn't raise on unparseable/garbage content -- it
            # silently comes back with an empty Scene (0 geometries), whose
            # `to_geometry()` is an empty Trimesh with `extents`/`bounds` of
            # `None`. There's no fallback tool for stl/obj (unlike 3mf
            # below), so this has to surface as a clear, explicit failure
            # here rather than crashing downstream on `None` extents.
            raise ValueError(f"no geometry found in {fmt.value} file: {path}")
        return MeshLoad(mesh, "trimesh")

    mesh: trimesh.Trimesh | None = None
    try:
        loaded = trimesh.load(path)
    except Exception:  # noqa: BLE001 - any load failure means "fall back to lib3mf"
        loaded = None
    if loaded is not None:
        # Read units off the SOURCE (Scene or Trimesh) before flattening --
        # see the module docstring's "second, sharper wrinkle": flattening a
        # multi-geometry Scene drops this same metadata via a trimesh bug,
        # so reading it post-flatten would be unreliable.
        source_units = loaded.units
        mesh = to_single_mesh(loaded)
        if mesh.units is None:
            mesh.units = source_units
    if mesh is not None and len(mesh.faces) > 0:
        # Apply the 3MF <model unit> attribute trimesh parsed but never
        # applies (units default to "millimeter" per the 3MF spec, so this
        # is a no-op factor 1.0 for the common case). Fixes BOTH
        # dims/volume/area AND the GLB derivative, since convert_to_glb_file
        # uses this loader.
        if mesh.units is None:
            # Genuinely no unit information reached trimesh at all (as
            # opposed to the flatten-induced loss handled above) -- the 3MF
            # spec's own default is millimeter, so this is the correct,
            # deterministic value rather than a "guess" (guess=False keeps
            # rejecting non-spec/ambiguous unit STRINGS, just not silence).
            mesh.units = "millimeters"
        else:
            mesh.convert_units("millimeters", guess=False)
        return MeshLoad(mesh, "trimesh")
    return MeshLoad(load_3mf_lib3mf(path), "lib3mf")


def _lib3mf_matrix(transform) -> np.ndarray:
    """lib3mf ``Transform`` (``Fields[4][3]``: three linear rows + translation
    row, row-vector convention) -> a column-vector 4x4."""
    fields = np.array([[float(c) for c in row] for row in transform.Fields], dtype=np.float64)
    m = np.eye(4)
    m[:3, :3] = fields[:3].T
    m[:3, 3] = fields[3]
    return m


def _lib3mf_object_meshes(
    model, obj, matrix: np.ndarray, depth: int = 0, part: int | None = None
) -> list[trimesh.Trimesh]:
    """Flatten one lib3mf object (mesh or components tree) into transformed
    meshes. ``matrix`` is the accumulated build-item/component transform.
    Each piece carries ``metadata["part_index"]``: the ORDER of the top-level
    component it came from (lib3mf renumbers resource ids, so order is the
    only stable key; Bambu lists parts in ``model_settings`` order), or
    ``None`` for a build item that is a bare mesh."""
    if depth > 8:  # malformed/cyclic component graphs
        return []
    if obj.IsMeshObject():
        mesh_object = model.GetMeshObjectByID(obj.GetResourceID())
        vertices = np.array([v.Coordinates for v in mesh_object.GetVertices()], dtype=np.float64)
        triangles = np.array([t.Indices for t in mesh_object.GetTriangleIndices()], dtype=np.int64)
        if len(vertices) == 0 or len(triangles) == 0:
            return []
        mesh = trimesh.Trimesh(vertices=vertices, faces=triangles, process=False)
        mesh.apply_transform(matrix)
        mesh.metadata["part_index"] = part
        return [mesh]
    if obj.IsComponentsObject():
        comps = model.GetComponentsObjectByID(obj.GetResourceID())
        out: list[trimesh.Trimesh] = []
        for i in range(comps.GetComponentCount()):
            comp = comps.GetComponent(i)
            sub = _lib3mf_matrix(comp.GetTransform()) if comp.HasTransform() else np.eye(4)
            out += _lib3mf_object_meshes(
                model,
                comp.GetObjectResource(),
                matrix @ sub,
                depth + 1,
                i if depth == 0 else part,
            )
        return out
    return []


def load_3mf_build_pieces(path: Path) -> list[list[trimesh.Trimesh]]:
    """Per ``<build><item>``, in document order: its mm-scaled,
    build-transformed mesh pieces (one per component leaf; ``[]`` for an item
    with no geometry). Components and Production-Extension ``p:path``
    references are followed by lib3mf."""
    import lib3mf

    wrapper = lib3mf.get_wrapper()
    model = wrapper.CreateModel()
    model.QueryReader("3mf").ReadFromFile(str(path))
    factor = _LIB3MF_UNIT_TO_MM.get(int(model.GetUnit()), 1.0)

    items: list[list[trimesh.Trimesh]] = []
    it = model.GetBuildItems()
    while it.MoveNext():
        item = it.GetCurrent()
        matrix = (
            _lib3mf_matrix(item.GetObjectTransform()) if item.HasObjectTransform() else np.eye(4)
        )
        parts = _lib3mf_object_meshes(model, item.GetObjectResource(), matrix)
        if factor != 1.0:
            for part in parts:
                part.apply_scale(factor)
        items.append(parts)
    return items


def load_3mf_build_items(path: Path) -> list[trimesh.Trimesh | None]:
    """One mm-scaled, build-transformed mesh per ``<build><item>``, in
    document order (``None`` for an item with no geometry)."""
    return [
        trimesh.util.concatenate(parts) if parts else None for parts in load_3mf_build_pieces(path)
    ]


def load_3mf_lib3mf(path: Path) -> trimesh.Trimesh:
    """Parse ``path`` via lib3mf's reference-implementation reader (RESEARCH
    §1's documented approach). This is the fallback for 3MF geometry
    trimesh's own reader can't follow (Production Extension ``<build><item
    p:path>`` references). Build-item transforms are applied (objects are
    placed where the slicer put them); a file with no build items falls back
    to the raw mesh objects.
    """
    meshes = [m for m in load_3mf_build_items(path) if m is not None]
    if not meshes:
        meshes = _load_raw_mesh_objects(path)
    if not meshes:
        raise ValueError(f"no mesh objects found in 3mf: {path}")
    return trimesh.util.concatenate(meshes)


def _load_raw_mesh_objects(path: Path) -> list[trimesh.Trimesh]:
    import lib3mf

    wrapper = lib3mf.get_wrapper()
    model = wrapper.CreateModel()
    model.QueryReader("3mf").ReadFromFile(str(path))
    factor = _LIB3MF_UNIT_TO_MM.get(int(model.GetUnit()), 1.0)
    meshes: list[trimesh.Trimesh] = []
    iterator = model.GetMeshObjects()
    while iterator.MoveNext():
        mesh_object = iterator.GetCurrentMeshObject()
        vertices = np.array([v.Coordinates for v in mesh_object.GetVertices()], dtype=np.float64)
        triangles = np.array([t.Indices for t in mesh_object.GetTriangleIndices()], dtype=np.int64)
        if len(vertices) == 0 or len(triangles) == 0:
            continue
        mesh = trimesh.Trimesh(vertices=vertices, faces=triangles)
        if factor != 1.0:
            mesh.apply_scale(factor)
        meshes.append(mesh)
    return meshes
