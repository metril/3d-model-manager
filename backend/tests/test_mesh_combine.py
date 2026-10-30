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
from app.services.mesh_combine import parse_printable_area, plan_layout
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
