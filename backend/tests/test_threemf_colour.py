"""Filament colouring of Bambu/Orca project 3MFs (parts, modifiers, colour changes)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import trimesh

from app.models.enums import BlobFormat
from app.pipeline import convert, plates3mf, threemf_colour
from tests.threemf_builder import make_coloured_project_3mf, make_project_3mf

RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)


def _setup(tmp_path: Path):
    path = tmp_path / "c.3mf"
    path.write_bytes(make_coloured_project_3mf())
    with zipfile.ZipFile(path) as zf:
        plates = plates3mf.parse_unsliced_plates(zf)
        bed = plates3mf.read_bed(zf)
    return path, plates, bed


def _by_colour(mesh: trimesh.Trimesh) -> dict[tuple, np.ndarray]:
    fc = np.asarray(mesh.visual.face_colors)
    return {
        tuple(int(x) for x in c): mesh.triangles_center[(fc == c).all(axis=1)]
        for c in np.unique(fc, axis=0)
    }


def test_read_colour_info(tmp_path: Path) -> None:
    path, _, _ = _setup(tmp_path)
    with zipfile.ZipFile(path) as zf:
        info = threemf_colour.read_colour_info(zf)
    assert info is not None and info.is_multicolour
    assert info.changes == {2: [(10.0, BLUE)]}
    assert info.part_rgba(1, 0) == RED
    assert info.part_rgba(1, 1) == BLUE
    assert info.part_rgba(1, 2) is None  # modifier


def test_plain_project_not_read_as_coloured(tmp_path: Path) -> None:
    p = tmp_path / "plain.3mf"
    p.write_bytes(make_project_3mf(2))
    with zipfile.ZipFile(p) as zf:
        assert threemf_colour.read_colour_info(zf) is None
    assert plates3mf.whole_file_mesh(p) is None


def test_plate_parts_coloured_and_modifier_excluded(tmp_path: Path) -> None:
    path, plates, bed = _setup(tmp_path)
    meshes = plates3mf.plate_meshes(path, plates, bed)
    m1 = meshes[1]
    assert len(m1.faces) == 24  # modifier cube dropped
    cols = _by_colour(m1)
    assert set(cols) == {RED, BLUE}
    assert cols[RED][:, 0].max() < cols[BLUE][:, 0].min()  # part A left of part B
    assert m1.bounds[1][0] - m1.bounds[0][0] < 50  # modifier (x +50) not in extents


def test_colour_change_splits_at_z(tmp_path: Path) -> None:
    path, plates, bed = _setup(tmp_path)
    m2 = plates3mf.plate_meshes(path, plates, bed)[2]
    cols = _by_colour(m2)
    assert set(cols) == {GREEN, BLUE}
    assert cols[GREEN][:, 2].max() <= 10 < cols[BLUE][:, 2].min()
    # plate 1 has no change entry: unaffected by plate 2's change
    assert BLUE in _by_colour(plates3mf.plate_meshes(path, plates, bed)[1])


def test_colours_survive_glb_export(tmp_path: Path) -> None:
    path, plates, bed = _setup(tmp_path)
    m2 = plates3mf.plate_meshes(path, plates, bed)[2]
    out = tmp_path / "p2.glb"
    convert.to_y_up(m2).export(out, file_type="glb")
    g = trimesh.load(out, force="mesh")
    assert g.visual.kind == "vertex"
    got = {tuple(int(x) for x in c) for c in np.unique(g.visual.vertex_colors, axis=0)}
    assert got == {GREEN, BLUE}
    assert abs(g.bounds[1][1] - 20) < 1e-6  # Y-up


def test_whole_file_glb_coloured(tmp_path: Path) -> None:
    path, _, _ = _setup(tmp_path)
    dst = tmp_path / "whole.glb"
    convert.convert_to_glb_file(path, BlobFormat.THREEMF, dst)
    g = trimesh.load(dst, force="mesh")
    got = {tuple(int(x) for x in c) for c in np.unique(g.visual.vertex_colors, axis=0)}
    assert got == {RED, GREEN, BLUE}
    assert len(g.faces) == 36  # 3 display cubes, modifier excluded


def test_bake_does_not_blend_shared_vertices() -> None:
    box = trimesh.creation.box((1, 1, 1))
    fc = np.tile(np.array(RED, dtype=np.uint8), (12, 1))
    fc[:6] = BLUE
    box.visual = trimesh.visual.ColorVisuals(box, face_colors=fc)
    baked = threemf_colour.bake_vertex_colours(box)
    assert {tuple(c) for c in np.unique(baked.visual.vertex_colors, axis=0)} == {RED, BLUE}
    assert len(baked.faces) == 12
