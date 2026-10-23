"""Live status + pause/resume/stop command endpoints (SPEC "API surface":
printers .../status/.../pause/resume/stop; M4 Task 7). Status is a
read-only Redis poll of ``app.printers.base.state_key`` (never 500s on a
missing/corrupt key -- ``online: false`` instead); the command endpoints
never touch the adapter/lib, they only publish ``{"command": ...}`` onto
``app.printers.base.command_channel`` for printerd to consume.
"""

from __future__ import annotations

import json
import time

import redis as redis_lib

from app.printers.base import command_channel, state_key

CREATE = {"name": "A1 mini", "host": "192.168.1.50", "serial": "0309ABC", "access_code": "12345678"}


async def _make_printer(client) -> int:
    return (await client.post("/api/printers", json=CREATE)).json()["id"]


async def test_status_503_when_disabled(authenticated_client):
    assert (await authenticated_client.get("/api/printers/1/status")).status_code == 503


async def test_status_offline_when_no_state(authenticated_client, printer_enabled):
    pid = await _make_printer(authenticated_client)
    r = await authenticated_client.get(f"/api/printers/{pid}/status")
    assert r.status_code == 200 and r.json()["online"] is False


async def test_status_404_when_printer_missing(authenticated_client, printer_enabled):
    assert (await authenticated_client.get("/api/printers/999999/status")).status_code == 404


async def test_status_reflects_redis(authenticated_client, printer_enabled, redis_url):
    pid = await _make_printer(authenticated_client)
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(pid), json.dumps({"gcode_state": "RUNNING", "mc_percent": 42, "layer_num": 10}))
    c.close()
    body = (await authenticated_client.get(f"/api/printers/{pid}/status")).json()
    assert body["online"] is True and body["gcode_state"] == "RUNNING"
    assert body["mc_percent"] == 42 and body["layer_num"] == 10


async def test_status_offline_when_state_corrupt(authenticated_client, printer_enabled, redis_url):
    pid = await _make_printer(authenticated_client)
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(pid), "not-json")
    c.close()
    body = (await authenticated_client.get(f"/api/printers/{pid}/status")).json()
    assert body["online"] is False


async def test_pause_publishes_command(authenticated_client, printer_enabled, redis_url):
    pid = await _make_printer(authenticated_client)
    c = redis_lib.Redis.from_url(redis_url)
    ps = c.pubsub()
    ps.subscribe(command_channel(pid))
    while ps.get_message(timeout=0.1):
        pass
    r = await authenticated_client.post(f"/api/printers/{pid}/pause")
    assert r.status_code == 202
    deadline = time.monotonic() + 5
    got = None
    while time.monotonic() < deadline and got is None:
        msg = ps.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if msg and msg["type"] == "message":
            got = json.loads(msg["data"])
    assert got == {"command": "pause"}
    ps.close()
    c.close()


async def test_resume_publishes_command(authenticated_client, printer_enabled, redis_url):
    pid = await _make_printer(authenticated_client)
    c = redis_lib.Redis.from_url(redis_url)
    ps = c.pubsub()
    ps.subscribe(command_channel(pid))
    while ps.get_message(timeout=0.1):
        pass
    r = await authenticated_client.post(f"/api/printers/{pid}/resume")
    assert r.status_code == 202
    deadline = time.monotonic() + 5
    got = None
    while time.monotonic() < deadline and got is None:
        msg = ps.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if msg and msg["type"] == "message":
            got = json.loads(msg["data"])
    assert got == {"command": "resume"}
    ps.close()
    c.close()


async def test_stop_publishes_command(authenticated_client, printer_enabled, redis_url):
    pid = await _make_printer(authenticated_client)
    c = redis_lib.Redis.from_url(redis_url)
    ps = c.pubsub()
    ps.subscribe(command_channel(pid))
    while ps.get_message(timeout=0.1):
        pass
    r = await authenticated_client.post(f"/api/printers/{pid}/stop")
    assert r.status_code == 202
    deadline = time.monotonic() + 5
    got = None
    while time.monotonic() < deadline and got is None:
        msg = ps.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if msg and msg["type"] == "message":
            got = json.loads(msg["data"])
    assert got == {"command": "stop"}
    ps.close()
    c.close()


async def test_command_404_when_printer_missing(authenticated_client, printer_enabled):
    assert (await authenticated_client.post("/api/printers/999999/pause")).status_code == 404


async def test_command_409_when_printer_disabled(authenticated_client, printer_enabled):
    pid = await _make_printer(authenticated_client)
    await authenticated_client.patch(f"/api/printers/{pid}", json={"enabled": False})
    assert (await authenticated_client.post(f"/api/printers/{pid}/pause")).status_code == 409
    assert (await authenticated_client.post(f"/api/printers/{pid}/resume")).status_code == 409
    assert (await authenticated_client.post(f"/api/printers/{pid}/stop")).status_code == 409


async def test_status_v2_fields_from_redis(authenticated_client, printer_enabled, redis_url):
    pid = await _make_printer(authenticated_client)
    c = redis_lib.Redis.from_url(redis_url)
    c.set(
        state_key(pid),
        json.dumps(
            {
                "gcode_state": "IDLE",
                "bed_target": 60.0,
                "fan_part_pct": 53,
                "light_on": True,
                "stage": "Heating nozzle",
                "hms": [{"attr": 1, "code": 2}],
                "firmware": {"ota": "01.07.00.00", "ams": [{"id": 0, "sw_ver": "1"}]},
                "ams_units": [{"id": 0, "humidity": 31.0}],
            }
        ),
    )
    c.close()
    body = (await authenticated_client.get(f"/api/printers/{pid}/status")).json()
    assert body["bed_target"] == 60.0 and body["fan_part_pct"] == 53 and body["light_on"] is True
    assert body["stage"] == "Heating nozzle" and body["hms"] == [{"attr": 1, "code": 2}]
    assert body["firmware"]["ota"] == "01.07.00.00" and body["ams_units"][0]["humidity"] == 31.0
    assert body["chamber_temper"] is None and body["dev_mode"] is None


async def test_printer_out_has_capabilities(authenticated_client, printer_enabled):
    pid = await _make_printer(authenticated_client)  # serial 0309ABC -> A1 mini by prefix
    body = (await authenticated_client.get(f"/api/printers/{pid}")).json()
    assert body["capabilities"]["model_key"] == "a1mini"
    assert body["capabilities"]["verified"] is True
