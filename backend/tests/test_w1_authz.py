"""Wave-1 authz + login hardening: login throttle, upload ownership, imports /
sensors permissions, catalog search escaping, upload enqueue failure."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import File, Job
from app.models.inventory import ColorCatalogEntry
from app.services import login_throttle
from app.tasks.ingest import store_to_backend
from tests.auth_fixtures import LoginAs, MakeUser
from tests.test_auth import PASSWORD, USERNAME

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def test_login_throttle_429_after_failures(
    client: httpx.AsyncClient, admin_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(login_throttle, "USER_LIMIT", 3)
    for _ in range(3):
        r = await client.post("/api/auth/login", json={"username": USERNAME, "password": "nope"})
        assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0


async def test_login_success_clears_user_counter(
    client: httpx.AsyncClient, admin_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(login_throttle, "USER_LIMIT", 3)
    for _ in range(2):
        await client.post("/api/auth/login", json={"username": USERNAME, "password": "nope"})
    ok = await client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert ok.status_code == 204
    for _ in range(2):
        r = await client.post("/api/auth/login", json={"username": USERNAME, "password": "nope"})
        assert r.status_code == 401


async def test_login_throttle_per_ip(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(login_throttle, "IP_LIMIT", 2)
    for name in ("a", "b"):
        r = await client.post("/api/auth/login", json={"username": name, "password": "x"})
        assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"username": "c", "password": "x"})
    assert r.status_code == 429


async def _admin_model(admin: httpx.AsyncClient) -> tuple[dict, int]:
    r = await admin.post("/api/models", json={"name": "Owned"})
    assert r.status_code == 201, r.text
    body = r.json()
    return body, body["current_revision"]["id"]


async def test_upload_by_non_owner_is_403(
    authenticated_client: httpx.AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    model, rev = await _admin_model(authenticated_client)
    other = await login_as(await make_user("member"))
    r = await other.put(
        "/api/uploads",
        params={"model_id": model["id"], "revision_id": rev, "rel_path": "a.stl"},
        content=b"abc",
    )
    assert r.status_code == 403


async def test_upload_enqueue_failure_drops_file_and_fails_job(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, rev = await _admin_model(authenticated_client)

    def boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(store_to_backend, "apply_async", boom)
    r = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": model["id"], "revision_id": rev, "rel_path": "a.stl"},
        content=b"abc",
    )
    assert r.status_code == 503
    db_session.expire_all()
    assert (await db_session.scalars(select(File).where(File.rel_path == "a.stl"))).first() is None
    jobs = (await db_session.scalars(select(Job).where(Job.type == "store_to_backend"))).all()
    assert [j.state for j in jobs] == ["failed"]


@pytest.mark.parametrize(
    "path",
    ["/api/imports/search?q=x", "/api/imports/lists", "/api/imports/lists/printables/1/items"],
)
async def test_imports_browse_requires_library_write(
    make_user: MakeUser, login_as: LoginAs, path: str
) -> None:
    viewer = await login_as(await make_user("viewer"))
    assert (await viewer.get(path)).status_code == 403


async def test_sensor_entities_requires_settings_write(
    make_user: MakeUser, login_as: LoginAs
) -> None:
    member = await login_as(await make_user("member"))
    r = await member.get("/api/storage-locations/sensors/entities")
    assert r.status_code == 403


async def test_catalog_search_escapes_like_and_limits(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add_all(
        [
            ColorCatalogEntry(manufacturer="ZZ", color_name="100% Red", hex="#ff0000"),
            ColorCatalogEntry(manufacturer="ZZ", color_name="1000 Blue", hex="#0000ff"),
        ]
    )
    await db_session.commit()
    r = await authenticated_client.get("/api/color-catalog", params={"q": "100%"})
    assert [e["color_name"] for e in r.json()] == ["100% Red"]
    r = await authenticated_client.get(
        "/api/color-catalog", params={"manufacturer": "zz", "limit": 1}
    )
    assert len(r.json()) == 1
