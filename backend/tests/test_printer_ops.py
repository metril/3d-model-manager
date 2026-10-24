"""T3 printer ops: clear plate, xcam/print options, AMS slot configure, AMS
labels, SD browser + print-from-SD."""

from __future__ import annotations

import json

import pytest
import redis as redis_lib
from sqlalchemy import select

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import AmsLabel, AuditLog, Printer, PrintJob
from app.models.enums import PrinterKind, PrintJobOrigin, PrintJobState
from app.printerd import PrinterWorker
from app.printers import bambu
from app.printers import commands as cmds
from app.printers.base import PrinterConnection, state_key
from app.printers.bambu import BambuLanAdapter
from app.printers.fake import FakePrinterAdapter
from app.services import dispatcher, hooks, spool_inventory
from app.tasks import base
from tests.cassettes import bambu_snapshots as snap
from tests.test_bambu_adapter import StubPrinter
from tests.test_printer_control_api import (  # noqa: F401  (fixtures)
    CREATE,
    IDLE,
    _printer,
    printerd_stub,
    rds,
    set_state,
)

CONN = PrinterConnection(host="1.2.3.4", serial="0309ABC", access_code="12345678")


def _set_flag(pid: int, value: bool) -> None:
    with base.sync_session() as s:
        s.get(Printer, pid).awaiting_plate_clear = value
        s.commit()


def _flag(pid: int) -> bool:
    with base.sync_session() as s:
        return s.get(Printer, pid).awaiting_plate_clear


# -- clear plate -------------------------------------------------------------


@pytest.fixture
def worker(redis_url, printer_enabled, migrated_db):
    settings = get_settings()
    with base.sync_session() as s:
        p = Printer(
            name="pw",
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial="S",
            access_code_enc="x",
            enabled=True,
        )
        s.add(p)
        s.commit()
        pid = p.id
    adapter = FakePrinterAdapter(PrinterConnection(host="h", serial="S", access_code="x"))
    client = redis_lib.Redis.from_url(settings.redis_url)
    return PrinterWorker(settings, pid, adapter, client), pid


@pytest.mark.parametrize(
    "report,expect",
    [(snap.SNAPSHOT_FINISH, True), (snap.SNAPSHOT_ERROR, True), (snap.SNAPSHOT_PRINTING, False)],
)
def test_printerd_sets_flag_on_terminal_transition(worker, report, expect):
    w, pid = worker
    # external job: no file, adopted when the printer reports RUNNING
    w.handle_report(snap.SNAPSHOT_PRINTING)
    with base.sync_session() as s:
        job = s.scalars(select(PrintJob).where(PrintJob.printer_id == pid)).one()
        assert job.origin == PrintJobOrigin.EXTERNAL
    assert _flag(pid) is False
    w.handle_report(report)
    assert _flag(pid) is expect


def test_idle_repoll_after_clear_does_not_reset_flag(worker):
    w, pid = worker
    w.handle_report(snap.SNAPSHOT_PRINTING)
    w.handle_report(snap.SNAPSHOT_FINISH)
    _set_flag(pid, False)  # user cleared the plate
    w.handle_report(snap.SNAPSHOT_FINISH)  # printer keeps reporting FINISH
    assert _flag(pid) is False


async def test_clear_plate_lifecycle(authenticated_client, printer_enabled, set_state):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    fired: list[tuple] = []
    hooks.on_plate_cleared(lambda p, u: fired.append((p, u)))
    try:
        r = await authenticated_client.post(f"/api/printers/{pid}/clear-plate")
        assert r.status_code == 400 and r.json()["code"] == "not_awaiting"

        _set_flag(pid, True)
        st = await authenticated_client.get(f"/api/printers/{pid}/status")
        assert st.json()["awaiting_plate_clear"] is True
        r = await authenticated_client.post(f"/api/printers/{pid}/clear-plate")
        assert r.status_code == 200 and r.json() == {"awaiting_plate_clear": False}
        assert _flag(pid) is False and fired and fired[0][0] == pid

        set_state(pid, gcode_state="FINISH")  # flag already false but printer in FINISH: allowed
        r = await authenticated_client.post(f"/api/printers/{pid}/clear-plate")
        assert r.status_code == 200
    finally:
        hooks.unsubscribe("plate_cleared", fired and hooks._SUBSCRIBERS["plate_cleared"][-1])
    with base.sync_session() as s:
        assert s.scalars(select(AuditLog).where(AuditLog.action == "clear_plate")).all()


async def test_printer_out_and_queue_settings(authenticated_client, printer_enabled):
    pid = await _printer(authenticated_client)
    listing = await authenticated_client.get("/api/printers")
    assert listing.json()[0]["awaiting_plate_clear"] is False
    assert (await authenticated_client.get("/api/settings/queue")).json() == {
        "require_plate_clear": False
    }
    r = await authenticated_client.put("/api/settings/queue", json={"require_plate_clear": True})
    assert r.json() == {"require_plate_clear": True}
    assert (await authenticated_client.get("/api/settings/queue")).json()[
        "require_plate_clear"
    ] is True
    assert pid


def test_dispatcher_plate_gate(redis_url, printer_enabled, migrated_db):
    from app.models import Setting

    with base.sync_session() as s:
        p = Printer(
            name="gate",
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial="G",
            access_code_enc=encrypt_secret(get_settings(), "12345678"),
            model="A1 mini",
            enabled=True,
            awaiting_plate_clear=True,
        )
        s.add(p)
        s.commit()
        pid = p.id
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(pid), json.dumps({"gcode_state": "FINISH"}))
    try:
        with base.sync_session() as s:
            assert pid not in dispatcher._busy_printer_ids(s, c)  # gate off (default)
            s.add(Setting(key="queue", value={"require_plate_clear": True}))
            s.commit()
            assert pid in dispatcher._busy_printer_ids(s, c)  # gate on + flag set
            s.get(Printer, pid).awaiting_plate_clear = False
            s.commit()
            assert pid not in dispatcher._busy_printer_ids(s, c)  # cleared
    finally:
        c.close()
    assert dispatcher.plate_hold_reason(Printer(awaiting_plate_clear=True), True) == (
        "awaiting_plate_clear"
    )
    assert dispatcher.plate_hold_reason(Printer(awaiting_plate_clear=True), False) is None


# -- xcam / print options ----------------------------------------------------


def _adapter(monkeypatch, dump=None):
    stub = StubPrinter()
    if dump is not None:
        stub.mqtt_dump = lambda: dump
    sent: list[dict] = []
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: stub)
    monkeypatch.setattr(bambu, "_publish_raw", lambda p, payload: sent.append(payload))
    return BambuLanAdapter(CONN), sent


def test_xcam_payload_goldens():
    assert bambu.xcam_payload("spaghetti_detector", True, "high") == {
        "xcam": {
            "command": "xcam_control_set",
            "sequence_id": "0",
            "module_name": "spaghetti_detector",
            "control": True,
            "enable": True,
            "print_halt": True,
            "halt_print_sensitivity": "high",
        }
    }
    never = bambu.xcam_payload("first_layer_inspector", False, "never_halt")["xcam"]
    assert "halt_print_sensitivity" not in never and never["print_halt"] is True
    assert never["control"] is False and never["enable"] is False


def test_print_option_payload_goldens():
    assert bambu.print_option_payload("auto_recovery", True) == {
        "print": {"command": "print_option", "sequence_id": "0", "auto_recovery": True}
    }
    assert (
        bambu.print_option_payload("auto_switch_filament", False)["print"]["auto_switch_filament"]
        is False
    )


def test_xcam_command_validation():
    assert cmds.validate("set_xcam", {"module": "pileup_detector", "enable": True}) == {
        "module": "pileup_detector",
        "enable": True,
        "sensitivity": "medium",
    }
    for bad in (
        {"module": "nope", "enable": True},
        {"module": "pileup_detector", "enable": "yes"},
        {"module": "pileup_detector", "enable": True, "sensitivity": "max"},
    ):
        with pytest.raises(cmds.CommandError):
            cmds.validate("set_xcam", bad)
    with pytest.raises(cmds.CommandError):
        cmds.validate("set_print_option", {"option": "sound", "value": True})


def test_cfg_bitmask_and_option_parsing():
    # spaghetti: sens=2 (high) at bits 5-6, enabled bit 7; airprint enabled low at 14-16
    cfg = (2 << 5) | (1 << 7) | (1 << 16)
    dump = {
        "print": {"cfg": format(1 << 18, "x")},
        "xcam": {"cfg": cfg, "first_layer_inspector": True, "print_halt": True,
                 "auto_recovery_step_loss": False},
    }  # fmt: skip
    po = bambu.parse_print_options(dump)
    assert po["xcam"]["spaghetti_detector"] == {"enable": True, "sensitivity": "high"}
    assert po["xcam"]["airprint_detector"] == {"enable": True, "sensitivity": "low"}
    assert po["xcam"]["pileup_detector"]["enable"] is False
    assert po["xcam"]["first_layer_inspector"] == {"enable": True, "sensitivity": None}
    assert po["print_halt"] is True and po["auto_recovery"] is False
    assert po["auto_switch_filament"] is True
    # xcam nested under print is read too
    nested = bambu.parse_print_options({"print": {"xcam": {"printing_monitor": True}}})
    assert nested["xcam"]["printing_monitor"]["enable"] is True


def test_set_xcam_echo_hold(monkeypatch):
    dump = {"print": {}, "xcam": {"cfg": 0}}
    a, sent = _adapter(monkeypatch, dump)
    clock = [100.0]
    monkeypatch.setattr(bambu.time, "monotonic", lambda: clock[0])
    a.set_xcam("spaghetti_detector", True, "high")
    assert sent[0]["xcam"]["halt_print_sensitivity"] == "high"
    # printer still echoes the OLD value (disabled) -> optimistic value wins for 3 s
    held = a._snapshot()["print_options"]["xcam"]["spaghetti_detector"]
    assert held == {"enable": True, "sensitivity": "high"}
    clock[0] += 2.9
    assert a._snapshot()["print_options"]["xcam"]["spaghetti_detector"]["enable"] is True
    clock[0] += 0.2  # hold expired: the printer's value is accepted again
    assert a._snapshot()["print_options"]["xcam"]["spaghetti_detector"]["enable"] is False


def test_set_print_option_echo_hold(monkeypatch):
    a, sent = _adapter(monkeypatch, {"print": {}, "xcam": {"auto_recovery_step_loss": False}})
    clock = [10.0]
    monkeypatch.setattr(bambu.time, "monotonic", lambda: clock[0])
    a.set_print_option("auto_recovery", True)
    assert sent[0] == bambu.print_option_payload("auto_recovery", True)
    assert a._snapshot()["print_options"]["auto_recovery"] is True
    clock[0] += 3.5
    assert a._snapshot()["print_options"]["auto_recovery"] is False


async def test_xcam_route_gates_and_audit(
    authenticated_client, printer_enabled, set_state, printerd_stub
):
    pid = await _printer(authenticated_client)  # A1 mini
    set_state(pid, **IDLE)
    got = printerd_stub(pid, {"ok": True})
    r = await authenticated_client.post(
        f"/api/printers/{pid}/control/xcam",
        json={"module": "spaghetti_detector", "enable": True, "sensitivity": "never_halt"},
    )
    assert r.status_code == 200 and got[0]["command"] == "set_xcam"
    r = await authenticated_client.post(
        f"/api/printers/{pid}/control/xcam", json={"module": "airprint_detector", "enable": True}
    )  # not an A1 mini module
    assert r.status_code == 422 and r.json()["code"] == "unsupported"
    r = await authenticated_client.post(
        f"/api/printers/{pid}/control/print-option", json={"option": "auto_recovery", "value": True}
    )
    assert r.status_code == 200
    with base.sync_session() as s:
        actions = {a.action for a in s.scalars(select(AuditLog))}
    assert {"set_xcam", "set_print_option"} <= actions


async def test_capabilities_exposed(authenticated_client, printer_enabled):
    await _printer(authenticated_client)
    caps = (await authenticated_client.get("/api/printers")).json()[0]["capabilities"]
    assert caps["sd_browser"] is True and caps["sd_print"] is True
    assert "spaghetti_detector" in caps["xcam_modules"]


# -- AMS slot configure / reset -----------------------------------------------


def test_slot_payload_goldens():
    assert bambu.slot_configure_payload(
        1, 2, "GFL99", "PLA", "PLA Basic", "FF0000FF", 190, 230
    ) == {
        "print": {
            "command": "ams_filament_setting",
            "ams_id": 1,
            "tray_id": 2,
            "slot_id": 2,
            "tray_info_idx": "GFL99",
            "tray_type": "PLA",
            "tray_sub_brands": "PLA Basic",
            "tray_color": "FF0000FF",
            "nozzle_temp_min": 190,
            "nozzle_temp_max": 230,
            "sequence_id": "0",
        }
    }
    ext = bambu.slot_reset_payload(255, 254)["print"]
    assert (ext["ams_id"], ext["tray_id"], ext["slot_id"]) == (255, 254, 0)
    assert (
        ext["tray_color"] == "00000000" and ext["nozzle_temp_max"] == 0 and ext["tray_type"] == ""
    )
    ht = bambu.slot_reset_payload(128, 0)["print"]
    assert (ht["ams_id"], ht["tray_id"], ht["slot_id"]) == (128, 0, 0)


def test_slot_validation_normalises_color():
    base_args = {
        "ams_id": 0,
        "tray_id": 1,
        "tray_type": "PETG",
        "color": "#ab12cd",
        "nozzle_min": 220,
        "nozzle_max": 260,
    }
    assert cmds.validate("ams_slot_configure", base_args)["color"] == "AB12CDFF"
    for bad in (
        {**base_args, "color": "red"},
        {**base_args, "nozzle_min": 300},
        {**base_args, "tray_id": 4},
        {**base_args, "ams_id": 9},
        {**base_args, "tray_type": ""},
    ):
        with pytest.raises(cmds.CommandError):
            cmds.validate("ams_slot_configure", bad)


async def test_slot_configure_route_writes_db_and_device(
    authenticated_client, printer_enabled, set_state, printerd_stub
):
    pid = await _printer(authenticated_client)
    sp = await authenticated_client.post(
        "/api/spools", json={"material_type": "PLA", "color_hex": "#FF0000"}
    )
    assert sp.status_code in (200, 201), sp.text
    spool_id = sp.json()["id"]
    set_state(pid, **IDLE, trays=[])
    got = printerd_stub(pid, {"ok": True})
    body = {
        "spool_id": spool_id,
        "tray_type": "PLA",
        "sub_brand": "PLA Basic",
        "tray_info_idx": "GFL99",
        "color": "FF0000",
        "nozzle_min": 190,
        "nozzle_max": 230,
    }
    r = await authenticated_client.put(f"/api/printers/{pid}/slots/0/1", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["device"] == {"status": "applied"}
    assert got[0]["command"] == "ams_slot_configure"
    assert got[0]["args"]["color"] == "FF0000FF" and got[0]["args"]["tray_id"] == 1
    # DB assignment happened
    with base.sync_session() as s:
        row = spool_inventory._slot_row(s, pid, 0, 1)
        assert row is not None and row.spool_id == spool_id
    # device-only (no spool_id key) leaves the assignment alone; apply_device=false skips
    r = await authenticated_client.put(
        f"/api/printers/{pid}/slots/0/1", json={**body, "apply_device": False}
    )
    assert r.json()["device"] == {"status": "skipped"} and len(got) == 1
    # incomplete device fields -> 422
    r = await authenticated_client.put(f"/api/printers/{pid}/slots/0/1", json={"tray_type": "PLA"})
    assert r.status_code == 422
    # legacy shape unchanged
    r = await authenticated_client.put(f"/api/printers/{pid}/slots/0/1", json={"spool_id": None})
    assert r.status_code == 200 and isinstance(r.json(), list)
    # reset
    r = await authenticated_client.delete(f"/api/printers/{pid}/slots/0/1/device")
    assert r.status_code == 200 and got[-1]["command"] == "ams_slot_reset"
    assert got[-1]["args"] == {"ams_id": 0, "tray_id": 1}


async def test_slot_configure_offline_skips_device(authenticated_client, printer_enabled):
    pid = await _printer(authenticated_client)
    r = await authenticated_client.put(
        f"/api/printers/{pid}/slots/0/0",
        json={"tray_type": "PLA", "color": "00FF00", "nozzle_min": 190, "nozzle_max": 230},
    )
    assert r.status_code == 200 and r.json()["device"] == {"status": "skipped"}


# -- AMS labels -----------------------------------------------------------------


async def test_ams_label_crud_and_status_merge(authenticated_client, printer_enabled, set_state):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE, ams_units=[{"id": 0, "serial": "AMS-SN-1"}, {"id": 1}])
    r = await authenticated_client.put(
        f"/api/printers/{pid}/ams/0/label", json={"label": "  Shelf "}
    )
    assert r.status_code == 200 and r.json()["label"] == "Shelf"
    assert r.json()["ams_serial"] == "AMS-SN-1"
    r = await authenticated_client.put(f"/api/printers/{pid}/ams/0/label", json={"label": "Top"})
    assert r.json()["label"] == "Top"  # upsert, not a second row
    await authenticated_client.put(f"/api/printers/{pid}/ams/1/label", json={"label": "Low"})
    units = (await authenticated_client.get(f"/api/printers/{pid}/status")).json()["ams_units"]
    assert {u["id"]: u["label"] for u in units} == {0: "Top", 1: "Low"}
    with base.sync_session() as s:
        assert len(s.scalars(select(AmsLabel)).all()) == 2
    for bad in ("", "x" * 101):
        r = await authenticated_client.put(f"/api/printers/{pid}/ams/0/label", json={"label": bad})
        assert r.status_code == 422
    assert (
        await authenticated_client.delete(f"/api/printers/{pid}/ams/0/label")
    ).status_code == 204
    assert (
        await authenticated_client.delete(f"/api/printers/{pid}/ams/0/label")
    ).status_code == 404


# -- SD browser ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path,ok",
    [
        ("/", "/"),
        ("/cache//a.3mf", "/cache/a.3mf"),
        ("/cache/./a.3mf", "/cache/a.3mf"),
        ("cache/a.3mf", None),
        ("/cache/../etc/passwd", None),
        ("/a\x00b", None),
        ("/a\\b", None),
        ("", None),
    ],
)
def test_normalize_sd_path(path, ok):
    if ok is None:
        with pytest.raises(cmds.CommandError):
            cmds.normalize_sd_path(path)
    else:
        assert cmds.normalize_sd_path(path) == ok


def test_sd_project_file_payload_golden():
    p = bambu.sd_project_file_payload("/cache/job one.gcode.3mf", 2, False)["print"]
    assert p["url"] == "file:///sdcard/cache/job one.gcode.3mf"
    assert p["param"] == "Metadata/plate_2.gcode" and p["file"] == "job one.gcode.3mf"
    assert p["subtask_name"] == "job one" and p["use_ams"] is False
    assert p["command"] == "project_file"


def test_parse_storage():
    assert bambu.parse_storage("213 123456", None) == {
        "total": None,
        "used": None,
        "free": 123456,
        "source": "avbl",
    }
    assert bambu.parse_storage(None, "211 ... 999 bytes free")["source"] == "stat"
    assert bambu.parse_storage(None, None) is None


def test_list_line_marks_directories():
    from datetime import UTC, datetime

    now = datetime(2026, 10, 24, tzinfo=UTC)
    d = bambu.parse_list_line("drwxr-xr-x 2 u g 4096 Oct 22 14:03 cache", now)
    f = bambu.parse_list_line("-rw-r--r-- 1 u g 12 Oct 22 14:03 a.3mf", now)
    assert d.is_dir and not f.is_dir


@pytest.fixture
def sd(authenticated_client, printer_enabled, set_state, fake_adapter, monkeypatch):
    fake_adapter.files.update(
        {
            "/a.3mf": b"123",
            "/cache/b.gcode.3mf": b"4567",
            "/cache/readme.txt": b"x",
            "/model/deep/c.3mf": b"y",
        }
    )
    fake_adapter.storage = 1000
    return fake_adapter


async def test_sd_list_download_delete_storage(authenticated_client, sd, set_state):
    pid = await _printer(authenticated_client)
    r = await authenticated_client.get(f"/api/printers/{pid}/files", params={"path": "/"})
    assert r.status_code == 200
    names = [(e["name"], e["is_dir"], e["path"]) for e in r.json()["entries"]]
    assert names == [
        ("cache", True, "/cache"),
        ("model", True, "/model"),
        ("a.3mf", False, "/a.3mf"),
    ]
    r = await authenticated_client.get(f"/api/printers/{pid}/files", params={"path": "/cache"})
    assert [e["path"] for e in r.json()["entries"]] == [
        "/cache/b.gcode.3mf",
        "/cache/readme.txt",
    ]
    r = await authenticated_client.get(
        f"/api/printers/{pid}/files/download", params={"path": "/cache/b.gcode.3mf"}
    )
    assert r.status_code == 200 and r.content == b"4567"
    assert "b.gcode.3mf" in r.headers["content-disposition"]
    r = await authenticated_client.get(f"/api/printers/{pid}/files/storage")
    assert r.json() == {
        "total": 1000, "used": 9, "free": 991, "source": "fake", "present": None,
    }  # fmt: skip
    r = await authenticated_client.delete(
        f"/api/printers/{pid}/files", params={"path": "/cache/readme.txt"}
    )
    assert r.status_code == 204 and "/cache/readme.txt" not in sd.files
    r = await authenticated_client.delete(
        f"/api/printers/{pid}/files", params={"path": "/cache/readme.txt"}
    )
    assert r.status_code == 404
    with base.sync_session() as s:
        row = s.scalars(select(AuditLog).where(AuditLog.action == "sd_delete")).first()
        assert row.payload == {"path": "/cache/readme.txt"}


async def test_sd_traversal_rejected(authenticated_client, sd):
    pid = await _printer(authenticated_client)
    for method, url in (
        ("GET", f"/api/printers/{pid}/files"),
        ("GET", f"/api/printers/{pid}/files/download"),
        ("DELETE", f"/api/printers/{pid}/files"),
    ):
        for bad in ("/cache/../secret", "relative/x", "/a\x00b"):
            r = await authenticated_client.request(method, url, params={"path": bad})
            assert r.status_code == 400, (method, bad, r.status_code)
    assert "/cache/b.gcode.3mf" in sd.files


async def test_sd_storage_falls_back_to_state(authenticated_client, sd, set_state):
    pid = await _printer(authenticated_client)
    sd.storage = None
    set_state(pid, **IDLE, sdcard=True)
    r = await authenticated_client.get(f"/api/printers/{pid}/files/storage")
    assert r.json()["source"] == "sdcard" and r.json()["present"] is True


async def test_sd_print_extension_gate_and_job(authenticated_client, sd, set_state, printerd_stub):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    got = printerd_stub(pid, {"ok": True})
    r = await authenticated_client.post(
        f"/api/printers/{pid}/files/print", json={"path": "/cache/readme.txt"}
    )
    assert r.status_code == 400 and not got
    r = await authenticated_client.post(
        f"/api/printers/{pid}/files/print", json={"path": "/../x.3mf"}
    )
    assert r.status_code == 400
    r = await authenticated_client.post(
        f"/api/printers/{pid}/files/print",
        json={"path": "/cache/b.gcode.3mf", "plate": 2, "use_ams": False},
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    assert got[0]["command"] == "sd_print"
    assert got[0]["args"] == {"path": "/cache/b.gcode.3mf", "plate": 2, "use_ams": False}
    with base.sync_session() as s:
        job = s.get(PrintJob, r.json()["job_id"])
        assert job.origin == PrintJobOrigin.EXTERNAL and job.file_id is None
        assert job.state == PrintJobState.STARTING.value and job.plate == 2
        assert s.scalars(select(AuditLog).where(AuditLog.action == "sd_print")).first()


async def test_sd_print_failure_creates_no_job(authenticated_client, sd, set_state, printerd_stub):
    pid = await _printer(authenticated_client)
    set_state(pid, **{**IDLE, "gcode_state": "RUNNING"})
    printerd_stub(pid, {"ok": True})
    r = await authenticated_client.post(f"/api/printers/{pid}/files/print", json={"path": "/a.3mf"})
    assert r.status_code == 409 and r.json()["code"] == "printing"
    with base.sync_session() as s:
        assert s.scalars(select(PrintJob)).all() == []


def test_fake_adapter_sd_ops(fake_adapter):
    fake_adapter.files["/x/y.3mf"] = b"zz"
    fake_adapter.storage = 10
    assert [(f.name, f.is_dir) for f in fake_adapter.list_files("/")] == [("x", True)]
    fake_adapter.delete_file("/x/y.3mf")
    with pytest.raises(FileNotFoundError):
        fake_adapter.delete_file("/x/y.3mf")
    assert fake_adapter.storage_info()["free"] == 10


def test_bambu_delete_and_storage(monkeypatch):
    class Ftp:
        def __init__(self, ret):
            self.ret = ret
            self.deleted = []

        def delete_file(self, p):
            self.deleted.append(p)
            return self.ret

    a = BambuLanAdapter(CONN)
    ok = Ftp("250 ok")
    monkeypatch.setattr(bambu, "_build_ftp", lambda conn: ok)
    a.delete_file("/cache/a.3mf")
    assert ok.deleted == ["/cache/a.3mf"]
    monkeypatch.setattr(bambu, "_build_ftp", lambda conn: Ftp(None))
    with pytest.raises(OSError):
        a.delete_file("/cache/a.3mf")
    monkeypatch.setattr(bambu, "_ftp_commands", lambda client, cmds_: ["213 555", None])
    assert a.storage_info()["free"] == 555
