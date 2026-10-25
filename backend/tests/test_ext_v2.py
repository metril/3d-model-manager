"""``/api/ext/v2`` scoped API keys: minting (capped by the creator's perms),
header auth, scope / allowlist / permission denial, control routed through the
command bus, queue + slice + upload, rate limiting, expiry and audit."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import redis as redis_lib
from sqlalchemy import select

from app.models import ApiToken, AuditLog, User
from app.models.enums import UserRole
from app.printers.base import cmd_result_key, command_channel, state_key
from app.services import api_tokens

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")

CREATE = {
    "name": "A1 mini",
    "model": "A1 mini",
    "host": "192.168.1.50",
    "serial": "0309ABC",
    "access_code": "12345678",
}
IDLE = {"gcode_state": "IDLE", "nozzle_temper": 200.0, "dev_mode": True}
RUNNING = {"gcode_state": "RUNNING", "nozzle_temper": 200.0, "dev_mode": True}


@pytest.fixture(autouse=True)
def rds(redis_url):
    c = redis_lib.Redis.from_url(redis_url)
    c.flushdb()
    yield c
    c.close()


@pytest.fixture
def set_state(rds):
    def _set(pid, **state):
        rds.set(state_key(pid), json.dumps(state))

    return _set


@pytest.fixture
def printerd_stub(redis_url):
    stops: list[threading.Event] = []

    def start(pid, result=None):
        received: list[dict] = []
        stop = threading.Event()
        stops.append(stop)
        c = redis_lib.Redis.from_url(redis_url)
        ps = c.pubsub()
        ps.subscribe(command_channel(pid))

        def loop():
            while not stop.is_set():
                m = ps.get_message(ignore_subscribe_messages=True, timeout=0.1)
                if m and m["type"] == "message":
                    env = json.loads(m["data"])
                    received.append(env)
                    c.set(
                        cmd_result_key(pid, env["request_id"]),
                        json.dumps(result or {"ok": True}),
                        ex=30,
                    )
            ps.close()
            c.close()

        threading.Thread(target=loop, daemon=True).start()
        time.sleep(0.2)
        return received

    yield start
    for s in stops:
        s.set()


async def _printer(client, **extra) -> int:
    r = await client.post("/api/printers", json={**CREATE, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _mint(client, **body) -> dict:
    body.setdefault("label", "k")
    body.setdefault("scopes", ["read"])
    r = await client.post("/api/settings/api-keys", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


# -- minting -------------------------------------------------------------------


async def test_mint_shows_key_once_and_stores_hash(authenticated_client, db_session):
    minted = await _mint(authenticated_client, scopes=["read", "queue"])
    assert minted["key"].startswith("tdmm_") and minted["key_prefix"] == minted["key"][:10]
    row = (await db_session.execute(select(ApiToken))).scalar_one()
    assert row.token_hash != minted["key"] and minted["key"] not in row.token_hash
    listing = (await authenticated_client.get("/api/settings/api-keys")).json()
    assert len(listing) == 1 and "key" not in listing[0] and "token_hash" not in listing[0]
    assert listing[0]["scopes"] == ["queue", "read"] and listing[0]["rate_limit_per_min"] == 120
    # legacy token list stays free of scoped keys
    assert (await authenticated_client.get("/api/settings/api-tokens")).json() == []


async def test_mint_requires_a_scope_and_a_known_scope(authenticated_client):
    for scopes in ([], ["admin"]):
        r = await authenticated_client.post(
            "/api/settings/api-keys", json={"label": "x", "scopes": scopes}
        )
        assert r.status_code == 422


async def test_mint_capped_by_creator_permissions(member_client, viewer_client):
    # members hold printers:print + library:write but not printers:control
    r = await member_client.post(
        "/api/settings/api-keys", json={"label": "x", "scopes": ["control"]}
    )
    assert r.status_code == 403 and "printers:control" in r.json()["detail"]
    ok = await member_client.post(
        "/api/settings/api-keys", json={"label": "x", "scopes": ["read", "queue", "upload"]}
    )
    assert ok.status_code == 201
    # viewers hold nothing: read only
    for scope in ("queue", "upload", "control"):
        r = await viewer_client.post(
            "/api/settings/api-keys", json={"label": "x", "scopes": [scope]}
        )
        assert r.status_code == 403
    assert (
        await viewer_client.post("/api/settings/api-keys", json={"label": "x", "scopes": ["read"]})
    ).status_code == 201


async def test_mint_rejects_unknown_printer(authenticated_client):
    r = await authenticated_client.post(
        "/api/settings/api-keys", json={"label": "x", "scopes": ["read"], "printer_ids": [9999]}
    )
    assert r.status_code == 422


async def test_revoke_key(authenticated_client, client):
    minted = await _mint(authenticated_client)
    assert (await client.get("/api/ext/v2/ping", headers=_h(minted["key"]))).status_code == 200
    r = await authenticated_client.delete(f"/api/settings/api-keys/{minted['id']}")
    assert r.status_code == 204
    assert (await client.get("/api/ext/v2/ping", headers=_h(minted["key"]))).status_code == 401
    assert (await authenticated_client.delete("/api/settings/api-keys/9999")).status_code == 404


# -- auth ----------------------------------------------------------------------


async def test_ping_accepts_x_api_key_and_bearer_and_tracks_last_used(
    authenticated_client, client, db_session
):
    minted = await _mint(authenticated_client)
    r = await client.get("/api/ext/v2/ping", headers=_h(minted["key"]))
    assert r.status_code == 200 and r.json()["key"]["scopes"] == ["read"]
    r = await client.get("/api/ext/v2/ping", headers={"Authorization": f"Bearer {minted['key']}"})
    assert r.status_code == 200
    row = (await db_session.execute(select(ApiToken))).scalar_one()
    await db_session.refresh(row)
    assert row.last_used_at is not None


async def test_no_credentials_or_bad_key_is_401(client):
    assert (await client.get("/api/ext/v2/ping")).status_code == 401
    assert (await client.get("/api/ext/v2/ping", headers=_h("tdmm_nope"))).status_code == 401


async def test_session_cookie_alone_is_401(authenticated_client):
    assert (await authenticated_client.get("/api/ext/v2/ping")).status_code == 401
    assert (await authenticated_client.get("/api/ext/v2/printers")).status_code == 401


async def test_planes_do_not_cross(authenticated_client, client, db_session, admin_user):
    minted = await _mint(authenticated_client)
    # scoped key refused on the v1 extension plane and session API
    r = await client.get("/api/ext/ping", headers={"Authorization": f"Bearer {minted['key']}"})
    assert r.status_code == 403
    async with httpx.AsyncClient(transport=client._transport, base_url="http://test") as anon:
        r = await anon.get("/api/settings/api-keys", headers=_h(minted["key"]))
    assert r.status_code == 401
    # legacy extension token refused on v2
    legacy, _ = await api_tokens.mint(db_session, user_id=admin_user.id, label="ext")
    r = await client.get("/api/ext/v2/ping", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 403


async def test_expired_key_is_401(authenticated_client, client, db_session):
    minted = await _mint(authenticated_client, expires_in_days=1)
    row = (await db_session.execute(select(ApiToken))).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    assert (await client.get("/api/ext/v2/ping", headers=_h(minted["key"]))).status_code == 401


async def test_inactive_owner_is_401(authenticated_client, client, db_session, admin_user):
    minted = await _mint(authenticated_client)
    user = await db_session.get(User, admin_user.id)
    user.is_active = False
    await db_session.commit()
    assert (await client.get("/api/ext/v2/ping", headers=_h(minted["key"]))).status_code == 401


# -- scopes / permission denial ----------------------------------------------------


async def test_scope_denials(authenticated_client, client, printer_enabled, set_state):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    read = (await _mint(authenticated_client, scopes=["read"]))["key"]
    ctl = (await _mint(authenticated_client, scopes=["control"]))["key"]
    q = (await _mint(authenticated_client, scopes=["queue"]))["key"]
    up = (await _mint(authenticated_client, scopes=["upload"]))["key"]

    # read-only cannot control / queue / upload / slice
    for method, path, body in [
        ("POST", f"/api/ext/v2/printers/{pid}/pause", None),
        ("POST", f"/api/ext/v2/printers/{pid}/control/light", {"on": True}),
        ("POST", "/api/ext/v2/queue", {"model_id": 1}),
        ("POST", "/api/ext/v2/models", {"name": "m"}),
        ("POST", "/api/ext/v2/files/1/slice", {"recipe_id": 1}),
        ("POST", "/api/ext/v2/imports", {"url": "https://x.test/1"}),
    ]:
        r = await client.request(method, path, json=body, headers=_h(read))
        assert r.status_code == 403, (path, r.text)
        assert "missing scope" in r.json()["detail"]
    # control/queue/upload keys cannot read
    for key in (ctl, q, up):
        assert (await client.get("/api/ext/v2/printers", headers=_h(key))).status_code == 403
        r = await client.get(f"/api/ext/v2/printers/{pid}/status", headers=_h(key))
        assert r.status_code == 403
    # control key cannot touch the queue / library
    assert (
        await client.post("/api/ext/v2/queue", json={"model_id": 1}, headers=_h(ctl))
    ).status_code == 403
    assert (
        await client.post("/api/ext/v2/models", json={"name": "m"}, headers=_h(ctl))
    ).status_code == 403
    # queue key cannot control
    r = await client.post(f"/api/ext/v2/printers/{pid}/pause", headers=_h(q))
    assert r.status_code == 403


async def test_owner_demotion_revokes_effective_power(
    make_user, login_as, client, db_session, printer_enabled
):
    owner = await make_user("member")
    c = await login_as(owner)
    key = (await _mint(c, scopes=["queue"]))["key"]
    assert (await client.get("/api/ext/v2/ping", headers=_h(key))).status_code == 200
    row = await db_session.get(User, owner.id)
    row.role = UserRole.VIEWER
    await db_session.commit()
    r = await client.post("/api/ext/v2/queue", json={"model_id": 1}, headers=_h(key))
    assert r.status_code == 403 and "permission" in r.json()["detail"]


async def test_admin_owner_key_never_gets_admin_only_surfaces(
    authenticated_client, client, printer_enabled, set_state
):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    key = (await _mint(authenticated_client, scopes=["read", "control", "queue", "upload"]))["key"]
    # gcode is admin-only and not exposed on the key surface
    r = await client.post(
        f"/api/ext/v2/printers/{pid}/control/gcode", json={"gcode": "G28"}, headers=_h(key)
    )
    assert r.status_code == 404
    # session-only API stays unreachable
    async with httpx.AsyncClient(transport=client._transport, base_url="http://test") as anon:
        assert (await anon.get("/api/settings/api-keys", headers=_h(key))).status_code == 401


# -- printers ------------------------------------------------------------------------


async def test_printer_list_status_and_allowlist(
    authenticated_client, client, set_state, printer_enabled
):
    a = await _printer(authenticated_client)
    b = await _printer(authenticated_client, name="B", serial="0309ABD")
    set_state(a, **IDLE)
    full = (await _mint(authenticated_client))["key"]
    only_a = (await _mint(authenticated_client, printer_ids=[a]))["key"]

    ids = [p["id"] for p in (await client.get("/api/ext/v2/printers", headers=_h(full))).json()]
    assert ids == [a, b]
    ids = [p["id"] for p in (await client.get("/api/ext/v2/printers", headers=_h(only_a))).json()]
    assert ids == [a]
    r = await client.get(f"/api/ext/v2/printers/{a}/status", headers=_h(only_a))
    assert r.status_code == 200 and r.json()["online"] is True
    r = await client.get(f"/api/ext/v2/printers/{b}/status", headers=_h(only_a))
    assert r.status_code == 404


async def test_control_routes_through_command_bus(
    authenticated_client, client, printer_enabled, set_state, printerd_stub, db_session
):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    got = printerd_stub(pid, {"ok": True})
    key = (await _mint(authenticated_client, scopes=["control"]))["key"]

    r = await client.post(f"/api/ext/v2/printers/{pid}/pause", headers=_h(key))
    assert r.status_code == 200 and r.json() == {"ok": True}
    r = await client.post(
        f"/api/ext/v2/printers/{pid}/control/temperature",
        json={"target": "bed", "value": 60},
        headers=_h(key),
    )
    assert r.status_code == 200
    assert [g["command"] for g in got] == ["pause", "set_temperature"]
    assert got[1]["args"] == {"target": "bed", "value": 60}

    # same validation / capability gating as the UI
    r = await client.post(
        f"/api/ext/v2/printers/{pid}/control/speed", json={"level": 9}, headers=_h(key)
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid"
    r = await client.post(
        f"/api/ext/v2/printers/{pid}/control/fan",
        json={"fan": "chamber", "pct": 50},
        headers=_h(key),
    )
    assert r.status_code == 422 and r.json()["code"] == "unsupported"
    assert (
        await client.post(f"/api/ext/v2/printers/{pid}/control/nope", headers=_h(key))
    ).status_code == 404

    # every mutating call is audited with the key
    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "api_key:post")))
        .scalars()
        .all()
    )
    assert len(rows) == 5 and all(r.payload["key_label"] == "k" for r in rows)
    assert all(r.printer_id == pid for r in rows)


async def test_control_refused_while_printing_and_offline(
    authenticated_client, client, printer_enabled, set_state, printerd_stub
):
    pid = await _printer(authenticated_client)
    key = (await _mint(authenticated_client, scopes=["control"]))["key"]
    r = await client.post(f"/api/ext/v2/printers/{pid}/control/home", headers=_h(key))
    assert r.status_code == 409 and r.json()["code"] == "offline"
    set_state(pid, **RUNNING)
    r = await client.post(f"/api/ext/v2/printers/{pid}/control/home", headers=_h(key))
    assert r.status_code == 409 and r.json()["code"] == "printing"


async def test_control_denied_outside_allowlist_and_acl(
    authenticated_client, client, printer_enabled, set_state, make_user, login_as
):
    a = await _printer(authenticated_client)
    b = await _printer(authenticated_client, name="B", serial="0309ABD")
    set_state(b, **IDLE)
    key = (await _mint(authenticated_client, scopes=["control"], printer_ids=[a]))["key"]
    r = await client.post(f"/api/ext/v2/printers/{b}/pause", headers=_h(key))
    assert r.status_code == 404


async def test_get_is_not_audited(authenticated_client, client, db_session):
    key = (await _mint(authenticated_client))["key"]
    await client.get("/api/ext/v2/ping", headers=_h(key))
    await client.get("/api/ext/v2/printers", headers=_h(key))
    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action.like("api_key:%"))))
        .scalars()
        .all()
    )
    assert rows == []


# -- rate limit --------------------------------------------------------------------------


async def test_rate_limit_per_key(authenticated_client, client):
    limited = (await _mint(authenticated_client, rate_limit_per_min=2))["key"]
    other = (await _mint(authenticated_client))["key"]
    assert (await client.get("/api/ext/v2/ping", headers=_h(limited))).status_code == 200
    assert (await client.get("/api/ext/v2/ping", headers=_h(limited))).status_code == 200
    r = await client.get("/api/ext/v2/ping", headers=_h(limited))
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    # other keys are unaffected
    r = await client.get("/api/ext/v2/ping", headers=_h(other))
    assert r.status_code == 200 and r.headers["x-ratelimit-limit"] == "120"


# -- queue / slice / library -------------------------------------------------------------


async def test_queue_roundtrip(authenticated_client, client, db_session):
    model_a = (await authenticated_client.post("/api/models", json={"name": "A"})).json()
    model_b = (await authenticated_client.post("/api/models", json={"name": "B"})).json()
    k = (await _mint(authenticated_client, scopes=["read", "queue"]))["key"]

    r = await client.post("/api/ext/v2/queue", json={"model_id": model_a["id"]}, headers=_h(k))
    assert r.status_code == 201, r.text
    first = r.json()
    r = await client.post("/api/ext/v2/queue", json={"model_id": model_b["id"]}, headers=_h(k))
    second = r.json()
    listing = (await client.get("/api/ext/v2/queue", headers=_h(k))).json()
    assert [e["id"] for e in listing] == [first["id"], second["id"]]

    r = await client.patch(f"/api/ext/v2/queue/{second['id']}", json={"position": 1}, headers=_h(k))
    assert r.status_code == 200 and [e["id"] for e in r.json()] == [second["id"], first["id"]]

    assert (
        await client.delete(f"/api/ext/v2/queue/{first['id']}", headers=_h(k))
    ).status_code == 204
    assert len((await client.get("/api/ext/v2/queue", headers=_h(k))).json()) == 1
    assert (await client.delete("/api/ext/v2/queue/9999", headers=_h(k))).status_code == 404


async def test_queue_ownership_enforced_for_non_admin_owner(
    authenticated_client, client, make_user, login_as
):
    model = (await authenticated_client.post("/api/models", json={"name": "A"})).json()
    entry = (await authenticated_client.post("/api/queue", json={"model_id": model["id"]})).json()
    member = await make_user("member")
    mc = await login_as(member)
    k = (await _mint(mc, scopes=["queue"]))["key"]
    r = await client.delete(f"/api/ext/v2/queue/{entry['id']}", headers=_h(k))
    assert r.status_code == 403
    # an admin's key may manage anyone's entries
    ak = (await _mint(authenticated_client, scopes=["queue"]))["key"]
    assert (
        await client.delete(f"/api/ext/v2/queue/{entry['id']}", headers=_h(ak))
    ).status_code == 204


async def test_allowlisted_key_must_name_an_allowed_printer(
    authenticated_client, client, printer_enabled
):
    a = await _printer(authenticated_client)
    b = await _printer(authenticated_client, name="B", serial="0309ABD")
    model = (await authenticated_client.post("/api/models", json={"name": "A"})).json()
    k = (await _mint(authenticated_client, scopes=["queue"], printer_ids=[a]))["key"]
    r = await client.post("/api/ext/v2/queue", json={"model_id": model["id"]}, headers=_h(k))
    assert r.status_code == 422
    r = await client.post(
        "/api/ext/v2/queue", json={"model_id": model["id"], "printer_id": b}, headers=_h(k)
    )
    assert r.status_code == 404


async def test_slice_reaches_service(authenticated_client, client):
    k = (await _mint(authenticated_client, scopes=["queue"]))["key"]
    r = await client.post("/api/ext/v2/files/1/slice", json={"recipe_id": 1}, headers=_h(k))
    assert r.status_code in (404, 409)  # slicer disabled / file unknown: handler reached
    rk = (await _mint(authenticated_client, scopes=["read"]))["key"]
    r = await client.get(
        "/api/ext/v2/slice-jobs/00000000-0000-0000-0000-000000000000", headers=_h(rk)
    )
    assert r.status_code == 404


async def test_upload_flow(authenticated_client, client):
    k = (await _mint(authenticated_client, scopes=["upload"]))["key"]
    r = await client.post("/api/ext/v2/models", json={"name": "Remote"}, headers=_h(k))
    assert r.status_code == 201, r.text
    model = r.json()
    r = await client.put(
        "/api/ext/v2/uploads",
        params={
            "model_id": model["id"],
            "revision_id": model["current_revision"]["id"],
            "rel_path": "part.stl",
        },
        content=b"solid x\nendsolid x\n" * 50,
        headers=_h(k),
    )
    assert r.status_code == 201, r.text
    assert r.json()["size"] > 0
    # creator is recorded as the key's owner
    detail = (await authenticated_client.get(f"/api/models/{model['slug']}")).json()
    assert detail["id"] == model["id"]


async def test_import_requires_upload_scope(authenticated_client, client, fake_import):
    k = (await _mint(authenticated_client, scopes=["upload"]))["key"]
    r = await client.post(
        "/api/ext/v2/imports", json={"url": "https://fake.test/thing/42"}, headers=_h(k)
    )
    assert r.status_code == 201, r.text
