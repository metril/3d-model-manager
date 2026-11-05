"""Per-object splitting of multi-object 3MFs for the plate editor."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.pipeline import plates3mf
from app.services.mesh_combine import CombinePart, MeshCombineError, Pose, load_parts, write_3mf
from tests.threemf_builder import make_project_3mf


def _project(tmp_path: Path, n: int = 3) -> Path:
    p = tmp_path / "proj.3mf"
    p.write_bytes(make_project_3mf(n))
    return p


def test_object_meshes_one_per_build_item_on_single_bed(tmp_path: Path) -> None:
    pieces = plates3mf.object_meshes(_project(tmp_path))
    assert [p.index for p in pieces] == [0, 1, 2]
    assert [p.plate for p in pieces] == [1, 2, 3]
    for p in pieces:
        lo, hi = p.mesh.bounds
        assert (lo[:2] >= 0).all() and (hi[:2] <= 180).all()
        assert np.allclose(p.mesh.extents, 20)
        assert np.allclose((lo[:2] + hi[:2]) / 2, 90)


def test_single_object_project_is_not_split(tmp_path: Path) -> None:
    assert plates3mf.object_meshes(_project(tmp_path, 1)) == []


def test_combine_part_with_object_uses_that_object_only(tmp_path: Path) -> None:
    path = _project(tmp_path)
    whole = load_parts([CombinePart(name="p.3mf", path=path)])[0]
    obj = load_parts([CombinePart(name="p.3mf", path=path, object=1)])[0]
    assert whole.width > 200  # spans the virtual plate grid
    assert (obj.width, obj.depth, obj.height) == pytest.approx((20, 20, 20))
    assert obj.origin[:2] == pytest.approx((80, 80))


def test_per_object_orient_is_baked(tmp_path: Path) -> None:
    path = _project(tmp_path)
    # 90 degrees about X on a cube keeps dims; use a tall check via scale-free bounds
    s = 2**-0.5
    tilted = load_parts([CombinePart(name="p", path=path, object=2, orient=(s, 0, 0, s))])[0]
    assert tilted.orient == pytest.approx((s, 0, 0, s))
    assert (tilted.width, tilted.depth, tilted.height) == pytest.approx((20, 20, 20))
    assert write_3mf([tilted], poses=[Pose(0, 50, 50)])[:2] == b"PK"


def test_missing_object_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(MeshCombineError):
        load_parts([CombinePart(name="p.3mf", path=_project(tmp_path), object=9)])


async def _upload(client: httpx.AsyncClient, name: str, data: bytes) -> tuple[dict, int]:
    created = (await client.post("/api/models", json={"name": name})).json()
    r = await client.put(
        "/api/uploads",
        params={
            "model_id": created["id"],
            "revision_id": created["current_revision"]["id"],
            "rel_path": "proj.3mf",
        },
        content=data,
    )
    assert r.status_code == 201, r.text
    return created, r.json()["file_id"]


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_objects_endpoint_glb_and_manual_combine(
    authenticated_client: httpx.AsyncClient,
) -> None:
    c = authenticated_client
    created, fid = await _upload(c, "Objects EP", make_project_3mf(2))
    slug = created["slug"]

    r = await c.get(f"/api/models/{slug}/files/{fid}/objects")
    assert r.status_code == 200, r.text
    objs = r.json()["objects"]
    assert [o["index"] for o in objs] == [0, 1]
    assert [o["plate"] for o in objs] == [1, 2]
    assert objs[0]["x_mm"] == pytest.approx(90) and objs[0]["w_mm"] == pytest.approx(20)
    glb = await c.get(objs[1]["glb_url"].removeprefix(""))
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"
    # Second call is served from the cache.
    assert (await c.get(f"/api/models/{slug}/files/{fid}/objects")).json()["objects"] == objs

    items = [
        {"file_id": fid, "object_index": 0, "x_mm": 40, "y_mm": 40},
        {
            "file_id": fid,
            "object_index": 1,
            "x_mm": 120,
            "y_mm": 40,
            "orient": [0.7071068, 0, 0, 0.7071068],
        },
    ]
    r = await c.post(
        f"/api/models/{slug}/combine",
        json={
            "items": items,
            "layout": "manual",
            "bed": {"x_mm": 180, "y_mm": 180},
            "name": "split",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fits"] is True
    assert [p["object_index"] for p in body["poses"]] == [0, 1]
    assert [p["object_index"] for p in body["parts"]] == [0, 1]
    dl = await c.get(f"/api/files/{body['file']['id']}/download")
    with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
        xml = zf.read("3D/3dmodel.model").decode()
    assert len(re.findall(r"<object ", xml)) == 2  # one per (object, orient)
    assert len(re.findall(r"<item ", xml)) == 2

    # Legacy item (no object_index) is the whole file: spans both plates -> off the bed.
    legacy = await c.post(
        f"/api/models/{slug}/combine?auto=1",
        json={"items": [{"file_id": fid}], "layout": "auto", "bed": {"x_mm": 180, "y_mm": 180}},
    )
    assert legacy.status_code == 200
    assert legacy.json()["poses"][0]["object_index"] is None
    assert legacy.json()["fits"] is False

    # Auto-arrange per object, and per-object orient.
    auto = await c.post(
        f"/api/models/{slug}/combine?auto=1",
        json={
            "items": [{"file_id": fid, "object_index": 0}, {"file_id": fid, "object_index": 1}],
            "layout": "auto",
            "bed": {"x_mm": 180, "y_mm": 180},
        },
    )
    assert auto.json()["fits"] is True
    o = await c.post(
        f"/api/models/{slug}/orient",
        json={"targets": [{"file_id": fid, "object_index": 1}]},
    )
    assert o.status_code == 200, o.text
    assert o.json()["files"][0]["object_index"] == 1


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_objects_endpoint_empty_for_single_object(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created, fid = await _upload(authenticated_client, "Single", make_project_3mf(1))
    r = await authenticated_client.get(f"/api/models/{created['slug']}/files/{fid}/objects")
    assert r.status_code == 200 and r.json()["objects"] == []
    bad = await authenticated_client.post(f"/api/models/{created['slug']}/orient", json={})
    assert bad.status_code == 422
