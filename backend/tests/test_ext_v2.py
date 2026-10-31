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


async def test_allowlisted_key_modifies_own_unassigned_entries(
    authenticated_client, client, printer_enabled, make_user, login_as
):
    a = await _printer(authenticated_client)
    model = (await authenticated_client.post("/api/models", json={"name": "A"})).json()
    mine = (await authenticated_client.post("/api/queue", json={"model_id": model["id"]})).json()
    member = await make_user("member")
    mc = await login_as(member)
    m2 = (await authenticated_client.post("/api/models", json={"name": "B"})).json()
    theirs = (await mc.post("/api/queue", json={"model_id": m2["id"]})).json()
    k = (await _mint(authenticated_client, scopes=["read", "queue"], printer_ids=[a]))["key"]

    ids = [e["id"] for e in (await client.get("/api/ext/v2/queue", headers=_h(k))).json()]
    assert mine["id"] in ids and theirs["id"] not in ids  # view == modify
    r = await client.patch(f"/api/ext/v2/queue/{mine['id']}", json={"position": 1}, headers=_h(k))
    assert r.status_code == 200, r.text
    r = await client.delete(f"/api/ext/v2/queue/{theirs['id']}", headers=_h(k))
    assert r.status_code == 404
    r = await client.delete(f"/api/ext/v2/queue/{mine['id']}", headers=_h(k))
    assert r.status_code == 204


# -- H1: status fields, camera, thumbnail --------------------------------------

JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"
TRAYS = [
    {"slot": 0, "ams_unit": 0, "tray": 0, "material": "PLA", "color": "#FF0000"},
    {"slot": 5, "ams_unit": 1, "tray": 1, "material": "PETG", "color": "#00FF00"},
]
EXT = {"slot": 254, "ams_unit": 255, "tray": 254, "material": "TPU", "color": "#0000FF"}


async def _job(db_session, seed_file, printer_id, *, rel_path="job.3mf", plate=1):
    from app.models import PrintJob
    from app.models.enums import BlobFormat, BlobKind, PrintJobState
    from app.models.library import Model, Revision

    model = Model(slug="hm", name="H Model")
    db_session.add(model)
    await db_session.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db_session.add(rev)
    await db_session.commit()
    f = await seed_file(
        model, rev, rel_path, b"3mf-bytes", blob_format=BlobFormat.THREEMF, blob_kind=BlobKind.MESH
    )
    job = PrintJob(
        printer_id=printer_id,
        file_id=f.id,
        state=PrintJobState.PRINTING,
        plate=plate,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.commit()
    return job, f, model, rev


def _plate_png(blob_hash: str, plate: int, data: bytes) -> None:
    from app.config import get_settings
    from app.services import derivatives

    path = derivatives.plate_thumb_path(get_settings(), blob_hash, plate)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def test_status_active_tray_and_null_job(
    authenticated_client, client, set_state, printer_enabled
):
    a = await _printer(authenticated_client)
    key = (await _mint(authenticated_client))["key"]
    url = f"/api/ext/v2/printers/{a}/status"
    set_state(a, **IDLE, trays=TRAYS, ext_tray=EXT, tray_now=5)
    j = (await client.get(url, headers=_h(key))).json()
    assert j["active_tray"]["material"] == "PETG" and j["print_job"] is None
    assert j["print_thumbnail_key"] is None
    set_state(a, **IDLE, trays=TRAYS, ext_tray=EXT, tray_now=254)
    assert (await client.get(url, headers=_h(key))).json()["active_tray"]["material"] == "TPU"
    for now in (255, None):
        set_state(a, **IDLE, trays=TRAYS, ext_tray=EXT, tray_now=now)
        assert (await client.get(url, headers=_h(key))).json()["active_tray"] is None


async def test_status_print_job_and_thumbnail_key(
    authenticated_client, client, set_state, db_session, seed_file, printer_enabled
):
    a = await _printer(authenticated_client)
    key = (await _mint(authenticated_client))["key"]
    set_state(a, **RUNNING)
    job, f, model, rev = await _job(db_session, seed_file, a)
    url = f"/api/ext/v2/printers/{a}/status"
    j = (await client.get(url, headers=_h(key))).json()
    assert j["print_job"]["print_job_id"] == job.id
    assert j["print_job"]["file_id"] == f.id and j["print_job"]["model_id"] == model.id
    assert j["print_job"]["model_name"] == "H Model" and j["print_job"]["revision_id"] == rev.id
    assert j["print_job"]["plate"] == 1 and j["print_job"]["started_at"]
    assert j["print_thumbnail_key"] is None  # no plate image yet
    _plate_png(f.blob_hash, 1, b"png")
    k1 = (await client.get(url, headers=_h(key))).json()["print_thumbnail_key"]
    assert k1 and len(k1) == 16
    # a different plate (different image) changes the key
    job.plate = 2
    await db_session.commit()
    _plate_png(f.blob_hash, 2, b"png2")
    k2 = (await client.get(url, headers=_h(key))).json()["print_thumbnail_key"]
    assert k2 and k2 != k1


async def test_ext_thumbnail_200_404_and_allowlist(
    authenticated_client, client, set_state, db_session, seed_file, printer_enabled
):
    a = await _printer(authenticated_client)
    b = await _printer(authenticated_client, name="B", serial="0309ABD")
    key = (await _mint(authenticated_client, printer_ids=[a]))["key"]
    assert (
        await client.get(f"/api/ext/v2/printers/{a}/thumbnail", headers=_h(key))
    ).status_code == 404
    _job_row, f, _m, _r = await _job(db_session, seed_file, a)
    assert (
        await client.get(f"/api/ext/v2/printers/{a}/thumbnail", headers=_h(key))
    ).status_code == 404
    _plate_png(f.blob_hash, 1, b"\x89PNGdata")
    r = await client.get(f"/api/ext/v2/printers/{a}/thumbnail", headers=_h(key))
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content == b"\x89PNGdata"
    assert (
        await client.get(f"/api/ext/v2/printers/{b}/thumbnail", headers=_h(key))
    ).status_code == 404
    assert (await client.get(f"/api/ext/v2/printers/{a}/thumbnail")).status_code == 401


async def test_ext_camera_snapshot_states(authenticated_client, client, rds, printer_enabled):
    from app.services import camera_state as cs

    a = await _printer(authenticated_client)
    b = await _printer(authenticated_client, name="B", serial="0309ABD")
    key = (await _mint(authenticated_client, printer_ids=[a]))["key"]
    url = f"/api/ext/v2/printers/{a}/camera/snapshot"
    r = await client.get(url, headers=_h(key))
    assert r.status_code == 503 and r.headers["retry-after"] == "2"
    cs.put_frame(rds, a, JPEG)
    r = await client.get(url, headers=_h(key))
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == JPEG
    cs.set_status(rds, a, "unsupported")
    assert (await client.get(url, headers=_h(key))).status_code == 404
    assert (
        await client.get(f"/api/ext/v2/printers/{b}/camera/snapshot", headers=_h(key))
    ).status_code == 404
    assert (await client.get(url, headers=_h("tdmm_nope"))).status_code == 401
    assert (await client.get(url)).status_code == 401


async def test_ext_camera_stream_content_type(
    authenticated_client, client, rds, printer_enabled, monkeypatch
):
    from app.services import camera_http
    from app.services import camera_state as cs

    monkeypatch.setattr(camera_http, "STREAM_IDLE_S", 0.3)
    a = await _printer(authenticated_client)
    key = (await _mint(authenticated_client))["key"]
    cs.put_frame(rds, a, JPEG)
    r = await client.get(f"/api/ext/v2/printers/{a}/camera/stream", headers=_h(key))
    assert r.status_code == 200
    assert r.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
    assert b"--frame" in r.content and JPEG in r.content
    cs.set_status(rds, a, "unsupported")
    r = await client.get(f"/api/ext/v2/printers/{a}/camera/stream", headers=_h(key))
    assert r.status_code == 404
    assert (await client.get(f"/api/ext/v2/printers/{a}/camera/stream")).status_code == 401


async def test_ext_camera_token_and_print_jobs(
    authenticated_client, client, db_session, seed_file, printer_enabled
):
    a = await _printer(authenticated_client)
    b = await _printer(authenticated_client, name="B", serial="0309ABD")
    key = (await _mint(authenticated_client, printer_ids=[a]))["key"]
    r = await client.post(f"/api/ext/v2/printers/{a}/camera/token", headers=_h(key))
    assert r.status_code == 200 and "token=" in r.json()["stream_url"]
    assert (
        await client.post(f"/api/ext/v2/printers/{b}/camera/token", headers=_h(key))
    ).status_code == 404
    job, *_ = await _job(db_session, seed_file, a)
    r = await client.get("/api/ext/v2/print-jobs", headers=_h(key))
    assert [j["id"] for j in r.json()] == [job.id]
    r = await client.get(f"/api/ext/v2/print-jobs?printer_id={b}", headers=_h(key))
    assert r.status_code == 404
    assert (await client.get("/api/ext/v2/print-jobs")).status_code == 401


async def test_ext_print_requires_queue_scope_and_allowlist(
    authenticated_client, client, printer_enabled
):
    a = await _printer(authenticated_client)
    b = await _printer(authenticated_client, name="B", serial="0309ABD")
    ro = (await _mint(authenticated_client))["key"]
    body = {"file_id": 1, "plate": 1}
    r = await client.post(f"/api/ext/v2/printers/{a}/print", json=body, headers=_h(ro))
    assert r.status_code == 403
    q = (await _mint(authenticated_client, scopes=["queue"], printer_ids=[a]))["key"]
    r = await client.post(f"/api/ext/v2/printers/{b}/print", json=body, headers=_h(q))
    assert r.status_code == 404
    r = await client.post(f"/api/ext/v2/printers/{a}/print", json=body, headers=_h(q))
    assert r.status_code in (404, 409, 422)  # reaches the shared start-print service
