"""Unsliced multi-plate project 3MF parsing + per-plate recentring."""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest

from app.pipeline import meshload, plates3mf
from tests.threemf_builder import make_project_3mf


def _write(tmp_path: Path, data: bytes) -> Path:
    p = tmp_path / "proj.3mf"
    p.write_bytes(data)
    return p


def _load(tmp_path: Path, **kw):
    path = _write(tmp_path, make_project_3mf(**kw))
    with zipfile.ZipFile(path) as zf:
        plates = plates3mf.parse_unsliced_plates(zf)
        bed = plates3mf.read_bed(zf)
    return path, plates, bed


def test_parse_three_plates(tmp_path: Path) -> None:
    _, plates, bed = _load(tmp_path, names=["Base", "", "Lid"])
    assert bed == (180.0, 180.0)
    assert [p["index"] for p in plates] == [1, 2, 3]
    assert [p["name"] for p in plates] == ["Base", "", "Lid"]
    assert [p["object_ids"] for p in plates] == [[1], [2], [3]]
    assert all(p["thumb_member"] for p in plates)


def test_plate_origin_grid_rows_go_negative_y() -> None:
    assert plates3mf.plate_origin(1, 3, (180, 180)) == (0, -0.0)
    assert plates3mf.plate_origin(2, 3, (180, 180)) == pytest.approx((216, 0))
    assert plates3mf.plate_origin(3, 3, (180, 180)) == pytest.approx((0, -216))


def test_plates_recentred_inside_bed(tmp_path: Path) -> None:
    path, plates, bed = _load(tmp_path)
    meshes = plates3mf.plate_meshes(path, plates, bed)
    assert set(meshes) == {1, 2, 3}
    for m in meshes.values():
        lo, hi = m.bounds
        assert (lo[:2] >= 0).all() and (hi[:2] <= 180).all()
        assert np.allclose((lo[:2] + hi[:2]) / 2, 90)
        assert np.allclose(m.extents, 20)


def test_unknown_bed_centres_on_default_bed(tmp_path: Path) -> None:
    path, plates, bed = _load(tmp_path, bed=None)
    assert bed is None
    meshes = plates3mf.plate_meshes(path, plates, bed)
    for m in meshes.values():
        lo, hi = m.bounds
        assert np.allclose((lo[:2] + hi[:2]) / 2, 128)


def test_off_bed_result_falls_back_to_bbox_centre(tmp_path: Path) -> None:
    path, plates, bed = _load(tmp_path, on_grid=False)
    meshes = plates3mf.plate_meshes(path, plates, bed)
    for m in meshes.values():
        lo, hi = m.bounds
        assert np.allclose((lo[:2] + hi[:2]) / 2, 90)


def test_unassigned_object_goes_on_plate_one(tmp_path: Path, caplog) -> None:
    path, plates, bed = _load(tmp_path, extra_unassigned=True)
    with caplog.at_level("INFO"):
        meshes = plates3mf.plate_meshes(path, plates, bed)
    assert "on no plate" in caplog.text
    assert len(meshes[1].faces) == 24 and len(meshes[2].faces) == 12


def test_single_plate_and_no_config(tmp_path: Path) -> None:
    _, plates, _ = _load(tmp_path, n_plates=1)
    assert len(plates) == 1
    p = tmp_path / "bare.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
    with zipfile.ZipFile(p) as zf:
        assert plates3mf.parse_unsliced_plates(zf) == []
        assert plates3mf.read_bed(zf) is None


def test_lib3mf_applies_build_transforms(tmp_path: Path) -> None:
    path = _write(tmp_path, make_project_3mf(3))
    mesh = meshload.load_3mf_lib3mf(path)
    # transforms applied: three cubes spread across the virtual grid, not stacked at origin
    assert mesh.extents[0] > 200 and mesh.extents[1] > 200
    items = meshload.load_3mf_build_items(path)
    assert len(items) == 3 and all(len(i.faces) == 12 for i in items)
