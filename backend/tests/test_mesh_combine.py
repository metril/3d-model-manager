"""Layout math for combining meshes onto one plate, and ``POST /models/{slug}/combine``."""

from __future__ import annotations

import io
import re
import zipfile

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SlicerProfile
from app.models.enums import ProfileKind
from app.services.bed_fit import parse_printable_area
from app.services.mesh_combine import (
    LoadedPart,
    Pose,
    check_poses,
    plan_layout,
    pose_half_extents,
    pose_transform,
)
from tests.corpus import box_stl

# -- pure layout math (no fixtures) -----------------------------------------


def _inside(plan, bed) -> bool:
    return all(
        p.x >= -1e-6 and p.y >= -1e-6 and p.x + p.w <= bed[0] + 1e-6 and p.y + p.d <= bed[1] + 1e-6
        for p in plan.placements
    )


def _overlap(a, b) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.d and b.y < a.y + a.d


@pytest.mark.parametrize("mode", ["grid", "auto"])
def test_layout_fits_inside_bed_without_overlap(mode) -> None:
    plan = plan_layout([(20.0, 10.0)] * 6, [0] * 6, (100.0, 60.0), mode=mode, spacing=5.0)
    assert plan.fits
    assert len(plan.placements) == 6
    assert _inside(plan, (100.0, 60.0))
    for i, a in enumerate(plan.placements):
        for b in plan.placements[i + 1 :]:
            assert not _overlap(a, b)


@pytest.mark.parametrize("mode", ["grid", "auto"])
def test_layout_reports_required_bed_when_too_many(mode) -> None:
    plan = plan_layout([(40.0, 40.0)] * 9, [0] * 9, (100.0, 100.0), mode=mode, spacing=5.0)
    assert not plan.fits
    assert plan.required_bed[0] > 100.0 or plan.required_bed[1] > 100.0


def test_layout_part_wider_than_bed_does_not_fit() -> None:
    plan = plan_layout([(300.0, 10.0)], [0], (180.0, 180.0), mode="auto")
    assert not plan.fits
    assert plan.required_bed[0] >= 300.0


def test_grid_cell_uses_largest_footprint() -> None:
    plan = plan_layout(
        [(10.0, 10.0), (30.0, 20.0)], [0, 1], (100.0, 100.0), mode="grid", spacing=5.0
    )
    assert plan.fits
    a, b = plan.placements
    assert b.x - a.x >= 35.0 - 1e-6 or b.y - a.y >= 25.0 - 1e-6


def test_parse_printable_area() -> None:
    assert parse_printable_area({"printable_area": ["0x0", "180x0", "180x180", "0x180"]}) == (
        180.0,
        180.0,
    )
    assert parse_printable_area({"printable_area": ["-10x0", "250x0", "250x210", "-10x210"]}) == (
        260.0,
        210.0,
    )
    assert parse_printable_area({}) is None


def _apply(tf: str, pt: tuple[float, float, float]) -> tuple[float, float, float]:
    m = [float(v) for v in tf.split()]
    x, y, z = pt
    return (
        x * m[0] + y * m[3] + z * m[6] + m[9],
        x * m[1] + y * m[4] + z * m[7] + m[10],
        x * m[2] + y * m[5] + z * m[8] + m[11],
    )


def _part(w=20.0, d=10.0) -> LoadedPart:
    return LoadedPart("p", None, None, w, d, 1, height=5.0)


def test_pose_transform_pivots_on_footprint_centre() -> None:
    part = _part()
    for rot, scale in [(0, 1), (90, 1), (37, 1.5), (-45, 0.5)]:
        tf = pose_transform(part, Pose(0, 100.0, 80.0, rot, scale))
        # Footprint centre (shifted frame: w/2, d/2, 0) lands on the pose x/y, z stays 0.
        cx, cy, cz = _apply(tf, (10.0, 5.0, 0.0))
        assert (cx, cy, cz) == pytest.approx((100.0, 80.0, 0.0), abs=1e-4)
    # 90 deg: the +x corner of the footprint moves to +y of the centre.
    tf = pose_transform(part, Pose(0, 0.0, 0.0, 90, 1))
    x, y, _ = _apply(tf, (20.0, 5.0, 0.0))
    assert (x, y) == pytest.approx((0.0, 10.0), abs=1e-4)
    # Scale is applied to z too.
    assert round(_apply(pose_transform(part, Pose(0, 0, 0, 0, 2)), (0, 0, 5.0))[2], 6) == 10.0


def test_pose_half_extents_and_bed_check() -> None:
    hx, hy = pose_half_extents(20.0, 10.0, 90.0, 1.0)
    assert (round(hx, 6), round(hy, 6)) == (5.0, 10.0)
    hx, hy = pose_half_extents(20.0, 10.0, 45.0, 2.0)
    assert round(hx, 4) == round(hy, 4) == round(2 * 30 * 0.70710678 / 2, 4)
    part = _part()
    fits = check_poses(
        [part],
        [Pose(0, 50, 50), Pose(0, 5, 50), Pose(0, 50, 50, 90, 1), Pose(0, 95, 5, 90, 1)],
        (100.0, 100.0),
    )
    assert [f.fits for f in fits] == [True, False, True, False]
    assert (fits[2].min_x, fits[2].max_y) == (45.0, 60.0)


# -- endpoint ---------------------------------------------------------------

pytestmark_api = pytest.mark.usefixtures("library_root", "data_dir")


async def _model_with_stl(client: httpx.AsyncClient, name: str) -> tuple[dict, int]:
    created = (await client.post("/api/models", json={"name": name})).json()
    response = await client.put(
        "/api/uploads",
        params={
            "model_id": created["id"],
            "revision_id": created["current_revision"]["id"],
            "rel_path": "box.stl",
        },
        content=box_stl(),
    )
    assert response.status_code == 201, response.text
    return created, response.json()["file_id"]


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_combine_creates_multi_object_3mf(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    created, file_id = await _model_with_stl(authenticated_client, "Combine Target")
    profile = SlicerProfile(
        name="Test A1 mini",
        kind=ProfileKind.PRINTER,
        data={"printable_area": ["0x0", "180x0", "180x180", "0x180"]},
    )
    db_session.add(profile)
    await db_session.commit()

    response = await authenticated_client.post(
        f"/api/models/{created['slug']}/combine",
        json={
            "items": [{"file_id": file_id, "copies": 4}],
            "printer_profile_id": profile.id,
            "layout": "grid",
            "spacing_mm": 5,
            "name": "plate",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fits"] is True
    assert len(body["placements"]) == 4
    assert body["file"]["rel_path"] == "plate.3mf"
    assert body["file"]["verified_at"] is not None

    # The stored file is a 3MF with one object and four build items.
    download = await authenticated_client.get(f"/api/files/{body['file']['id']}/download")
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        xml = zf.read("3D/3dmodel.model").decode()
    assert len(re.findall(r"<object ", xml)) == 1
    assert len(re.findall(r"<item ", xml)) == 4

    # A second combine with the same name does not clash.
    again = await authenticated_client.post(
        f"/api/models/{created['slug']}/combine",
        json={
            "items": [{"file_id": file_id, "copies": 2}],
            "bed": {"x_mm": 100, "y_mm": 100},
            "name": "plate",
        },
    )
    assert again.status_code == 200, again.text
    assert again.json()["file"]["rel_path"] == "plate (2).3mf"


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_combine_rejects_when_not_fitting(authenticated_client: httpx.AsyncClient) -> None:
    created, file_id = await _model_with_stl(authenticated_client, "Too Big")
    response = await authenticated_client.post(
        f"/api/models/{created['slug']}/combine",
        json={"items": [{"file_id": file_id, "copies": 20}], "bed": {"x_mm": 50, "y_mm": 50}},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "required_bed" in detail

    dry = await authenticated_client.post(
        f"/api/models/{created['slug']}/combine",
        json={
            "items": [{"file_id": file_id, "copies": 20}],
            "bed": {"x_mm": 50, "y_mm": 50},
            "dry_run": True,
        },
    )
    assert dry.status_code == 200
    assert dry.json()["fits"] is False
    assert dry.json()["file"] is None


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_combine_enqueue_failure_rolls_back(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch
) -> None:
    from sqlalchemy import select

    from app.api import combine as combine_api
    from app.models import File

    created, file_id = await _model_with_stl(authenticated_client, "Broker Down")

    def boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(combine_api.store_to_backend, "apply_async", boom)
    response = await authenticated_client.post(
        f"/api/models/{created['slug']}/combine",
        json={"items": [{"file_id": file_id}], "bed": {"x_mm": 100, "y_mm": 100}, "name": "plate"},
    )
    assert response.status_code == 503
    paths = (await db_session.execute(select(File.rel_path))).scalars().all()
    assert "plate.3mf" not in paths


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_combine_requires_bed_and_known_file(authenticated_client: httpx.AsyncClient) -> None:
    created, file_id = await _model_with_stl(authenticated_client, "Validation")
    slug = created["slug"]
    no_bed = await authenticated_client.post(
        f"/api/models/{slug}/combine", json={"items": [{"file_id": file_id}]}
    )
    assert no_bed.status_code == 422
    missing = await authenticated_client.post(
        f"/api/models/{slug}/combine",
        json={"items": [{"file_id": 999999}], "bed": {"x_mm": 100, "y_mm": 100}},
    )
    assert missing.status_code == 404


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_combine_manual_writes_transforms(authenticated_client: httpx.AsyncClient) -> None:
    created, file_id = await _model_with_stl(authenticated_client, "Manual")
    slug = created["slug"]
    items = [
        {"file_id": file_id, "x_mm": 50, "y_mm": 40, "rot_z_deg": 90, "scale": 2, "copy_index": 0},
        {"file_id": file_id, "x_mm": 120, "y_mm": 100, "copy_index": 1},
        {"file_id": file_id, "x_mm": 120, "y_mm": 30, "copy_index": 2},
    ]
    body = {"items": items, "layout": "manual", "bed": {"x_mm": 180, "y_mm": 180}, "name": "hand"}
    dry = await authenticated_client.post(
        f"/api/models/{slug}/combine", json={**body, "dry_run": True}
    )
    assert dry.status_code == 200, dry.text
    out = dry.json()
    assert out["fits"] is True and out["file"] is None
    assert [p["fits"] for p in out["poses"]] == [True, True, True]
    assert out["parts"][0]["w_mm"] == pytest.approx(20.0)
    assert out["parts"][0]["h_mm"] == pytest.approx(5.0)
    # 90deg x2: footprint 20x10 -> 20 (y) x 40 (x) around (50, 40)
    assert out["placements"][0]["w_mm"] == pytest.approx(20.0)
    assert out["placements"][0]["d_mm"] == pytest.approx(40.0)

    saved = await authenticated_client.post(f"/api/models/{slug}/combine", json=body)
    assert saved.status_code == 200, saved.text
    file = saved.json()["file"]
    assert file["rel_path"] == "hand.3mf"
    download = await authenticated_client.get(f"/api/files/{file['id']}/download")
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        xml = zf.read("3D/3dmodel.model").decode()
    assert len(re.findall(r"<object ", xml)) == 1
    transforms = re.findall(r'<item objectid="1" transform="([^"]+)"', xml)
    assert len(transforms) == 3
    first = [float(v) for v in transforms[0].split()]
    assert first[:9] == pytest.approx([0, 2, 0, -2, 0, 0, 0, 0, 2], abs=1e-6)
    # pivot (10, 5) -> (50, 40): t = (50 - (0*10 - 2*5), 40 - (2*10 + 0)) = (60, 20)
    assert first[9:] == pytest.approx([60, 20, 0], abs=1e-6)
    assert [float(v) for v in transforms[1].split()][9:] == pytest.approx([110, 95, 0])


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_combine_manual_422_lists_offending_items(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created, file_id = await _model_with_stl(authenticated_client, "Manual Off")
    slug = created["slug"]
    items = [
        {"file_id": file_id, "x_mm": 50, "y_mm": 50},
        {"file_id": file_id, "x_mm": 95, "y_mm": 50},
        {"file_id": file_id, "x_mm": 50, "y_mm": 3},
    ]
    body = {"items": items, "layout": "manual", "bed": {"x_mm": 100, "y_mm": 100}}
    response = await authenticated_client.post(f"/api/models/{slug}/combine", json=body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "does_not_fit"
    assert detail["items"] == [1, 2]
    dry = await authenticated_client.post(
        f"/api/models/{slug}/combine", json={**body, "dry_run": True}
    )
    assert dry.status_code == 200
    assert [p["fits"] for p in dry.json()["poses"]] == [True, False, False]
    missing_pose = await authenticated_client.post(
        f"/api/models/{slug}/combine",
        json={"items": [{"file_id": file_id}], "layout": "manual", "bed": body["bed"]},
    )
    assert missing_pose.status_code == 422


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_combine_auto_returns_poses_without_writing(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created, file_id = await _model_with_stl(authenticated_client, "Auto Poses")
    slug = created["slug"]
    response = await authenticated_client.post(
        f"/api/models/{slug}/combine",
        params={"auto": 1},
        json={"items": [{"file_id": file_id, "copies": 3}], "bed": {"x_mm": 100, "y_mm": 100}},
    )
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["file"] is None and out["fits"] is True
    assert [p["copy_index"] for p in out["poses"]] == [0, 1, 2]
    for pose, rect in zip(out["poses"], out["placements"], strict=True):
        assert pose["x_mm"] == pytest.approx(rect["x_mm"] + rect["w_mm"] / 2)
        assert pose["y_mm"] == pytest.approx(rect["y_mm"] + rect["d_mm"] / 2)


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_combine_height_limit_and_scaled_auto(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    created, file_id = await _model_with_stl(authenticated_client, "Tall")
    slug = created["slug"]
    profile = SlicerProfile(
        name="Short printer",
        kind=ProfileKind.PRINTER,
        data={
            "printable_area": ["0x0", "180x0", "180x180", "0x180"],
            "printable_height": 8,
        },
    )
    db_session.add(profile)
    await db_session.commit()
    base = {"layout": "manual", "printer_profile_id": profile.id}
    # The box is 5 mm tall: scale 1 fits, scale 2 (10 mm) exceeds the 8 mm limit.
    items = [
        {"file_id": file_id, "x_mm": 40, "y_mm": 40},
        {"file_id": file_id, "x_mm": 100, "y_mm": 100, "scale": 2},
    ]
    dry = await authenticated_client.post(
        f"/api/models/{slug}/combine", json={**base, "items": items, "dry_run": True}
    )
    assert dry.status_code == 200, dry.text
    assert [p["fits"] for p in dry.json()["poses"]] == [True, False]
    saved = await authenticated_client.post(
        f"/api/models/{slug}/combine", json={**base, "items": items}
    )
    assert saved.status_code == 422
    assert saved.json()["detail"]["items"] == [1]

    # Auto uses the scaled footprints (no overlap) and flags too-tall parts.
    auto = await authenticated_client.post(
        f"/api/models/{slug}/combine",
        params={"auto": 1},
        json={
            "items": [{"file_id": file_id, "copies": 3, "scale": 2}],
            "printer_profile_id": profile.id,
            "spacing_mm": 5,
        },
    )
    assert auto.status_code == 200, auto.text
    out = auto.json()
    assert out["fits"] is False  # 10 mm tall > 8 mm
    assert all(p["scale"] == 2 for p in out["poses"])
    assert all(r["w_mm"] == pytest.approx(40.0) for r in out["placements"])
    rects = out["placements"]
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            assert (
                a["x_mm"] + a["w_mm"] <= b["x_mm"] + 1e-6
                or b["x_mm"] + b["w_mm"] <= a["x_mm"] + 1e-6
                or a["y_mm"] + a["d_mm"] <= b["y_mm"] + 1e-6
                or b["y_mm"] + b["d_mm"] <= a["y_mm"] + 1e-6
            )
