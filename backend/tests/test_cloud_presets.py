"""Cloud preset sync (Orca Cloud device pairing + Bambu Cloud private presets
-> ``slicer_profiles``): status classification, diff, import/resync, local-edit
protection, and credential encryption."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import Setting, SlicerProfile
from app.services import bambu_auth, cloud_presets, orca_cloud

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


def _orca_profile(pid: str, name: str, ptype: str, **extra) -> dict:
    return {
        "id": pid,
        "name": name,
        "updated_time": "2026-10-01T00:00:00Z",
        "content": {"type": ptype, "name": name, "inherits": "Parent", **extra},
    }


class OrcaServer:
    """Fake api.orcaslicer.com; mutate `profiles` / `poll` between calls."""

    def __init__(self) -> None:
        self.profiles: list[dict] = []
        self.poll = "authorization_pending"
        self.refreshes = 0
        self.pull_auth: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth/device/code":
            return httpx.Response(
                200,
                json={
                    "device_code": "DC-secret",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://orca/verify",
                    "verification_uri_complete": "https://orca/verify?c=ABCD-1234",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        if path == "/oauth/token":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            if form["grant_type"] == "refresh_token":
                self.refreshes += 1
                return httpx.Response(
                    200,
                    json={"access_token": "AT-new", "refresh_token": "RT-new", "expires_in": 86400},
                )
            if self.poll == "ok":
                return httpx.Response(
                    200,
                    json={"access_token": "AT1", "refresh_token": "RT1", "expires_in": 86400},
                )
            return httpx.Response(400, json={"error": self.poll})
        if path == "/api/v1/external/sync/pull":
            self.pull_auth.append(request.headers["authorization"])
            return httpx.Response(200, json={"next_cursor": "1", "upserts": self.profiles})
        return httpx.Response(404)


@pytest.fixture
def orca(monkeypatch: pytest.MonkeyPatch) -> OrcaServer:
    srv = OrcaServer()
    monkeypatch.setattr(
        orca_cloud, "_client", lambda: httpx.Client(transport=httpx.MockTransport(srv.handler))
    )
    return srv


async def _connect_orca(client: httpx.AsyncClient, srv: OrcaServer) -> None:
    r = await client.post("/api/cloud-presets/orca/device/start")
    assert r.status_code == 200, r.text
    assert r.json()["user_code"] == "ABCD-1234" and "device_code" not in r.json()
    srv.poll = "ok"
    r = await client.post("/api/cloud-presets/orca/device/poll")
    assert r.json()["status"] == "complete", r.text


async def test_orca_pairing_flow_and_encrypted_storage(
    authenticated_client: httpx.AsyncClient, db_session, orca: OrcaServer
) -> None:
    st = (await authenticated_client.get("/api/cloud-presets/status")).json()
    assert st["orca"]["connected"] is False

    await authenticated_client.post("/api/cloud-presets/orca/device/start")
    st = (await authenticated_client.get("/api/cloud-presets/status")).json()
    assert st["orca"]["pending"] is True and st["orca"]["connected"] is False
    r = await authenticated_client.post("/api/cloud-presets/orca/device/poll")
    assert r.json()["status"] == "pending"
    orca.poll = "slow_down"
    r = await authenticated_client.post("/api/cloud-presets/orca/device/poll")
    assert r.json() == {"status": "pending", "interval": 10}
    orca.poll = "ok"
    r = await authenticated_client.post("/api/cloud-presets/orca/device/poll")
    assert r.json()["status"] == "complete"

    st = (await authenticated_client.get("/api/cloud-presets/status")).json()
    assert st["orca"]["connected"] is True and st["orca"]["pending"] is False

    row = await db_session.get(Setting, "orca_cloud", populate_existing=True)
    dumped = json.dumps(row.value)
    assert "AT1" not in dumped and "RT1" not in dumped and "DC-secret" not in dumped

    assert (await authenticated_client.delete("/api/cloud-presets/orca")).status_code == 204
    assert (await authenticated_client.get("/api/cloud-presets/status")).json()["orca"][
        "connected"
    ] is False


async def test_orca_pairing_denied_expired_and_not_connected(
    authenticated_client: httpx.AsyncClient, orca: OrcaServer
) -> None:
    r = await authenticated_client.get("/api/cloud-presets/orca/presets")
    assert r.status_code == 401
    r = await authenticated_client.post("/api/cloud-presets/orca/device/poll")
    assert r.json()["status"] == "expired"  # nothing pending
    await authenticated_client.post("/api/cloud-presets/orca/device/start")
    orca.poll = "access_denied"
    r = await authenticated_client.post("/api/cloud-presets/orca/device/poll")
    assert r.json()["status"] == "denied"


async def test_orca_sync_resync_and_local_edit_protection(
    authenticated_client: httpx.AsyncClient, db_session, orca: OrcaServer
) -> None:
    await _connect_orca(authenticated_client, orca)
    # a bundled parent the cloud printer preset inherits from: model/nozzle are copied
    db_session.add(
        SlicerProfile(
            name="Parent",
            kind=cloud_presets.ProfileKind.PRINTER,
            printer_model="Bambu Lab A1 mini",
            nozzle_mm=0.4,
            data={},
        )
    )
    await db_session.commit()
    orca.profiles = [
        _orca_profile("a", "My A1", "machine"),  # `machine` alias, `Parent` is kind printer
        _orca_profile("b", "My PLA", "filament", filament_type=["PLA"]),
        _orca_profile("c", "My Quality", "print", layer_height="0.2"),
        _orca_profile("d", "Unknown", "weird"),  # dropped
    ]

    r = await authenticated_client.get("/api/cloud-presets/orca/presets")
    body = r.json()
    assert {p["preset_id"]: p["status"] for p in body["presets"]} == {
        "a": "new",
        "b": "new",
        "c": "new",
    }
    assert {p["preset_id"]: p["kind"] for p in body["presets"]}["c"] == "process"

    r = await authenticated_client.post("/api/cloud-presets/orca/sync", json={})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 3 and r.json()["updated"] == 0

    rows = {
        row.cloud_id: row
        for row in (
            await db_session.scalars(
                select(SlicerProfile)
                .where(SlicerProfile.cloud_source == "orca")
                .execution_options(populate_existing=True)
            )
        ).all()
    }
    a = rows["a"]
    assert (a.printer_model, a.nozzle_mm) == ("Bambu Lab A1 mini", 0.4)
    assert a.data["type"] == "machine" and a.data["from"] == "system"
    assert a.data["inherits"] == "Parent"  # not flattened: the sidecar resolves it
    assert rows["c"].data["type"] == "process"

    # up to date; a second bulk sync is a no-op
    r = await authenticated_client.post("/api/cloud-presets/orca/sync", json={})
    assert r.json() == {"created": 0, "updated": 0, "skipped": [], "failed": []}

    # upstream edit -> remote_changed -> resync updates; diff shows the key
    orca.profiles[2]["content"]["layer_height"] = "0.16"
    statuses = {
        p["preset_id"]: p["status"]
        for p in (await authenticated_client.get("/api/cloud-presets/orca/presets")).json()[
            "presets"
        ]
    }
    assert statuses["c"] == "remote_changed" and statuses["a"] == "up_to_date"
    d = (await authenticated_client.get("/api/cloud-presets/orca/presets/c/diff")).json()
    assert d["status"] == "remote_changed"
    assert d["changes"] == [{"key": "layer_height", "local": "0.2", "remote": "0.16"}]

    # a local edit on another preset: protected unless overwrite_local
    await authenticated_client.patch(
        f"/api/profiles/{rows['b'].id}",
        json={"data": {**rows["b"].data, "nozzle_temperature": ["200"]}},
    )
    orca.profiles[1]["content"]["filament_type"] = ["PETG"]  # ... and changed upstream too
    statuses = {
        p["preset_id"]: p["status"]
        for p in (await authenticated_client.get("/api/cloud-presets/orca/presets")).json()[
            "presets"
        ]
    }
    assert statuses["b"] == "conflict"

    r = await authenticated_client.post("/api/cloud-presets/orca/sync", json={})
    assert r.json()["updated"] == 1  # only c
    assert r.json()["skipped"] == [{"preset_id": "b", "name": "My PLA", "reason": "conflict"}]
    d = (await authenticated_client.get("/api/cloud-presets/orca/presets/b/diff")).json()
    assert {c["key"] for c in d["changes"]} == {"filament_type", "nozzle_temperature"}

    r = await authenticated_client.post(
        "/api/cloud-presets/orca/sync", json={"preset_ids": ["b"], "overwrite_local": True}
    )
    assert r.json()["updated"] == 1 and r.json()["skipped"] == []
    await db_session.refresh(rows["b"])
    assert rows["b"].data["filament_type"] == ["PETG"]
    assert "nozzle_temperature" not in rows["b"].data

    # a cloud preset deleted upstream is reported as removed (local row kept)
    orca.profiles = orca.profiles[:2]
    body = (await authenticated_client.get("/api/cloud-presets/orca/presets")).json()
    assert [p["preset_id"] for p in body["removed"]] == ["c"]

    r = await authenticated_client.post(
        "/api/cloud-presets/orca/sync", json={"preset_ids": ["nope"]}
    )
    assert r.status_code == 404


async def test_orca_name_collision_gets_suffix(
    authenticated_client: httpx.AsyncClient, db_session, orca: OrcaServer
) -> None:
    await _connect_orca(authenticated_client, orca)
    db_session.add(
        SlicerProfile(name="Fast", kind=cloud_presets.ProfileKind.PROCESS, data={"x": 1})
    )
    await db_session.commit()
    orca.profiles = [_orca_profile("z", "Fast", "print")]
    r = await authenticated_client.post("/api/cloud-presets/orca/sync", json={})
    assert r.json()["created"] == 1
    names = {
        p.name
        for p in (
            await db_session.scalars(
                select(SlicerProfile).where(SlicerProfile.kind == cloud_presets.ProfileKind.PROCESS)
            )
        ).all()
    }
    assert names == {"Fast", "Fast (Orca Cloud)"}


async def test_orca_expired_token_refreshes_and_persists_rotation(
    authenticated_client: httpx.AsyncClient, db_session, orca: OrcaServer
) -> None:
    await _connect_orca(authenticated_client, orca)
    settings = get_settings()
    st = await orca_cloud.get_state(db_session, settings)
    st.expires_at = time.time() - 10
    await orca_cloud.save_state(db_session, settings, st)

    r = await authenticated_client.get("/api/cloud-presets/orca/presets")
    assert r.status_code == 200
    assert orca.refreshes == 1 and orca.pull_auth == ["Bearer AT-new"]
    st = await orca_cloud.get_state(db_session, settings)
    assert st.refresh_token == "RT-new"


async def _expire_orca(db_session) -> None:
    settings = get_settings()
    st = await orca_cloud.get_state(db_session, settings)
    st.expires_at = time.time() - 10
    await orca_cloud.save_state(db_session, settings, st)


async def test_orca_concurrent_refresh_spends_token_once(
    authenticated_client: httpx.AsyncClient, db_session, orca: OrcaServer, monkeypatch
) -> None:
    import asyncio

    from app.db import get_sessionmaker

    await _connect_orca(authenticated_client, orca)
    await _expire_orca(db_session)
    real = orca_cloud.refresh_tokens

    def slow(settings, rt):
        time.sleep(0.3)
        return real(settings, rt)

    monkeypatch.setattr(orca_cloud, "refresh_tokens", slow)
    settings = get_settings()

    async def one() -> str:
        async with get_sessionmaker()() as s:
            return await orca_cloud.ensure_access_token(s, settings)

    out = await asyncio.gather(one(), one(), one())
    assert out == ["AT-new"] * 3 and orca.refreshes == 1


async def test_orca_refresh_clears_pairing_only_on_invalid_grant(
    authenticated_client: httpx.AsyncClient, db_session, orca: OrcaServer, monkeypatch
) -> None:
    await _connect_orca(authenticated_client, orca)
    await _expire_orca(db_session)
    settings = get_settings()
    resp = {"r": httpx.Response(403, text="<html>cloudflare</html>")}
    real_handler = orca.handler

    def handler(request):
        if b"refresh_token" in request.content and b"grant_type=refresh_token" in request.content:
            return resp["r"]
        return real_handler(request)

    monkeypatch.setattr(
        orca_cloud, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(orca_cloud.OrcaCloudAuthError):
        await orca_cloud.ensure_access_token(db_session, settings)
    assert (await orca_cloud.get_state(db_session, settings)).connected  # kept

    resp["r"] = httpx.Response(400, json={"error": "invalid_grant"})
    with pytest.raises(orca_cloud.OrcaCloudAuthError):
        await orca_cloud.ensure_access_token(db_session, settings)
    assert not (await orca_cloud.get_state(db_session, settings)).connected  # wiped


def test_apply_token_response_missing_expires_in_defaults_24h() -> None:
    st = orca_cloud.OrcaAuthState()
    orca_cloud.apply_token_response(st, {"access_token": "opaque", "refresh_token": "r"})
    assert st.access_valid()
    assert abs(st.expires_at - time.time() - 86400) < 5


def test_apply_token_response_uses_jwt_exp() -> None:
    import base64

    exp = int(time.time()) + 7200
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    st = orca_cloud.OrcaAuthState()
    orca_cloud.apply_token_response(st, {"access_token": f"h.{body}.s"})
    assert st.expires_at == exp


async def test_cloud_list_and_diff_require_inventory_write(
    make_user, login_as, orca: OrcaServer
) -> None:
    viewer = await make_user("viewer")
    c = await login_as(viewer)
    assert (await c.get("/api/cloud-presets/orca/presets")).status_code == 403
    assert (await c.get("/api/cloud-presets/orca/presets/x/diff")).status_code == 403


def test_upsert_remote_survives_concurrent_insert(_truncate_all_tables, monkeypatch) -> None:
    from app.tasks import base

    remote = cloud_presets.RemotePreset(
        source="orca",
        preset_id="p-race",
        name="Race",
        kind=cloud_presets.ProfileKind.PROCESS,
        updated_time="t1",
        content={"type": "process", "name": "Race"},
    )
    content = remote.content
    with base.sync_session() as s:
        assert cloud_presets.upsert_remote(s, remote, content, None) == "created"
        s.commit()
    real = cloud_presets.local_rows
    calls = {"n": 0}

    def stale(session, source):  # first read misses the row the "other sync" inserted
        calls["n"] += 1
        return {} if calls["n"] == 1 else real(session, source)

    monkeypatch.setattr(cloud_presets, "local_rows", stale)
    with base.sync_session() as s:
        out = cloud_presets.upsert_remote(s, remote, content, None)
        s.commit()
    assert out == cloud_presets.UP_TO_DATE and calls["n"] >= 2


# --- Bambu Cloud ------------------------------------------------------------


def _bambu_handler(state: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer BAT"
        path = request.url.path
        if path == "/v1/iot-service/api/slicer/setting":
            return httpx.Response(
                200,
                json={
                    "filament": {
                        "private": [
                            {"setting_id": "f1", "name": "Mine PLA", "updated_time": state["t"]}
                        ],
                        "public": [{"setting_id": "pub", "name": "Bambu PLA"}],
                    },
                    "printer": {"private": [], "public": []},
                    "print": {
                        "private": [
                            {"setting_id": "p1", "name": "Mine Fast", "updated_time": "t0"}
                        ],
                        "public": [],
                    },
                },
            )
        sid = path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "setting_id": sid,
                "setting": {
                    "type": "filament",
                    "name": sid,
                    "nozzle_temperature": [state["temp"] if sid == "f1" else "200"],
                },
            },
        )

    return handler


async def test_bambu_cloud_private_presets_sync(
    authenticated_client: httpx.AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    await bambu_auth.set_bambu_auth(
        db_session,
        settings,
        account="a@b.c",
        region="global",
        refresh_token="BRT",
        access_token="BAT",
        access_expires_at=time.time() + 3600,
    )
    state = {"t": "t0", "temp": "210"}

    def factory(region: str = "global") -> httpx.Client:
        return httpx.Client(
            base_url=bambu_auth.base_url_for_region(region),
            transport=httpx.MockTransport(_bambu_handler(state)),
        )

    monkeypatch.setattr(bambu_auth, "_client", factory)

    body = (await authenticated_client.get("/api/cloud-presets/bambu/presets")).json()
    assert {p["preset_id"] for p in body["presets"]} == {"f1", "p1"}  # public ones skipped
    assert {p["status"] for p in body["presets"]} == {"new"}

    r = await authenticated_client.post("/api/cloud-presets/bambu/sync", json={})
    assert r.json()["created"] == 2, r.text
    row = (
        await db_session.scalars(
            select(SlicerProfile)
            .where(SlicerProfile.cloud_id == "f1")
            .execution_options(populate_existing=True)
        )
    ).one()
    assert row.cloud_source == "bambu" and row.data["nozzle_temperature"] == ["210"]
    assert row.data["type"] == "filament"

    # unchanged listing -> up to date; changed updated_time -> remote_changed
    statuses = lambda b: {p["preset_id"]: p["status"] for p in b["presets"]}  # noqa: E731
    body = (await authenticated_client.get("/api/cloud-presets/bambu/presets")).json()
    assert statuses(body) == {"f1": "up_to_date", "p1": "up_to_date"}
    state.update(t="t1", temp="215")
    body = (await authenticated_client.get("/api/cloud-presets/bambu/presets")).json()
    assert statuses(body)["f1"] == "remote_changed"
    d = (await authenticated_client.get("/api/cloud-presets/bambu/presets/f1/diff")).json()
    assert d["changes"] == [{"key": "nozzle_temperature", "local": ["210"], "remote": ["215"]}]
    r = await authenticated_client.post("/api/cloud-presets/bambu/sync", json={})
    assert r.json()["updated"] == 1 and r.json()["created"] == 0


async def test_bambu_not_connected_is_401(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.get("/api/cloud-presets/bambu/presets")
    assert r.status_code == 401


def test_status_of_and_diff_helpers() -> None:
    assert cloud_presets.status_of(None, "h") == "new"
    row = SlicerProfile(
        data={"a": 1}, cloud_hash="h", data_hash=cloud_presets.content_hash({"a": 1})
    )
    assert cloud_presets.status_of(row, "h") == "up_to_date"
    assert cloud_presets.status_of(row, "h2") == "remote_changed"
    row.data = {"a": 2}
    assert cloud_presets.status_of(row, "h") == "local_modified"
    assert cloud_presets.status_of(row, "h2") == "conflict"
    assert cloud_presets.diff_data({"a": 1, "b": 2}, {"a": 1, "c": 3}) == [
        {"key": "b", "local": 2, "remote": None},
        {"key": "c", "local": None, "remote": 3},
    ]


def test_upsert_remote_derives_scope_meta(_truncate_all_tables) -> None:
    from app.models import SlicerProfile
    from app.tasks import base

    def remote(pid, kind, name, content):
        return cloud_presets.RemotePreset(
            source="orca", preset_id=pid, name=name, kind=kind, updated_time="t", content=content
        )

    kind = cloud_presets.ProfileKind
    printer = remote(
        "m1",
        kind.PRINTER,
        "My A1M",
        {"printer_model": "N1", "nozzle_diameter": ["0.4"], "type": "machine"},
    )
    parent = remote(
        "f0",
        kind.FILAMENT,
        "Base PLA",
        {"type": "filament", "compatible_printers": ["My A1M"]},
    )
    child = remote("f1", kind.FILAMENT, "My PLA", {"inherits": "Base PLA", "type": "filament"})
    multi = remote(
        "p1",
        kind.PROCESS,
        "0.20mm multi",
        {
            "type": "process",
            "layer_height": "0.2",
            "compatible_printers": ["Bambu Lab A1 mini 0.4 nozzle", "Bambu Lab P1S 0.4 nozzle"],
        },
    )
    with base.sync_session() as s:
        for r in (printer, parent, child, multi):
            assert cloud_presets.upsert_remote(s, r, r.content, None) == "created"
        s.commit()
        rows = {x.name: x for x in s.query(SlicerProfile).all()}
    assert rows["My A1M"].compat == [{"model_key": "a1mini", "nozzle_mm": 0.4}]
    assert rows["0.20mm multi"].printer_model_key is None
    assert {c["model_key"] for c in rows["0.20mm multi"].compat} == {"a1mini", "p1s"}
    assert rows["0.20mm multi"].layer_height_mm == 0.2
    assert rows["My PLA"].compat == [{"model_key": "a1mini", "nozzle_mm": 0.4}]
