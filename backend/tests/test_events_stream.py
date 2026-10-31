"""``GET /api/events`` with API keys + ``printer.status`` / ``printer.delta``."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import redis as redis_lib

from app.printers.base import state_key
from app.schemas.printers import AmsTrayOut, HmsEntryOut, PrinterStatusOut
from app.services import printer_stream
from app.services.events import CHANNEL
from app.services.printer_status import normalize_status
from tests.test_events_sse import live_client  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("library_root", "data_dir", "printer_enabled")

CREATE = {
    "name": "A1 mini",
    "model": "A1 mini",
    "host": "192.168.1.50",
    "serial": "0309ABC",
    "access_code": "12345678",
}


# -- unit ---------------------------------------------------------------------


def test_normalize_sorts_and_rounds():
    out = PrinterStatusOut(
        online=True,
        nozzle_temper=200.04,
        bed_temper=59.96,
        mc_remaining_time=12,
        wifi_signal="-52dBm",
        trays=[
            AmsTrayOut(slot=5, ams_unit=1),
            AmsTrayOut(slot=1, ams_unit=0),
            AmsTrayOut(slot=0, ams_unit=0),
        ],
        hms=[HmsEntryOut(attr=2, code=1), HmsEntryOut(attr=1, code=9)],
    )
    normalize_status(out)
    assert out.wifi_signal == -52
    assert (out.nozzle_temper, out.bed_temper) == (200.0, 60.0)
    assert [t.slot for t in out.trays] == [0, 1, 5]
    assert [h.attr for h in out.hms] == [1, 2]
    assert PrinterStatusOut(online=True, wifi_signal=None).wifi_signal is None


def test_merge_patch_roundtrip():
    a = {"a": 1, "b": {"c": 2, "d": 3}, "l": [1, 2], "gone": 5}
    b = {"a": 1, "b": {"c": 9}, "l": [2], "new": {"x": 1}}
    patch = printer_stream.merge_patch_diff(a, b)
    assert patch == {"b": {"c": 9, "d": None}, "l": [2], "gone": None, "new": {"x": 1}}
    assert printer_stream.apply_merge_patch(a, patch) == b
    assert printer_stream.merge_patch_diff(b, b) == {}


def test_comparable_drops_volatile_and_nulls():
    assert printer_stream.comparable(
        {"last_report_at": "t", "x": None, "y": {"z": None, "w": 1}}
    ) == {"y": {"w": 1}}


# -- SSE ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fast(monkeypatch, redis_url):
    from app.api import events

    async def _hb(_db):
        return 0.2

    monkeypatch.setattr(events, "_heartbeat_s", _hb)
    monkeypatch.setattr(printer_stream, "TICK_S", 0.05)
    printer_stream.invalidate()
    c = redis_lib.Redis.from_url(redis_url)
    c.flushdb()
    yield c
    c.close()


async def _mk_printer(client, serial: str) -> int:
    r = await client.post("/api/printers", json={**CREATE, "serial": serial, "name": serial})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _mint(client, **body) -> str:
    body.setdefault("label", "k")
    body.setdefault("scopes", ["read"])
    r = await client.post("/api/settings/api-keys", json=body)
    assert r.status_code == 201, r.text
    return r.json()["key"]


async def _events(client, headers=None, params=None, *, until, timeout=10):
    """Collect (events, ping_count) until ``until(events)``."""
    evs: list[dict] = []
    pings = 0

    async def run():
        nonlocal pings
        async with client.stream("GET", "/api/events", headers=headers, params=params) as r:
            assert r.status_code == 200, r.status_code
            async for line in r.aiter_lines():
                if line.startswith(":"):
                    pings += 1
                elif line.startswith("data:"):
                    evs.append(json.loads(line[5:]))
                if until(evs, pings):
                    return

    await asyncio.wait_for(run(), timeout)
    return evs, pings


def _set(rds, pid, **state):
    rds.set(state_key(pid), json.dumps(state))


async def test_bad_key_401_and_session_still_works(live_client):  # noqa: F811
    bare = httpx.AsyncClient(base_url=str(live_client.base_url))
    async with bare:
        r = await bare.get("/api/events", headers={"X-API-Key": "tdmm_nope"})
        assert r.status_code == 401
        r = await bare.get("/api/events")
        assert r.status_code == 401
    await _mk_printer(live_client, "SERS1XYZ")
    await _events(live_client, until=lambda e, p: p >= 1)  # session, no printer.*


async def test_write_only_scope_forbidden(live_client):  # noqa: F811
    key = await _mint(live_client, scopes=["queue"])
    r = await live_client.get("/api/events", headers={"X-API-Key": key})
    assert r.status_code == 403


async def test_initial_status_allowlist_and_printer_ids(live_client, fast):  # noqa: F811
    p1 = await _mk_printer(live_client, "SERA1XYZ")
    p2 = await _mk_printer(live_client, "SERA2XYZ")
    p3 = await _mk_printer(live_client, "SERA3XYZ")
    for p in (p1, p2, p3):
        _set(fast, p, gcode_state="IDLE", nozzle_temper=21.0, wifi_signal="-52dBm")
    key = await _mint(live_client, printer_ids=[p1, p2])
    h = {"X-API-Key": key}
    evs, _ = await _events(live_client, h, until=lambda e, p: len(e) >= 2)
    assert {e["printer_id"] for e in evs} == {p1, p2}
    assert all(e["type"] == "printer.status" for e in evs)
    assert evs[0]["status"]["wifi_signal"] == -52 and evs[0]["status"]["online"] is True
    # printer_ids narrows; ids outside the allowlist are ignored
    evs, _ = await _events(
        live_client, h, {"printer_ids": f"{p2},{p3}"}, until=lambda e, p: len(e) >= 1 and p >= 1
    )
    assert [e["printer_id"] for e in evs] == [p2]
    # bearer works too
    evs, _ = await _events(
        live_client, {"Authorization": f"Bearer {key}"}, until=lambda e, p: len(e) >= 2
    )
    assert len(evs) == 2


async def test_session_gets_printer_events_only_when_asked(live_client, fast):  # noqa: F811
    p1 = await _mk_printer(live_client, "SERB1XYZ")
    _set(fast, p1, gcode_state="IDLE")
    evs, _ = await _events(
        live_client, params={"printer_ids": str(p1)}, until=lambda e, p: len(e) >= 1
    )
    assert evs[0]["type"] == "printer.status"


async def test_delta_only_on_real_change(live_client, fast):  # noqa: F811
    pid = await _mk_printer(live_client, "SERD1XYZ")
    trays = [{"slot": 0, "ams_unit": 0}, {"slot": 1, "ams_unit": 0}]
    _set(fast, pid, gcode_state="IDLE", nozzle_temper=200.0, trays=trays, last_report_at="t0")
    key = await _mint(live_client)
    h = {"X-API-Key": key}

    async def mutate():
        await asyncio.sleep(0.4)
        # volatile-only, jitter below 0.1, tray reorder: none may emit
        _set(
            fast,
            pid,
            gcode_state="IDLE",
            nozzle_temper=200.02,
            trays=trays[::-1],
            last_report_at="t1",
        )
        await asyncio.sleep(0.4)
        _set(
            fast, pid, gcode_state="RUNNING", nozzle_temper=200.02, trays=trays, last_report_at="t2"
        )

    task = asyncio.create_task(mutate())
    evs, _ = await _events(live_client, h, until=lambda e, p: len(e) >= 2)
    await task
    assert evs[0]["type"] == "printer.status"
    assert evs[1] == {
        "type": "printer.delta",
        "printer_id": pid,
        "patch": {"gcode_state": "RUNNING"},
    }


async def test_print_job_event_filtered_by_allowlist(live_client, fast, redis_url):  # noqa: F811
    p1 = await _mk_printer(live_client, "SERE1XYZ")
    p2 = await _mk_printer(live_client, "SERE2XYZ")
    key = await _mint(live_client, printer_ids=[p1])
    h = {"X-API-Key": key}

    async def publish():
        await asyncio.sleep(0.4)
        for pid, jid in ((p2, 1), (p1, 2)):
            fast.publish(
                CHANNEL,
                json.dumps(
                    {
                        "type": "print_job.updated",
                        "print_job_id": jid,
                        "printer_id": pid,
                        "state": "printing",
                    }
                ),
            )
        fast.publish(CHANNEL, json.dumps({"type": "job.updated", "job_id": "x"}))

    task = asyncio.create_task(publish())
    evs, _ = await _events(
        live_client, h, until=lambda e, p: any(x["type"] == "print_job.updated" for x in e)
    )
    await task
    pj = [e for e in evs if e["type"] == "print_job.updated"]
    assert pj == [
        {"type": "print_job.updated", "print_job_id": 2, "printer_id": p1, "state": "printing"}
    ]
    assert all(e["type"] in ("printer.status", "print_job.updated") for e in evs)


async def test_ping_interval(live_client, fast):  # noqa: F811
    key = await _mint(live_client)
    _, pings = await _events(live_client, {"X-API-Key": key}, until=lambda e, p: p >= 2)
    assert pings >= 2


async def test_session_with_printer_ids_drops_foreign_print_job(live_client, fast):  # noqa: F811
    p1 = await _mk_printer(live_client, "SERF1XYZ")
    p2 = await _mk_printer(live_client, "SERF2XYZ")

    async def publish():
        await asyncio.sleep(0.4)
        for pid, jid in ((p2, 1), (p1, 2)):
            ev = {"type": "print_job.updated", "print_job_id": jid, "printer_id": pid, "state": "x"}
            fast.publish(CHANNEL, json.dumps(ev))

    task = asyncio.create_task(publish())
    evs, _ = await _events(
        live_client,
        params={"printer_ids": str(p1)},
        until=lambda e, p: any(x["type"] == "print_job.updated" for x in e),
    )
    await task
    assert [e["print_job_id"] for e in evs if e["type"] == "print_job.updated"] == [2]


async def test_revoked_key_ends_stream_and_ratelimit_headers(live_client, monkeypatch):  # noqa: F811
    from app.api import events

    monkeypatch.setattr(events, "RECHECK_S", 0.2)
    await _mk_printer(live_client, "SERR1XYZ")
    r = await live_client.post("/api/settings/api-keys", json={"label": "rv", "scopes": ["read"]})
    key, kid = r.json()["key"], r.json()["id"]
    async with live_client.stream("GET", "/api/events", headers={"X-API-Key": key}) as resp:
        assert resp.status_code == 200
        assert "x-ratelimit-limit" in resp.headers
        d = await live_client.delete(f"/api/settings/api-keys/{kid}")
        assert d.status_code in (200, 204), d.text

        async def drain():
            async for _ in resp.aiter_lines():
                pass

        await asyncio.wait_for(drain(), 10)  # stream ends on its own
