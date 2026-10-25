# ruff: noqa: F811  (pytest fixtures imported from test_printer_control_api)
"""T1 AMS drying: payloads, preflight, state parsing, capabilities, settings,
schedules and the ``drying.tick`` state machine."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import respx
from sqlalchemy import select

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import AuditLog, Printer, ScheduledDrying, Setting, SmartPlug
from app.models.enums import PrinterKind
from app.printers import bambu
from app.printers import commands as cmds
from app.printers.base import state_key
from app.printers.capabilities import capabilities_for
from app.services import dispatcher, drying, drying_tick
from app.services.drying_settings import DryingConfig
from app.tasks import base
from tests.cassettes import bambu_snapshots as snap
from tests.test_printer_control_api import (  # noqa: F401  (fixtures)
    CREATE,
    IDLE,
    printerd_stub,
    rds,
    set_state,
)
from tests.test_printer_ops import _adapter

H2D = {"model": "H2D", "serial": "09400A1B2C3D4E5"}
FW_OK = {"ota": "01.03.00.00"}
UNIT = {"id": 0, "humidity": 55.0, "dry_status": 0, "dry_sf_reason": []}
TRAYS = [{"slot": 0, "ams_unit": 0, "tray": 0, "tray_type": "PETG"}]


def _state(**kw) -> dict:
    return {
        **IDLE,
        "firmware": FW_OK,
        "ams_units": [dict(UNIT)],
        "trays": TRAYS,
        **kw,
    }


async def _h2d(client, **extra) -> int:
    r = await client.post("/api/printers", json={**CREATE, **H2D, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# -- payloads / commands -------------------------------------------------------


def test_payload_goldens():
    start = bambu.drying_payload(0, 65, 12, "PETG", True, start=True)
    assert start == {
        "print": {
            "sequence_id": "0",
            "command": "ams_filament_drying",
            "ams_id": 0,
            "temp": 65,
            "cooling_temp": 20,
            "duration": 12,
            "humidity": 0,
            "mode": 1,
            "rotate_tray": True,
            "filament": "PETG",
            "close_power_conflict": False,
        }
    }
    stop = bambu.drying_payload(128, 0, 0, "", False, start=False)["print"]
    assert stop["ams_id"] == 128  # AMS-HT ids pass through
    assert (stop["mode"], stop["cooling_temp"], stop["temp"], stop["duration"]) == (0, 0, 0, 0)
    assert stop["humidity"] == 0 and stop["close_power_conflict"] is False


def test_adapter_publishes_and_caches_target(monkeypatch):
    a, sent = _adapter(monkeypatch, snap.AMS_DRYING_DUMP)
    clock = [1000.0]
    monkeypatch.setattr(bambu.time, "time", lambda: clock[0])
    a.drying_start(0, 65, 12, "PETG", False)
    assert sent[0]["print"]["command"] == "ams_filament_drying"
    unit = a._snapshot()["ams_units"][0]
    assert (unit["dry_target_temp"], unit["dry_filament"], unit["dry_started_at"]) == (
        65,
        "PETG",
        1000.0,
    )
    a.drying_stop(0)
    assert sent[1]["print"]["mode"] == 0
    assert "dry_target_temp" not in a._snapshot()["ams_units"][0]


def test_target_cleared_when_status_returns_to_zero(monkeypatch):
    dump = json.loads(json.dumps(snap.AMS_DRYING_DUMP))
    a, _ = _adapter(monkeypatch, dump)
    clock = [1000.0]
    monkeypatch.setattr(bambu.time, "time", lambda: clock[0])
    a.drying_start(1, 55, 8, "PLA", False)  # unit 1 reports status 0 right after the command
    assert a._snapshot()["ams_units"][1]["dry_target_temp"] == 55  # grace: status lags
    clock[0] += bambu.DRY_TARGET_GRACE_S + 1
    assert "dry_target_temp" not in a._snapshot()["ams_units"][1]
    a.drying_start(0, 65, 12, "PETG", False)  # unit 0 is active (status 2) -> seen
    assert a._snapshot()["ams_units"][0]["dry_target_temp"] == 65
    dump["print"]["ams"]["ams"][0]["info"] = "3"  # cycle ended
    assert "dry_target_temp" not in a._snapshot()["ams_units"][0]


def test_command_validation():
    ok = cmds.validate("drying_start", {"ams_id": 128, "temp": 85, "duration_hours": 24})
    assert ok == {
        "ams_id": 128,
        "temp": 85,
        "duration_hours": 24,
        "filament": "",
        "rotate_tray": False,
    }
    assert cmds.validate("drying_stop", {"ams_id": 3}) == {"ams_id": 3}
    for bad in (
        {"ams_id": 0, "temp": 44, "duration_hours": 12},
        {"ams_id": 0, "temp": 86, "duration_hours": 12},
        {"ams_id": 0, "temp": 60, "duration_hours": 0},
        {"ams_id": 0, "temp": 60, "duration_hours": 25},
        {"ams_id": 4, "temp": 60, "duration_hours": 12},
        {"ams_id": 136, "temp": 60, "duration_hours": 12},
        {"ams_id": 255, "temp": 60, "duration_hours": 12},
        {"ams_id": 0, "temp": 60, "duration_hours": 12, "rotate_tray": "yes"},
    ):
        with pytest.raises(cmds.CommandError) as e:
            cmds.validate("drying_start", bad)
        assert e.value.code == "invalid"


# -- preflight -----------------------------------------------------------------


def _pre(model_key="h2d", fw="01.03.00.00", state=None, **kw):
    return drying.validate_preflight(
        model_key=model_key,
        firmware=fw,
        state=state or _state(),
        ams_id=kw.pop("ams_id", 0),
        filament=kw.pop("filament", ""),
        **kw,
    )


def test_preflight_matrix():
    assert _pre()["filament"] == "PETG"  # backfilled from the loaded tray
    assert _pre(filament="ABS")["filament"] == "ABS"
    assert _pre(state=_state(trays=[]))["filament"] == "PLA"
    for model, code, http in (("a1mini", "unsupported", 422), ("a1", "unsupported", 422)):
        with pytest.raises(drying.DryingRefused) as e:
            _pre(model_key=model)
        assert (e.value.code, e.value.http) == (code, http)
    for model in ("p1p", "p1s"):
        with pytest.raises(drying.DryingRefused) as e:
            _pre(model_key=model)
        assert (e.value.code, e.value.http) == ("screen_only", 409)
    with pytest.raises(drying.DryingRefused) as e:  # firmware below the h2d minimum
        _pre(fw="01.01.00.00")
    assert e.value.code == "unsupported"
    with pytest.raises(drying.DryingRefused) as e:
        _pre(ams_id=7)
    assert e.value.code == "invalid"
    blocked = _state(ams_units=[{**UNIT, "dry_sf_reason": [1, 3, 99]}])
    with pytest.raises(drying.DryingRefused) as e:
        _pre(state=blocked)
    assert e.value.code == "blocked" and e.value.extra["codes"] == [1, 3]
    assert len(e.value.extra["messages"]) == 2
    with pytest.raises(drying.DryingRefused) as e:  # printing, fw too old for print+dry
        _pre(model_key="x1", fw="01.09.00.00", state=_state(gcode_state="RUNNING"))
    assert e.value.code == "printing"
    assert _pre(state=_state(gcode_state="RUNNING"))["filament"] == "PETG"  # h2d >= 01.03
    # stop never needs a free unit but still needs support
    assert _pre(state=blocked, stop=True) == {"filament": ""}
    with pytest.raises(drying.DryingRefused):
        _pre(model_key="p1s", stop=True)


def test_state_preflight_hook_backfills_and_blocks():
    clean = cmds.validate("drying_start", {"ams_id": 0, "temp": 65, "duration_hours": 12})
    cmds.DISPATCH["drying_start"].preflight(clean, _state())
    assert clean["filament"] == "PETG"
    with pytest.raises(cmds.CommandError) as e:
        cmds.DISPATCH["drying_start"].preflight(
            clean, _state(ams_units=[{**UNIT, "dry_sf_reason": [8]}])
        )
    assert e.value.code == "blocked" and e.value.extra["codes"] == [8]


# -- state parsing / humidity ----------------------------------------------------


def test_info_bits_parsed_from_fixture():
    units = {u["id"]: u for u in bambu._ams_units(snap.AMS_DRYING_DUMP["print"], None)}
    assert (units[0]["dry_status"], units[0]["dry_sub_status"]) == (2, 1)
    assert units[0]["dry_time_min"] == 540 and units[0]["dry_sf_reason"] == [6]
    assert units[0]["humidity"] == 31.0 and units[0]["humidity_index"] == 4
    assert units[1]["dry_status"] == 0 and units[1]["dry_sub_status"] == 0
    assert units[128]["dry_sf_reason"] == [3, 8]  # "x" dropped, "8" coerced
    assert units[129]["dry_status"] == 4 and units[129]["humidity_index"] is None


def test_humidity_never_falls_back_to_the_inverted_index():
    units = {u["id"]: u for u in bambu._ams_units(snap.AMS_DRYING_DUMP["print"], None)}
    assert units[1]["humidity"] is None and units[1]["humidity_index"] == 2
    assert drying.ams_humidity_percent({"humidity": "2"}) is None
    assert drying.ams_humidity_percent({"humidity_raw": "47"}) == 47.0
    assert drying.ams_humidity_percent({"humidity_raw": "n/a"}) is None
    assert drying.ams_humidity_percent(None) is None


def test_unparseable_info_degrades():
    u = bambu._ams_units({"ams": {"ams": [{"id": "0", "info": "zz"}]}}, None)[0]
    assert u["dry_status"] is None and u["dry_sf_reason"] == []


# -- capabilities ---------------------------------------------------------------


def test_capability_firmware_gating():
    assert not capabilities_for("A1 mini").ams_drying
    assert not capabilities_for("A1").ams_drying
    p1s = capabilities_for("P1S")
    assert p1s.drying_screen_only and not p1s.ams_drying
    assert capabilities_for("X1C").ams_drying  # model only, firmware unknown
    assert not capabilities_for("X1C", None, "01.08.00.00").ams_drying
    assert capabilities_for("X1C", None, "01.09.00.00").ams_drying
    assert not capabilities_for("X1C", None, "01.09.00.00").ams_drying_while_printing
    assert capabilities_for("X1C", None, "01.11.02.00").ams_drying_while_printing
    assert capabilities_for("X1E", None, "01.00.00.00").ams_drying  # no minimum row
    assert not capabilities_for("X1E", None, "99").ams_drying_while_printing
    assert not capabilities_for("H2D", None, "01.02.00.00").ams_drying
    h2d = capabilities_for("H2D", None, "01.03.00.00")
    assert h2d.ams_drying and h2d.ams_drying_while_printing
    assert capabilities_for("Mystery").ams_drying is False
    assert capabilities_for("H2D").verified is False  # never claimed live-verified


async def test_status_route_is_firmware_aware(authenticated_client, printer_enabled, set_state):
    pid = await _h2d(authenticated_client)
    set_state(pid, **_state(firmware={"ota": "01.01.00.00"}))
    st = (await authenticated_client.get(f"/api/printers/{pid}/status")).json()
    assert st["capabilities"]["ams_drying"] is False
    set_state(pid, **_state(ams_units=[{**UNIT, "dry_status": 2, "dry_time_min": 30}]))
    st = (await authenticated_client.get(f"/api/printers/{pid}/status")).json()
    assert st["capabilities"]["ams_drying"] is True
    assert st["ams_units"][0]["dry_status"] == 2 and st["ams_units"][0]["dry_time_min"] == 30
    listed = (await authenticated_client.get("/api/printers")).json()[0]["capabilities"]
    assert listed["ams_drying"] is True  # PrinterOut: model gate only


# -- control routes --------------------------------------------------------------

START = {"ams_id": 0, "temp": 65, "duration_hours": 12}


async def test_start_stop_route_and_audit(
    authenticated_client, printer_enabled, set_state, printerd_stub
):
    pid = await _h2d(authenticated_client)
    set_state(pid, **_state())
    got = printerd_stub(pid, {"ok": True})
    r = await authenticated_client.post(f"/api/printers/{pid}/drying/start", json=START)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert got[0]["command"] == "drying_start"
    assert got[0]["args"] == {**START, "filament": "PETG", "rotate_tray": False}
    r = await authenticated_client.post(f"/api/printers/{pid}/drying/stop", json={"ams_id": 0})
    assert r.status_code == 200 and got[1]["command"] == "drying_stop"
    with base.sync_session() as s:
        actions = [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert actions == ["drying_start", "drying_stop"]


async def test_route_gate_matrix(authenticated_client, printer_enabled, set_state, printerd_stub):
    # A1 mini: unsupported (422)
    a1 = (await authenticated_client.post("/api/printers", json=CREATE)).json()["id"]
    set_state(a1, **_state())
    r = await authenticated_client.post(f"/api/printers/{a1}/drying/start", json=START)
    assert r.status_code == 422 and r.json()["code"] == "unsupported"
    # P1S: screen only (409)
    p1s = (
        await authenticated_client.post(
            "/api/printers", json={**CREATE, "model": "P1S", "serial": "01P00A1B2C3D4E5"}
        )
    ).json()["id"]
    set_state(p1s, **_state())
    for verb, body in (("start", START), ("stop", {"ams_id": 0})):
        r = await authenticated_client.post(f"/api/printers/{p1s}/drying/{verb}", json=body)
        assert r.status_code == 409 and r.json()["code"] == "screen_only"
    # H2D
    pid = await _h2d(authenticated_client)
    set_state(pid, **_state(ams_units=[{**UNIT, "dry_sf_reason": [3, 8]}]))
    r = await authenticated_client.post(f"/api/printers/{pid}/drying/start", json=START)
    j = r.json()
    assert r.status_code == 409 and j["code"] == "blocked" and j["codes"] == [3, 8]
    assert len(j["messages"]) == 2
    set_state(pid, **_state(firmware={"ota": "01.01.00.00"}))
    r = await authenticated_client.post(f"/api/printers/{pid}/drying/start", json=START)
    assert r.status_code == 422 and r.json()["code"] == "unsupported"
    set_state(pid, **_state(gcode_state="RUNNING", firmware={"ota": "01.03.00.00"}))
    printerd_stub(pid, {"ok": True})  # H2D >= 01.03 may dry while printing
    r = await authenticated_client.post(f"/api/printers/{pid}/drying/start", json=START)
    assert r.status_code == 200
    for bad in ({**START, "temp": 90}, {**START, "duration_hours": 0}, {**START, "ams_id": 9}):
        r = await authenticated_client.post(f"/api/printers/{pid}/drying/start", json=bad)
        assert r.status_code == 422 and r.json()["code"] == "invalid"
    r = await authenticated_client.post(
        f"/api/printers/{pid}/drying/start", json={**START, "ams_id": 2}
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid"  # unit 2 not reported


async def test_start_refused_while_printing_without_cap(
    authenticated_client, printer_enabled, set_state
):
    x1 = (
        await authenticated_client.post(
            "/api/printers", json={**CREATE, "model": "X1C", "serial": "00M00A1B2C3D4E5"}
        )
    ).json()["id"]
    set_state(x1, **_state(gcode_state="RUNNING", firmware={"ota": "01.09.00.00"}))
    r = await authenticated_client.post(f"/api/printers/{x1}/drying/start", json=START)
    assert r.status_code == 409 and r.json()["code"] == "printing"


async def test_control_requires_perm(viewer_client, printer_enabled):
    r = await viewer_client.post("/api/printers/1/drying/start", json=START)
    assert r.status_code == 403


# -- settings --------------------------------------------------------------------


async def test_settings_defaults_roundtrip_and_validation(authenticated_client, viewer_client):
    d = (await authenticated_client.get("/api/settings/drying")).json()
    assert d["presets"]["PLA"] == {"n3f_temp": 45, "n3s_temp": 45, "n3f_hours": 12, "n3s_hours": 12}
    assert d["presets"]["TPU"] == {"n3f_temp": 65, "n3s_temp": 75, "n3f_hours": 12, "n3s_hours": 18}
    assert set(d["presets"]) == {"PLA", "PETG", "TPU", "ABS", "ASA", "PA", "PC", "PVA"}
    assert (d["ams_humidity_good"], d["ams_humidity_fair"]) == (40, 60)
    assert d["per_type_thresholds"] == {} and d["ambient_sustained_min"] == 30
    assert not any(
        d[k]
        for k in (
            "ambient_drying_enabled",
            "queue_drying_enabled",
            "queue_drying_block",
            "print_drying_enabled",
        )
    )
    assert (await viewer_client.get("/api/settings/drying")).status_code == 200
    assert (await viewer_client.put("/api/settings/drying", json=d)).status_code == 403

    d["queue_drying_enabled"] = True
    d["per_type_thresholds"] = {"pla": 35, "default": 55}
    d["presets"]["PLA"]["n3f_temp"] = 50
    r = await authenticated_client.put("/api/settings/drying", json=d)
    assert r.status_code == 200 and r.json()["per_type_thresholds"] == {"PLA": 35, "default": 55}
    eff = (await authenticated_client.get("/api/drying/presets")).json()["presets"]
    assert eff["PLA"]["n3f_temp"] == 50

    for mut in (
        lambda x: x["presets"]["PLA"].update(n3f_temp=44),
        lambda x: x["presets"]["PLA"].update(n3s_temp=86),
        lambda x: x["presets"]["PLA"].update(n3f_hours=0),
        lambda x: x["presets"]["PLA"].update(n3s_hours=25),
        lambda x: x.update(ams_humidity_fair=101),
        lambda x: x.update(ambient_sustained_min=0),
        lambda x: x.update(per_type_thresholds={"PLA": 101}),
    ):
        bad = json.loads(json.dumps(d))
        mut(bad)
        assert (await authenticated_client.put("/api/settings/drying", json=bad)).status_code == 422


# -- schedules CRUD ---------------------------------------------------------------

SCHED = {"ams_id": 0, "temp": 65, "duration_hours": 12}


async def test_schedule_crud_and_perms(authenticated_client, viewer_client, printer_enabled):
    pid = await _h2d(authenticated_client)
    when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    r = await authenticated_client.post(
        f"/api/printers/{pid}/drying/schedules", json={**SCHED, "start_after": when}
    )
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["status"] == "pending" and row["source"] == "manual" and row["filament"] == ""
    assert row["created_by_user_id"] is not None
    lst = (await authenticated_client.get(f"/api/printers/{pid}/drying/schedules")).json()
    assert [x["id"] for x in lst] == [row["id"]]
    only = await authenticated_client.get(
        f"/api/printers/{pid}/drying/schedules", params={"status": "done"}
    )
    assert only.json() == []
    assert (
        await viewer_client.post(f"/api/printers/{pid}/drying/schedules", json=SCHED)
    ).status_code == 403
    assert (await viewer_client.delete(f"/api/drying/schedules/{row['id']}")).status_code == 403
    r = await authenticated_client.delete(f"/api/drying/schedules/{row['id']}")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (
        await authenticated_client.delete(f"/api/drying/schedules/{row['id']}")
    ).status_code == 409
    assert (await authenticated_client.delete("/api/drying/schedules/9999")).status_code == 404
    bad = await authenticated_client.post(
        f"/api/printers/{pid}/drying/schedules", json={**SCHED, "temp": 90}
    )
    assert bad.status_code == 422
    with base.sync_session() as s:
        actions = [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert actions == ["drying_schedule_create", "drying_schedule_cancel"]


async def test_schedule_create_capability_preflight(authenticated_client, printer_enabled):
    a1 = (await authenticated_client.post("/api/printers", json=CREATE)).json()["id"]
    r = await authenticated_client.post(f"/api/printers/{a1}/drying/schedules", json=SCHED)
    assert r.status_code == 422
    p1s = (
        await authenticated_client.post(
            "/api/printers", json={**CREATE, "model": "P1S", "serial": "01P00A1B2C3D4E5"}
        )
    ).json()["id"]
    r = await authenticated_client.post(f"/api/printers/{p1s}/drying/schedules", json=SCHED)
    assert r.status_code == 409


async def test_running_schedule_cannot_be_cancelled(authenticated_client, printer_enabled):
    pid = await _h2d(authenticated_client)
    with base.sync_session() as s:
        row = ScheduledDrying(
            printer_id=pid, ams_id=0, temp=65, duration_hours=12, status="running"
        )
        s.add(row)
        s.commit()
        sid = row.id
    r = await authenticated_client.delete(f"/api/drying/schedules/{sid}")
    assert r.status_code == 409


def test_plug_flag_default_and_cascade(rds):
    with base.sync_session() as s:
        p = _db_printer(s)
        plug = _plug(s, p.id)
        assert plug.auto_off_after_drying is False
        s.add(ScheduledDrying(printer_id=p.id, ams_id=0, temp=60, duration_hours=2))
        s.commit()
        s.delete(p)
        s.commit()
        assert s.scalars(select(ScheduledDrying)).all() == []


# -- tick state machine ------------------------------------------------------------


def _db_printer(s, **kw) -> Printer:
    p = Printer(
        name="dry",
        kind=PrinterKind.BAMBU_LAN,
        host="h",
        serial="09400A1B2C3D4E5",
        model="H2D",
        access_code_enc=encrypt_secret(get_settings(), "1"),
        enabled=True,
        **kw,
    )
    s.add(p)
    s.commit()
    return p


def _plug(s, pid, **kw) -> SmartPlug:
    cfg = {"on_url": "http://plug/on", "off_url": "http://plug/off", "status_url": "http://plug/s"}
    plug = SmartPlug(
        printer_id=pid,
        kind="rest",
        config_enc=encrypt_secret(get_settings(), json.dumps(cfg)),
        **kw,
    )
    s.add(plug)
    s.commit()
    return plug


def _put_cfg(**kw) -> None:
    with base.sync_session() as s:
        s.add(Setting(key="drying", value=DryingConfig(**kw).model_dump()))
        s.commit()


@pytest.fixture
def bus(monkeypatch):
    sent: list[ScheduledDrying | tuple] = []
    events: list[tuple] = []
    monkeypatch.setattr(
        drying_tick, "_send_start", lambda r, row, fil: sent.append((row.id, row.ams_id, fil))
    )
    monkeypatch.setattr(
        drying_tick.events, "emit", lambda ev, pid=None, payload=None: events.append((ev, pid))
    )
    return sent, events


def _put_state(r, pid, **kw):
    r.set(state_key(pid), json.dumps(_state(**kw)))


def _tick(r, now):
    with base.sync_session() as s:
        return drying_tick.tick(s, r, get_settings(), now=now)


def _row(sid) -> ScheduledDrying:
    with base.sync_session() as s:
        row = s.get(ScheduledDrying, sid)
        s.expunge(row)
        return row


def test_tick_due_to_running_to_done(rds, bus):
    sent, events = bus
    t0 = datetime.now(UTC)
    with base.sync_session() as s:
        pid = _db_printer(s).id
        future = ScheduledDrying(
            printer_id=pid, ams_id=0, temp=65, duration_hours=2, start_after=t0 + timedelta(hours=1)
        )
        due = ScheduledDrying(printer_id=pid, ams_id=0, temp=65, duration_hours=2)
        s.add_all([future, due])
        s.commit()
        fid, did = future.id, due.id
    _put_state(rds, pid)
    counts = _tick(rds, t0)
    assert counts["started"] == 1 and sent == [(did, 0, "PETG")]
    assert _row(did).status == "running" and _row(fid).status == "pending"
    # unit starts drying -> started event, still running
    _put_state(rds, pid, ams_units=[{**UNIT, "dry_status": 2}])
    _tick(rds, t0 + timedelta(minutes=1))
    assert _row(did).status == "running" and ("drying_started", pid) in events
    # drying ends -> done + finished event
    _put_state(rds, pid)
    counts = _tick(rds, t0 + timedelta(minutes=130))
    assert _row(did).status == "done" and counts["done"] == 1
    assert _row(did).finished_at is not None and ("drying_finished", pid) in events
    # the future row starts once due, but the unit's earlier run is over
    _tick(rds, t0 + timedelta(hours=2))
    assert _row(fid).status == "running"
    with base.sync_session() as s:
        assert "drying_start" in [a.action for a in s.scalars(select(AuditLog))]


def test_tick_did_not_start(rds, bus):
    _, events = bus
    t0 = datetime.now(UTC)
    with base.sync_session() as s:
        pid = _db_printer(s).id
        s.add(ScheduledDrying(printer_id=pid, ams_id=0, temp=65, duration_hours=2))
        s.commit()
    _put_state(rds, pid)
    _tick(rds, t0)
    _tick(rds, t0 + timedelta(seconds=60))  # status still 0, within the window
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).one().status == "running"
    counts = _tick(rds, t0 + timedelta(seconds=95))
    with base.sync_session() as s:
        row = s.scalars(select(ScheduledDrying)).one()
    assert (row.status, row.error_code, counts["failed"]) == ("failed", "did_not_start", 1)
    assert ("drying_failed", pid) in events


def test_tick_waiting_and_failed_reasons(rds, bus):
    sent, _ = bus
    t0 = datetime.now(UTC)
    with base.sync_session() as s:
        pid = _db_printer(s).id
        s.add(ScheduledDrying(printer_id=pid, ams_id=0, temp=65, duration_hours=2))
        s.commit()
    # offline (no state)
    _tick(rds, t0)
    with base.sync_session() as s:
        row = s.scalars(select(ScheduledDrying)).one()
        assert (row.status, row.waiting_reason) == ("waiting", "printer_offline")
    _put_state(rds, pid, ams_units=[{**UNIT, "dry_sf_reason": [1]}])
    _tick(rds, t0)
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).one().waiting_reason == "ams_power_required"
    _put_state(rds, pid, ams_units=[{**UNIT, "dry_sf_reason": [3]}])
    _tick(rds, t0)
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).one().waiting_reason == "ams_retract_filament"
    _put_state(rds, pid, ams_units=[{**UNIT, "dry_sf_reason": [2]}])
    _tick(rds, t0)
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).one().waiting_reason == "ams_blocked"
    assert sent == []
    _put_state(rds, pid)  # clears -> starts
    _tick(rds, t0)
    assert len(sent) == 1
    # unsupported model fails the row immediately
    with base.sync_session() as s:
        s.get(Printer, pid).model = "A1 mini"
        s.scalars(select(ScheduledDrying)).one().status = "done"
        s.add(ScheduledDrying(printer_id=pid, ams_id=0, temp=65, duration_hours=2))
        s.commit()
    _tick(rds, t0)
    with base.sync_session() as s:
        last = s.scalars(select(ScheduledDrying).order_by(ScheduledDrying.id.desc())).first()
        assert (last.status, last.error_code) == ("failed", "unsupported")


def test_tick_ambient_needs_sustained_humidity(rds, bus):
    sent, _ = bus
    t0 = datetime.now(UTC)
    _put_cfg(ambient_drying_enabled=True, ambient_sustained_min=30, ams_humidity_fair=50)
    with base.sync_session() as s:
        pid = _db_printer(s).id
    _put_state(rds, pid)  # 55 % >= 50
    _tick(rds, t0)
    _tick(rds, t0 + timedelta(minutes=29))
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).all() == []
    # a dip below the threshold resets the window
    _put_state(rds, pid, ams_units=[{**UNIT, "humidity": 30.0}])
    _tick(rds, t0 + timedelta(minutes=31))
    _put_state(rds, pid)
    _tick(rds, t0 + timedelta(minutes=32))
    _tick(rds, t0 + timedelta(minutes=61))
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).all() == []
    counts = _tick(rds, t0 + timedelta(minutes=63))
    assert counts["created"] == 1 and counts["started"] == 1
    with base.sync_session() as s:
        row = s.scalars(select(ScheduledDrying)).one()
    assert row.source == "ambient" and row.filament == "PETG"
    assert (row.temp, row.duration_hours) == (65, 12)  # H2D n3f preset for PETG
    assert sent == [(row.id, 0, "PETG")]
    # cooldown + open run: no second schedule
    _tick(rds, t0 + timedelta(minutes=70))
    with base.sync_session() as s:
        assert len(s.scalars(select(ScheduledDrying)).all()) == 1


def test_tick_ambient_skips_busy_printer_and_per_type_threshold(rds, bus):
    t0 = datetime.now(UTC)
    _put_cfg(ambient_drying_enabled=True, ambient_sustained_min=1, per_type_thresholds={"PETG": 80})
    with base.sync_session() as s:
        pid = _db_printer(s).id
    _put_state(rds, pid)  # 55 % < PETG 80
    _tick(rds, t0)
    _tick(rds, t0 + timedelta(minutes=5))
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).all() == []
    with base.sync_session() as s:
        s.get(Setting, "drying").value = DryingConfig(
            ambient_drying_enabled=True, ambient_sustained_min=1
        ).model_dump()
        s.commit()
    _put_state(rds, pid, gcode_state="RUNNING")  # busy: no automation
    _tick(rds, t0 + timedelta(minutes=10))
    _tick(rds, t0 + timedelta(minutes=20))
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).all() == []


def test_resolve_threshold_and_params():
    trays = [{"tray_type": "PLA"}, {"tray_type": "PETG-CF"}, {"tray_type": ""}]
    assert drying.resolve_humidity_threshold(trays, {}, 60) == 60
    assert drying.resolve_humidity_threshold(trays, {"PLA": 35, "default": 55}, 60) == 35
    assert drying.resolve_humidity_threshold([{"tray_type": "XYZ"}], {"PLA": 35}, 60) == 60
    presets = DryingConfig().presets_dict()
    # lowest temp, longest time (PETG-CF resolves to the PETG row)
    assert drying.conservative_params(trays, 0, presets) == (45, 12, "PLA")
    assert drying.conservative_params([{"tray_type": "PA6-CF"}], 128, presets) == (85, 12, "PA")
    assert drying.conservative_params([{"tray_type": "XYZ"}], 0, presets) is None


def test_queue_block_hold_and_dispatcher(rds, bus, monkeypatch):
    t0 = datetime.now(UTC)
    _put_cfg(queue_drying_block=True)
    with base.sync_session() as s:
        pid = _db_printer(s).id
    _put_state(rds, pid, ams_units=[{**UNIT, "dry_status": 2}])
    _tick(rds, t0)
    assert drying_tick.hold_reason(rds, pid) == "drying"
    with base.sync_session() as s:
        assert pid in dispatcher._busy_printer_ids(s, rds)
    _put_state(rds, pid)  # drying over
    _tick(rds, t0 + timedelta(minutes=1))
    assert drying_tick.hold_reason(rds, pid) is None
    with base.sync_session() as s:
        assert pid not in dispatcher._busy_printer_ids(s, rds)
    # block off: an active unit never holds
    with base.sync_session() as s:
        s.get(Setting, "drying").value = DryingConfig(queue_drying_block=False).model_dump()
        s.commit()
    _put_state(rds, pid, ams_units=[{**UNIT, "dry_status": 2}])
    _tick(rds, t0 + timedelta(minutes=2))
    assert drying_tick.hold_reason(rds, pid) is None


def test_queue_drying_creates_schedule_for_wanted_printer(rds, bus, monkeypatch):
    t0 = datetime.now(UTC)
    _put_cfg(queue_drying_enabled=True, ams_humidity_fair=50)
    with base.sync_session() as s:
        pid = _db_printer(s).id
    _put_state(rds, pid)
    monkeypatch.setattr(dispatcher, "eligible_queue_items", lambda s: [])
    monkeypatch.setattr(dispatcher, "queue_target_ids", lambda s, c, i: (set(), set()))
    _tick(rds, t0)
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).all() == []
    monkeypatch.setattr(dispatcher, "queue_target_ids", lambda s, c, i: ({pid}, {pid}))
    _tick(rds, t0 + timedelta(minutes=1))
    with base.sync_session() as s:
        row = s.scalars(select(ScheduledDrying)).one()
    assert row.source == "queue" and row.status == "running"


@respx.mock
def test_plug_auto_off_once_after_drying(rds, bus):
    off = respx.post("http://plug/off").respond(200)
    t0 = datetime.now(UTC)
    with base.sync_session() as s:
        pid = _db_printer(s).id
        _plug(s, pid, auto_off_after_drying=True)
        other = _db_printer(s)
        _plug(s, other.id)  # flag off: never switched
        oid = other.id
    active = {**UNIT, "dry_status": 2}
    for p in (pid, oid):
        _put_state(rds, p, ams_units=[active])
    _tick(rds, t0)
    assert not off.called
    # a print is running when the cycle ends: no power-off
    _put_state(rds, pid, ams_units=[dict(UNIT)], gcode_state="RUNNING")
    _tick(rds, t0 + timedelta(minutes=1))
    assert not off.called
    _put_state(rds, pid, ams_units=[active])
    _put_state(rds, oid, ams_units=[dict(UNIT)])
    _tick(rds, t0 + timedelta(minutes=2))
    _put_state(rds, pid)
    counts = _tick(rds, t0 + timedelta(minutes=3))
    assert off.call_count == 1 and counts["plug_off"] == 1
    _tick(rds, t0 + timedelta(minutes=4))  # edge consumed: once only
    assert off.call_count == 1
    with base.sync_session() as s:
        assert (
            s.scalars(select(SmartPlug).where(SmartPlug.printer_id == pid)).one().last_state
            == "off"
        )
        assert (
            s.scalars(select(SmartPlug).where(SmartPlug.printer_id == oid)).one().last_state
            != "off"
        )


def test_old_terminal_rows_pruned(rds, bus):
    t0 = datetime.now(UTC)
    with base.sync_session() as s:
        pid = _db_printer(s).id
        s.add(
            ScheduledDrying(
                printer_id=pid,
                ams_id=0,
                temp=60,
                duration_hours=2,
                status="done",
                finished_at=t0 - timedelta(days=8),
            )
        )
        s.commit()
    _tick(rds, t0)
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).all() == []


def test_beat_registered():
    from app.tasks.celery_app import celery_app

    assert celery_app.conf.beat_schedule["drying-tick"]["schedule"] == 60


def test_notify_events_registered():
    from app.services.notify import catalog

    for ev in ("drying_started", "drying_finished", "drying_failed"):
        assert ev in catalog.EVENT_IDS
        assert catalog.default_template(ev)[0] != "{{ event }}"
        assert catalog.sample_payload(ev)["ams_id"] == 0


def test_stuck_queue_run_fails_and_releases_hold(rds, bus):
    t0 = datetime.now(UTC)
    _put_cfg(queue_drying_block=True)
    with base.sync_session() as s:
        pid = _db_printer(s).id
        s.add(ScheduledDrying(printer_id=pid, ams_id=0, temp=65, duration_hours=2, source="queue"))
        s.commit()
    _put_state(rds, pid, ams_units=[{**UNIT, "dry_sf_reason": [3]}])
    _tick(rds, t0)
    with base.sync_session() as s:
        row = s.scalars(select(ScheduledDrying)).one()
    assert row.status == "waiting"
    assert drying_tick.hold_reason(rds, pid) is None  # waiting rows never hold
    counts = _tick(rds, t0 + timedelta(minutes=31))
    with base.sync_session() as s:
        row = s.scalars(select(ScheduledDrying)).one()
    assert (row.status, row.error_code, counts["failed"]) == ("failed", "ams_retract_filament", 1)


def test_queue_drying_ignores_unassigned_waiting_items(rds, bus, monkeypatch):
    t0 = datetime.now(UTC)
    _put_cfg(queue_drying_enabled=True, ams_humidity_fair=50)
    with base.sync_session() as s:
        pid = _db_printer(s).id
    _put_state(rds, pid)
    monkeypatch.setattr(dispatcher, "eligible_queue_items", lambda s: [])
    # a WAITING item may land anywhere (any-set) but no READY candidate (ready-set)
    monkeypatch.setattr(dispatcher, "queue_target_ids", lambda s, c, i: (set(), {pid}))
    _tick(rds, t0)
    with base.sync_session() as s:
        assert s.scalars(select(ScheduledDrying)).all() == []


def test_row_is_running_before_command_is_published(rds, monkeypatch):
    t0 = datetime.now(UTC)
    seen = []

    def send(r, row, fil):
        with base.sync_session() as s2:
            seen.append(s2.get(ScheduledDrying, row.id).status)

    monkeypatch.setattr(drying_tick, "_send_start", send)
    monkeypatch.setattr(drying_tick.events, "emit", lambda *a, **k: True)
    with base.sync_session() as s:
        pid = _db_printer(s).id
        s.add(ScheduledDrying(printer_id=pid, ams_id=0, temp=65, duration_hours=2))
        s.commit()
    _put_state(rds, pid)
    _tick(rds, t0)
    assert seen == ["running"]


@respx.mock
def test_plug_auto_off_skipped_when_queue_wants_printer(rds, bus, monkeypatch):
    from app.services.smart_plugs import manager

    off = respx.post("http://plug/off").respond(200)
    t0 = datetime.now(UTC)
    with base.sync_session() as s:
        pid = _db_printer(s).id
        _plug(s, pid, auto_off_after_drying=True)
    monkeypatch.setattr(manager, "_queue_wanted", lambda s, r: {pid})
    _put_state(rds, pid, ams_units=[{**UNIT, "dry_status": 2}])
    _tick(rds, t0)
    _put_state(rds, pid)
    _tick(rds, t0 + timedelta(minutes=1))
    assert not off.called


def test_humidity_good_must_not_exceed_fair():
    with pytest.raises(ValueError):
        DryingConfig(ams_humidity_good=70, ams_humidity_fair=60)
    assert DryingConfig(ams_humidity_good=60, ams_humidity_fair=60)
