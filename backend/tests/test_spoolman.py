import httpx
import pytest
import respx

from app.models import Setting, Spool, SpoolmanSyncState
from app.services import spoolman

pytestmark = pytest.mark.usefixtures("data_dir")

BASE = "http://spoolman.local:7912"
API = f"{BASE}/api/v1"


def _cfg(s, mode: str, url: str | None = BASE) -> None:
    s.merge(Setting(key="spoolman", value={"url": url, "mode": mode}))
    s.commit()


def _spool(s, *, used=100.0, sm_id=7, label=1000.0) -> Spool:
    sp = Spool(material_type="PLA", label_weight_g=label, weight_used_g=used, spoolman_id=sm_id)
    s.add(sp)
    s.commit()
    return sp


def test_mode_off_makes_no_http_calls(seed):
    sp = _spool(seed.s)
    with respx.mock(assert_all_called=False) as m:
        route = m.route().mock(return_value=httpx.Response(200, json={}))
        assert spoolman.push_usage(seed.s, sp, 5.0) is False
        assert spoolman.pull_spool(seed.s, sp) is None
        assert spoolman.set_location(seed.s, sp, "x") is False
        spoolman.on_slot_change(seed.s, 1, 0, 1, None, sp.id)
        spoolman.push_usages(seed.s, [(sp, 3.0)])
        assert spoolman.reconcile(seed.s)["skipped"] is True
        assert not route.called
    assert seed.s.get(SpoolmanSyncState, sp.id) is None


def test_push_usage_puts_use_weight(seed):
    _cfg(seed.s, "mirror")
    sp = _spool(seed.s)
    with respx.mock as m:
        route = m.put(f"{API}/spool/7/use").mock(return_value=httpx.Response(200, json={}))
        assert spoolman.push_usage(seed.s, sp, 12.5) is True
    assert route.calls.last.request.read() == b'{"use_weight":12.5}'
    st = seed.s.get(SpoolmanSyncState, sp.id)
    assert st.last_pushed_at is not None and st.last_error is None


def test_push_usage_failure_recorded_not_raised(seed):
    _cfg(seed.s, "mirror")
    sp = _spool(seed.s)
    with respx.mock as m:
        m.put(f"{API}/spool/7/use").mock(return_value=httpx.Response(500))
        assert spoolman.push_usage(seed.s, sp, 1.0) is False
    assert seed.s.get(SpoolmanSyncState, sp.id).last_error


def test_unlinked_spool_not_pushed(seed):
    _cfg(seed.s, "mirror")
    sp = _spool(seed.s, sm_id=None)
    with respx.mock(assert_all_called=False) as m:
        route = m.route().mock(return_value=httpx.Response(200, json={}))
        assert spoolman.push_usage(seed.s, sp, 1.0) is False
        assert not route.called


REMOTE = {"id": 7, "remaining_weight": 700.0, "used_weight": 300.0}


def test_pull_conflict_authoritative_remote_wins(seed):
    _cfg(seed.s, "authoritative")
    sp = _spool(seed.s, used=100.0)
    with respx.mock as m:
        m.get(f"{API}/spool").mock(return_value=httpx.Response(200, json=[REMOTE]))
        out = spoolman.reconcile(seed.s)
    assert out["pulled"] == 1
    seed.s.refresh(sp)
    assert sp.weight_used_g == 300.0
    assert seed.s.get(SpoolmanSyncState, sp.id).last_pulled_weight == 700.0


def test_pull_conflict_mirror_keeps_local(seed):
    _cfg(seed.s, "mirror")
    sp = _spool(seed.s, used=100.0)
    with respx.mock as m:
        m.get(f"{API}/spool/7").mock(return_value=httpx.Response(200, json=REMOTE))
        assert spoolman.pull_spool(seed.s, sp) == REMOTE
    seed.s.refresh(sp)
    assert sp.weight_used_g == 100.0
    assert seed.s.get(SpoolmanSyncState, sp.id).last_pulled_weight == 700.0


def test_reconcile_missing_remote_records_error(seed):
    _cfg(seed.s, "authoritative")
    sp = _spool(seed.s)
    with respx.mock as m:
        m.get(f"{API}/spool").mock(return_value=httpx.Response(200, json=[]))
        assert spoolman.reconcile(seed.s)["errors"] == 1
    assert "missing" in seed.s.get(SpoolmanSyncState, sp.id).last_error


def test_slot_change_sets_and_clears_location(seed):
    _cfg(seed.s, "mirror")
    a = _spool(seed.s, sm_id=7)
    b = _spool(seed.s, sm_id=8)
    with respx.mock as m:
        r7 = m.patch(f"{API}/spool/7").mock(return_value=httpx.Response(200, json={}))
        r8 = m.patch(f"{API}/spool/8").mock(return_value=httpx.Response(200, json={}))
        spoolman.on_slot_change(seed.s, 3, 0, 2, a.id, b.id)
    assert r7.calls.last.request.read() == b'{"location":""}'
    assert r8.calls.last.request.read() == b'{"location":"printer:3:2"}'


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


async def _db_spool(db_session, sm_id=None) -> int:
    sp = Spool(material_type="PETG", spoolman_id=sm_id)
    db_session.add(sp)
    await db_session.commit()
    return sp.id


async def test_settings_roundtrip_and_validation(authenticated_client):
    r = await authenticated_client.get("/api/spoolman/settings")
    assert r.json() == {"url": None, "mode": "off"}
    bad = await authenticated_client.put("/api/spoolman/settings", json={"mode": "mirror"})
    assert bad.status_code == 422
    r = await authenticated_client.put(
        "/api/spoolman/settings", json={"url": BASE + "/", "mode": "mirror"}
    )
    assert r.json() == {"url": BASE, "mode": "mirror"}


async def test_test_connection(authenticated_client):
    with respx.mock as m:
        m.get(f"{API}/info").mock(return_value=httpx.Response(200, json={"version": "0.22"}))
        r = await authenticated_client.post("/api/spoolman/test", json={"url": BASE})
    assert r.json() == {"ok": True, "version": "0.22"}
    with respx.mock as m:
        m.get(f"{API}/info").mock(side_effect=httpx.ConnectError("boom"))
        m.get(f"{API}/health").mock(side_effect=httpx.ConnectError("boom"))
        r = await authenticated_client.post("/api/spoolman/test", json={"url": BASE})
    assert r.json()["ok"] is False


async def test_remote_spools_requires_enabled_then_lists(authenticated_client):
    assert (await authenticated_client.get("/api/spoolman/spools")).status_code == 409
    await authenticated_client.put("/api/spoolman/settings", json={"url": BASE, "mode": "mirror"})
    with respx.mock as m:
        m.get(f"{API}/spool").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 4,
                        "remaining_weight": 800,
                        "filament": {"name": "Red", "material": "PLA", "vendor": {"name": "Acme"}},
                    }
                ],
            )
        )
        r = await authenticated_client.get("/api/spoolman/spools")
    assert r.json()[0]["vendor"] == "Acme" and r.json()[0]["id"] == 4


async def test_link_unlink_and_duplicate(authenticated_client, db_session):
    a = await _db_spool(db_session)
    b = await _db_spool(db_session)
    r = await authenticated_client.post(
        "/api/spoolman/link", json={"spool_id": a, "spoolman_id": 9}
    )
    assert r.status_code == 204
    shown = await authenticated_client.get(f"/api/spools/{a}")
    assert shown.json()["spoolman_id"] == 9
    dup = await authenticated_client.post(
        "/api/spoolman/link", json={"spool_id": b, "spoolman_id": 9}
    )
    assert dup.status_code == 409
    r = await authenticated_client.post("/api/spoolman/unlink", json={"spool_id": a})
    assert r.status_code == 204
    assert (await authenticated_client.get(f"/api/spools/{a}")).json()["spoolman_id"] is None
    miss = await authenticated_client.post("/api/spoolman/unlink", json={"spool_id": 9999})
    assert miss.status_code == 404


async def test_sync_route(authenticated_client, db_session):
    assert (await authenticated_client.post("/api/spoolman/sync")).status_code == 409
    sid = await _db_spool(db_session, sm_id=7)
    await authenticated_client.put(
        "/api/spoolman/settings", json={"url": BASE, "mode": "authoritative"}
    )
    with respx.mock as m:
        m.get(f"{API}/spool").mock(return_value=httpx.Response(200, json=[REMOTE]))
        r = await authenticated_client.post("/api/spoolman/sync")
    assert r.json()["pulled"] == 1
    assert (await authenticated_client.get(f"/api/spools/{sid}")).json()["weight_used_g"] == 300.0
