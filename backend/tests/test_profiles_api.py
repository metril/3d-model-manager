"""Tests for `app.api.profiles`: profiles/recipes CRUD, bundle import, and
sidecar health (P6 Task 4)."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SlicerProfile
from app.models.enums import ProfileKind
from tests.slicer_fixtures import write_bundle_zip

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def _seed_profile(
    db_session: AsyncSession, *, kind: ProfileKind, name: str, printer_model: str | None = None
) -> int:
    profile = SlicerProfile(name=name, kind=kind, printer_model=printer_model, data={"name": name})
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile.id


# ---------------------------------------------------------------------------
# Profiles CRUD
# ---------------------------------------------------------------------------


async def test_profile_crud_round_trip(authenticated_client: httpx.AsyncClient):
    r = await authenticated_client.post(
        "/api/profiles",
        json={"name": "My printer", "kind": "printer", "data": {"printer_model": "A1 mini"}},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    profile_id = body["id"]
    assert body["data"] == {"printer_model": "A1 mini"}

    r = await authenticated_client.get("/api/profiles")
    assert r.status_code == 200
    rows = {row["id"]: row for row in r.json()}
    assert rows[profile_id]["data"] is None

    r = await authenticated_client.get("/api/profiles?full=1")
    rows = {row["id"]: row for row in r.json()}
    assert rows[profile_id]["data"] == {"printer_model": "A1 mini"}

    r = await authenticated_client.get(f"/api/profiles/{profile_id}")
    assert r.status_code == 200
    assert r.json()["data"] == {"printer_model": "A1 mini"}

    r = await authenticated_client.patch(f"/api/profiles/{profile_id}", json={"name": "Renamed"})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"

    r = await authenticated_client.delete(f"/api/profiles/{profile_id}")
    assert r.status_code == 204

    r = await authenticated_client.get(f"/api/profiles/{profile_id}")
    assert r.status_code == 404


async def test_profile_kind_filter(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    await _seed_profile(db_session, kind=ProfileKind.PRINTER, name="P1")
    await _seed_profile(db_session, kind=ProfileKind.FILAMENT, name="F1")

    r = await authenticated_client.get("/api/profiles?kind=filament")
    assert r.status_code == 200
    names = {row["name"] for row in r.json()}
    assert names == {"F1"}


# ---------------------------------------------------------------------------
# Recipe validation matrix
# ---------------------------------------------------------------------------


async def test_recipe_create_round_trip(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    printer_id = await _seed_profile(db_session, kind=ProfileKind.PRINTER, name="P1")
    process_id = await _seed_profile(db_session, kind=ProfileKind.PROCESS, name="Pr1")
    filament_id = await _seed_profile(db_session, kind=ProfileKind.FILAMENT, name="F1")

    r = await authenticated_client.post(
        "/api/recipes",
        json={
            "name": "Recipe 1",
            "printer_profile_id": printer_id,
            "process_profile_id": process_id,
            "filament_profile_ids": [filament_id, None],
            "bed_type": "textured_pei",
        },
    )
    assert r.status_code == 201, r.text
    recipe_id = r.json()["id"]

    r = await authenticated_client.get(f"/api/recipes/{recipe_id}")
    assert r.status_code == 200
    assert r.json()["name"] == "Recipe 1"

    r = await authenticated_client.patch(f"/api/recipes/{recipe_id}", json={"name": "Renamed"})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"

    r = await authenticated_client.delete(f"/api/recipes/{recipe_id}")
    assert r.status_code == 204


async def test_recipe_wrong_printer_profile_kind_422(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    not_a_printer = await _seed_profile(db_session, kind=ProfileKind.PROCESS, name="Pr1")
    process_id = await _seed_profile(db_session, kind=ProfileKind.PROCESS, name="Pr2")

    r = await authenticated_client.post(
        "/api/recipes",
        json={
            "name": "Bad",
            "printer_profile_id": not_a_printer,
            "process_profile_id": process_id,
            "bed_type": "textured_pei",
        },
    )
    assert r.status_code == 422


async def test_recipe_wrong_process_profile_kind_422(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    printer_id = await _seed_profile(db_session, kind=ProfileKind.PRINTER, name="P1")
    not_a_process = await _seed_profile(db_session, kind=ProfileKind.PRINTER, name="P2")

    r = await authenticated_client.post(
        "/api/recipes",
        json={
            "name": "Bad",
            "printer_profile_id": printer_id,
            "process_profile_id": not_a_process,
            "bed_type": "textured_pei",
        },
    )
    assert r.status_code == 422


async def test_recipe_wrong_filament_kind_422(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    printer_id = await _seed_profile(db_session, kind=ProfileKind.PRINTER, name="P1")
    process_id = await _seed_profile(db_session, kind=ProfileKind.PROCESS, name="Pr1")
    not_a_filament = await _seed_profile(db_session, kind=ProfileKind.PRINTER, name="P2")

    r = await authenticated_client.post(
        "/api/recipes",
        json={
            "name": "Bad",
            "printer_profile_id": printer_id,
            "process_profile_id": process_id,
            "filament_profile_ids": [not_a_filament],
            "bed_type": "textured_pei",
        },
    )
    assert r.status_code == 422


async def test_recipe_first_n_requires_n_422(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    printer_id = await _seed_profile(db_session, kind=ProfileKind.PRINTER, name="P1")
    process_id = await _seed_profile(db_session, kind=ProfileKind.PROCESS, name="Pr1")

    r = await authenticated_client.post(
        "/api/recipes",
        json={
            "name": "Bad",
            "printer_profile_id": printer_id,
            "process_profile_id": process_id,
            "bed_type": "textured_pei",
            "fanout": "first_n",
        },
    )
    assert r.status_code == 422

    r = await authenticated_client.post(
        "/api/recipes",
        json={
            "name": "Good",
            "printer_profile_id": printer_id,
            "process_profile_id": process_id,
            "bed_type": "textured_pei",
            "fanout": "first_n",
            "n": 2,
        },
    )
    assert r.status_code == 201, r.text


async def test_recipe_target_printer_requires_access(
    member_client: httpx.AsyncClient, db_session: AsyncSession, printer_row
):
    printer_id = await _seed_profile(db_session, kind=ProfileKind.PRINTER, name="P1")
    process_id = await _seed_profile(db_session, kind=ProfileKind.PROCESS, name="Pr1")

    r = await member_client.post(
        "/api/recipes",
        json={
            "name": "Needs printer access",
            "printer_profile_id": printer_id,
            "process_profile_id": process_id,
            "bed_type": "textured_pei",
            "target_printer_id": printer_row.id,
        },
    )
    assert r.status_code == 403


async def test_recipe_delete_in_use_profile_409(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    printer_id = await _seed_profile(db_session, kind=ProfileKind.PRINTER, name="P1")
    process_id = await _seed_profile(db_session, kind=ProfileKind.PROCESS, name="Pr1")

    r = await authenticated_client.post(
        "/api/recipes",
        json={
            "name": "Recipe",
            "printer_profile_id": printer_id,
            "process_profile_id": process_id,
            "bed_type": "textured_pei",
        },
    )
    assert r.status_code == 201, r.text

    r = await authenticated_client.delete(f"/api/profiles/{printer_id}")
    assert r.status_code == 409

    r = await authenticated_client.delete(f"/api/profiles/{process_id}")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Bundle import
# ---------------------------------------------------------------------------


async def test_import_bundle_multipart(authenticated_client: httpx.AsyncClient, tmp_path):
    zip_path = write_bundle_zip(tmp_path / "bundle.zip", tmp_path / "tree")
    files = {"bundle": ("bundle.zip", zip_path.read_bytes(), "application/zip")}
    r = await authenticated_client.post(
        "/api/profiles/import", files=files, params={"printer_model": "Bambu Lab A1 mini"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] > 0
    assert body["updated"] == 0

    r = await authenticated_client.get("/api/profiles")
    names = {row["name"] for row in r.json()}
    assert "Bambu Lab A1 mini 0.4 nozzle" in names
    assert "Bambu Lab X1 Carbon 0.4 nozzle" not in names


async def test_import_path_forbidden_for_non_admin(member_client: httpx.AsyncClient, tmp_path):
    r = await member_client.post("/api/profiles/import", json={"path": str(tmp_path)})
    assert r.status_code == 403


async def test_import_path_requires_bbl_json(authenticated_client: httpx.AsyncClient, tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    r = await authenticated_client.post("/api/profiles/import", json={"path": str(empty_dir)})
    assert r.status_code == 422


async def test_import_path_admin_ok(authenticated_client: httpx.AsyncClient, tmp_path):
    from tests.slicer_fixtures import write_bundle

    root = write_bundle(tmp_path / "bundle")
    r = await authenticated_client.post("/api/profiles/import", json={"path": str(root)})
    assert r.status_code == 200, r.text
    assert r.json()["created"] > 0


async def test_import_multipart_over_size_cap_is_413(
    authenticated_client: httpx.AsyncClient, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    import app.api.profiles as profiles_api

    monkeypatch.setattr(profiles_api, "_MAX_UPLOAD_BYTES", 10)
    zip_path = write_bundle_zip(tmp_path / "bundle.zip", tmp_path / "tree")
    files = {"bundle": ("bundle.zip", zip_path.read_bytes(), "application/zip")}
    r = await authenticated_client.post("/api/profiles/import", files=files)
    assert r.status_code == 413


async def test_import_zip_too_many_members_is_422(
    authenticated_client: httpx.AsyncClient, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    from app.services import slicer_profiles

    monkeypatch.setattr(slicer_profiles, "_MAX_ZIP_MEMBERS", 2)
    zip_path = write_bundle_zip(tmp_path / "bundle.zip", tmp_path / "tree")
    files = {"bundle": ("bundle.zip", zip_path.read_bytes(), "application/zip")}
    r = await authenticated_client.post("/api/profiles/import", files=files)
    assert r.status_code == 422


async def test_import_zip_uncompressed_too_large_is_422(
    authenticated_client: httpx.AsyncClient, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    from app.services import slicer_profiles

    monkeypatch.setattr(slicer_profiles, "_MAX_ZIP_UNCOMPRESSED_BYTES", 4)
    zip_path = write_bundle_zip(tmp_path / "bundle.zip", tmp_path / "tree")
    files = {"bundle": ("bundle.zip", zip_path.read_bytes(), "application/zip")}
    r = await authenticated_client.post("/api/profiles/import", files=files)
    assert r.status_code == 422


async def test_import_malformed_json_body_is_422(authenticated_client: httpx.AsyncClient):
    r = await authenticated_client.post(
        "/api/profiles/import",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422


async def test_import_path_malformed_preset_json_is_422(
    authenticated_client: httpx.AsyncClient, tmp_path
):
    root = tmp_path / "broken-bundle"
    (root / "machine").mkdir(parents=True)
    (root / "BBL.json").write_text('{"name": "BBL"}')
    (root / "machine" / "broken.json").write_text("{not valid json")

    r = await authenticated_client.post("/api/profiles/import", json={"path": str(root)})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Sidecar health
# ---------------------------------------------------------------------------


async def test_health_unconfigured(authenticated_client: httpx.AsyncClient):
    r = await authenticated_client.get("/api/slicer/health")
    assert r.status_code == 200
    assert r.json() == {"configured": False, "ok": False, "version": None, "error": None}


async def test_health_up(authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from app.config import get_settings

    monkeypatch.setenv("TDMM_SLICER_URL", "http://slicer:3001")
    get_settings.cache_clear()
    try:
        with respx.mock:
            respx.get("http://slicer:3001/health").mock(
                return_value=httpx.Response(200, json={"version": "1.2.3", "status": "ok"})
            )
            r = await authenticated_client.get("/api/slicer/health")
    finally:
        get_settings.cache_clear()
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["ok"] is True
    assert body["version"] == "1.2.3"


async def test_health_down(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from app.config import get_settings

    monkeypatch.setenv("TDMM_SLICER_URL", "http://slicer:3001")
    get_settings.cache_clear()
    try:
        with respx.mock:
            respx.get("http://slicer:3001/health").mock(side_effect=httpx.ConnectError("refused"))
            r = await authenticated_client.get("/api/slicer/health")
    finally:
        get_settings.cache_clear()
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["ok"] is False
    assert body["error"]


async def test_health_non_object_body_is_502(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """M4: a 2xx body that isn't even a JSON object (here, a bare list) is
    a protocol violation, not a plain "unhealthy" report -- 502, not a 200
    with `ok: false`."""
    from app.config import get_settings

    monkeypatch.setenv("TDMM_SLICER_URL", "http://slicer:3001")
    get_settings.cache_clear()
    try:
        with respx.mock:
            respx.get("http://slicer:3001/health").mock(
                return_value=httpx.Response(200, json=[1, 2, 3])
            )
            r = await authenticated_client.get("/api/slicer/health")
    finally:
        get_settings.cache_clear()
    assert r.status_code == 502


async def test_profile_create_source_is_builtin(authenticated_client: httpx.AsyncClient):
    """M7: hand-entered profiles are tagged `source: builtin` (renamed
    from `manual` to match the spec's own wording)."""
    r = await authenticated_client.post(
        "/api/profiles", json={"name": "Hand entered", "kind": "process", "data": {}}
    )
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "builtin"
