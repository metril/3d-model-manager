"""Q2 control routes: 200/409/422/202 mapping, perms, audit rows, Dev-Mode
short-circuit, objects + warnings."""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile

import pytest
import redis as redis_lib
from sqlalchemy import select

from app.api import printer_control
from app.models import AuditLog, Group, GroupMember, PrinterAccess
from app.printers.base import cmd_result_key, command_channel, state_key
from app.printers.gcode3mf import printable_objects

CREATE = {
    "name": "A1 mini",
    "model": "A1 mini",
    "host": "192.168.1.50",
    "serial": "0309ABC",
    "access_code": "12345678",
}
IDLE = {"gcode_state": "IDLE", "nozzle_temper": 200.0, "dev_mode": True}


async def _printer(client, **extra) -> int:
    r = await client.post("/api/printers", json={**CREATE, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture(autouse=True)
def rds(redis_url):
    c = redis_lib.Redis.from_url(redis_url)
    c.flushdb()  # printer ids restart per test; drop state left by earlier tests
    yield c
    c.close()


@pytest.fixture
def set_state(rds):
    def _set(pid, **state):
        rds.set(state_key(pid), json.dumps(state))

    return _set


@pytest.fixture
def printerd_stub(redis_url):
    """A fake printerd: records envelopes, answers each with ``result``."""
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
                    if result is not None:
                        c.set(cmd_result_key(pid, env["request_id"]), json.dumps(result), ex=30)
            ps.close()
            c.close()

        threading.Thread(target=loop, daemon=True).start()
        time.sleep(0.2)  # let the subscription land
        return received

    yield start
    for s in stops:
        s.set()


async def test_ok_envelope_and_result(
    authenticated_client, printer_enabled, set_state, printerd_stub
):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    got = printerd_stub(pid, {"ok": True})
    r = await authenticated_client.post(
        f"/api/printers/{pid}/control/temperature", json={"target": "bed", "value": 60}
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert got[0]["command"] == "set_temperature"
    assert got[0]["args"] == {"target": "bed", "value": 60} and got[0]["request_id"]


@pytest.mark.parametrize(
    "path,body",
    [
        ("temperature", {"target": "chamber", "value": 40}),
        ("fan", {"fan": "aux", "pct": 50}),
        ("fan", {"fan": "chamber", "pct": 50}),
        ("airduct", {"mode": "heating"}),
    ],
)
async def test_capability_gate_422(authenticated_client, printer_enabled, set_state, path, body):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    r = await authenticated_client.post(f"/api/printers/{pid}/control/{path}", json=body)
    assert r.status_code == 422 and r.json()["code"] == "unsupported"


async def test_invalid_args_422_and_over_max(authenticated_client, printer_enabled, set_state):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    r = await authenticated_client.post(f"/api/printers/{pid}/control/speed", json={"level": 7})
    assert r.status_code == 422 and r.json()["code"] == "invalid"
    r = await authenticated_client.post(
        f"/api/printers/{pid}/control/temperature", json={"target": "bed", "value": 100}
    )  # A1 mini max_bed = 80
    assert r.status_code == 422 and r.json()["code"] == "invalid"


async def test_offline_409(authenticated_client, printer_enabled):
    pid = await _printer(authenticated_client)
    r = await authenticated_client.post(f"/api/printers/{pid}/control/home")
    assert r.status_code == 409 and r.json()["code"] == "offline"


async def test_dev_mode_off_short_circuits_but_pause_still_sent(
    authenticated_client, printer_enabled, set_state, printerd_stub
):
    pid = await _printer(authenticated_client)
    set_state(pid, **{**IDLE, "dev_mode": False})
    got = printerd_stub(pid)
    r = await authenticated_client.post(f"/api/printers/{pid}/control/light", json={"on": True})
    assert r.status_code == 409 and r.json()["code"] == "dev_mode_off"
    r = await authenticated_client.post(f"/api/printers/{pid}/pause")
    assert r.status_code == 202
    time.sleep(0.4)
    assert [e["command"] for e in got] == ["pause"]


async def test_printerd_error_codes_map_to_http(
    authenticated_client, printer_enabled, set_state, printerd_stub
):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    printerd_stub(pid, {"ok": False, "code": "dev_mode_off", "message": "verify failed"})
    r = await authenticated_client.post(f"/api/printers/{pid}/control/light", json={"on": True})
    assert r.status_code == 409 and r.json()["code"] == "dev_mode_off"


async def test_pending_202_when_no_result(
    authenticated_client, printer_enabled, set_state, monkeypatch
):
    monkeypatch.setattr(printer_control, "RESULT_WAIT_S", 0.3)
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    r = await authenticated_client.post(f"/api/printers/{pid}/control/home")
    assert r.status_code == 202 and r.json()["status"] == "pending" and r.json()["request_id"]


async def test_gcode_admin_only_size_limit_and_audit(
    authenticated_client,
    member_client,
    member,
    db_session,
    printer_enabled,
    set_state,
    printerd_stub,
):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    group = Group(name="ctl", permissions=["printers:control"])
    db_session.add(group)
    await db_session.flush()
    db_session.add_all(
        [
            GroupMember(group_id=group.id, user_id=member.id),
            PrinterAccess(group_id=group.id, printer_id=pid, can_control=True),
        ]
    )
    await db_session.commit()
    printerd_stub(pid, {"ok": True})
    # member with control may light, but not raw gcode
    r = await member_client.post(f"/api/printers/{pid}/control/light", json={"on": True})
    assert r.status_code == 200, r.text
    r = await member_client.post(f"/api/printers/{pid}/control/gcode", json={"gcode": "G28"})
    assert r.status_code == 403
    r = await authenticated_client.post(
        f"/api/printers/{pid}/control/gcode", json={"gcode": "x" * 1025}
    )
    assert r.status_code == 422
    r = await authenticated_client.post(f"/api/printers/{pid}/control/gcode", json={"gcode": "G28"})
    assert r.status_code == 200
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert [(a.action, a.printer_id, a.payload) for a in rows] == [("gcode", pid, {"gcode": "G28"})]
    assert rows[0].user_id == rows[0].created_by_user_id is not None


async def test_member_without_acl_403(member_client, authenticated_client, printer_enabled):
    pid = await _printer(authenticated_client)
    r = await member_client.post(f"/api/printers/{pid}/control/home")
    assert r.status_code == 403


async def test_jog_guards_clamp_and_audit(
    authenticated_client, db_session, printer_enabled, set_state, printerd_stub
):
    pid = await _printer(authenticated_client)
    got = printerd_stub(pid, {"ok": True})
    set_state(pid, **{**IDLE, "gcode_state": "RUNNING"})
    r = await authenticated_client.post(
        f"/api/printers/{pid}/control/jog", json={"axis": "X", "mm": 10}
    )
    assert r.status_code == 409 and r.json()["code"] == "printing"
    set_state(pid, **IDLE)
    r = await authenticated_client.post(
        f"/api/printers/{pid}/control/jog", json={"axis": "x", "mm": 7}
    )
    assert r.status_code == 200
    assert got[-1]["args"] == {"axis": "X", "mm": 10.0}
    await authenticated_client.patch(
        f"/api/printers/{pid}", json={"build_volume_mm": {"x": 20, "y": 20, "z": 20}}
    )
    r = await authenticated_client.post(
        f"/api/printers/{pid}/control/jog", json={"axis": "Y", "mm": 50}
    )
    assert r.status_code == 422
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert [a.action for a in rows] == ["jog"]


async def test_extrude_cold_409_hot_ok_with_audit(
    authenticated_client, db_session, printer_enabled, set_state, printerd_stub
):
    pid = await _printer(authenticated_client)
    printerd_stub(pid, {"ok": True})
    set_state(pid, **{**IDLE, "nozzle_temper": 169.0})
    r = await authenticated_client.post(f"/api/printers/{pid}/control/extrude", json={"mm": 5})
    assert r.status_code == 409 and r.json()["code"] == "cold_extrude"
    set_state(pid, **{**IDLE, "nozzle_temper": 170.0})
    r = await authenticated_client.post(f"/api/printers/{pid}/control/extrude", json={"mm": 5})
    assert r.status_code == 200
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert [a.action for a in rows] == ["extrude"]


async def test_skip_objects_requires_print_and_audits(
    authenticated_client, db_session, printer_enabled, set_state, printerd_stub
):
    pid = await _printer(authenticated_client)
    got = printerd_stub(pid, {"ok": True})
    set_state(pid, **IDLE)
    r = await authenticated_client.post(
        f"/api/printers/{pid}/skip-objects", json={"object_ids": [4]}
    )
    assert r.status_code == 409 and r.json()["code"] == "not_printing"
    set_state(pid, **{**IDLE, "gcode_state": "RUNNING"})
    r = await authenticated_client.post(
        f"/api/printers/{pid}/skip-objects", json={"object_ids": [4]}
    )
    assert r.status_code == 200 and got[-1]["args"] == {"object_ids": [4]}
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert [(a.action, a.payload) for a in rows] == [("skip_objects", {"object_ids": [4]})]


async def test_objects_empty_without_job(authenticated_client, printer_enabled):
    pid = await _printer(authenticated_client)
    r = await authenticated_client.get(f"/api/printers/{pid}/objects")
    assert r.status_code == 200 and r.json()["objects"] == []
    assert (await authenticated_client.get("/api/printers/99999/objects")).status_code == 404


def test_printable_objects_parses_slice_info():
    xml = (
        '<config><plate><metadata key="index" value="1"/>'
        '<object identify_id="12" name="Cube" skipped="false"/>'
        '<object identify_id="13" name="Gone" skipped="true"/></plate>'
        '<plate><metadata key="index" value="2"/><object identify_id="40" name="Ball"/></plate>'
        "</config>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/slice_info.config", xml)
    assert printable_objects(io.BytesIO(buf.getvalue())) == [
        {"id": 12, "name": "Cube", "plate": 1},
        {"id": 40, "name": "Ball", "plate": 2},
    ]
    assert printable_objects(io.BytesIO(buf.getvalue()), plate=2) == [
        {"id": 40, "name": "Ball", "plate": 2}
    ]
    assert printable_objects(io.BytesIO(b"nope")) == []


async def test_developer_mode_warnings_respect_visibility(
    authenticated_client, member_client, member, db_session, printer_enabled, set_state
):
    a = await _printer(authenticated_client, name="A", serial="SERIAL01")
    b = await _printer(authenticated_client, name="B", serial="SERIAL02")
    c = await _printer(authenticated_client, name="C", serial="SERIAL03")
    set_state(a, **{**IDLE, "dev_mode": False, "dev_mode_reason": "fun_bit"})
    set_state(b, **{**IDLE, "dev_mode": False})
    set_state(c, **{**IDLE, "dev_mode": None})
    group = Group(name="vis", permissions=["printers:control"])
    db_session.add(group)
    await db_session.flush()
    db_session.add_all(
        [
            GroupMember(group_id=group.id, user_id=member.id),
            PrinterAccess(group_id=group.id, printer_id=a, can_control=True),
        ]
    )
    await db_session.commit()
    admin = (await authenticated_client.get("/api/printers/developer-mode-warnings")).json()
    assert sorted(w["printer_id"] for w in admin) == sorted([a, b])
    mine = (await member_client.get("/api/printers/developer-mode-warnings")).json()
    assert mine == [{"printer_id": a, "name": "A", "reason": "fun_bit"}]
