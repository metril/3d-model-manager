# ruff: noqa: F811  (pytest fixtures imported from sibling test modules)
"""T2 K-profiles: wire payloads, response parsing, cache + list flow, drift
fixer, slot-configure chaining, routes and spool profile CRUD."""

from __future__ import annotations

import json
import re
import threading
import time

import pytest
from sqlalchemy import select

from app.models import AuditLog, Spool, SpoolSlot
from app.models.kprofiles import KProfileNote, SpoolKProfile
from app.printers import bambu
from app.printers import commands as cmds
from app.printers.base import PrinterPublicState
from app.services import kprofiles
from app.tasks import base
from tests.test_printer_control_api import (  # noqa: F401  (fixtures)
    IDLE,
    _printer,
    printerd_stub,
    rds,
    set_state,
)
from tests.test_printer_ops import _adapter, worker  # noqa: F401

FILAMENTS = [
    {
        "ams_id": 0,
        "cali_idx": 0,
        "extruder_id": 0,
        "filament_id": "GFA00",
        "k_value": "0.020000",
        "n_coef": "1.000000",
        "name": "PLA Basic",
        "nozzle_id": "HS00-0.4",
        "setting_id": "PF12345678901234567",
        "tray_id": -1,
    },
    {
        "cali_idx": 1,
        "filament_id": "GFB00",
        "name": "ABS",
        "k_value": "0.05",
        "nozzle_diameter": "0.6",
    },
]


# -- payload goldens -----------------------------------------------------------


def test_get_payload_golden():
    assert bambu.kprofile_get_payload("0.4") == {
        "print": {
            "command": "extrusion_cali_get",
            "filament_id": "",
            "nozzle_diameter": "0.4",
            "sequence_id": "0",
        }
    }


def test_set_payload_golden_new_profile():
    clean = cmds.validate(
        "kprofile_set",
        {"name": "My PLA", "filament_id": "GFA00", "k_value": 0.0215, "nozzle_diameter": "0.4"},
    )
    assert clean["cali_idx"] == -1
    assert re.fullmatch(r"PF\d{17}", clean["setting_id"])
    item = bambu.kprofile_set_payload(clean, dual=False)["print"]["filaments"][0]
    assert item == {
        "ams_id": 0,
        "cali_idx": -1,
        "extruder_id": 0,
        "filament_id": "GFA00",
        "k_value": "0.021500",
        "n_coef": "0.000000",
        "name": "My PLA",
        "nozzle_diameter": "0.4",
        "nozzle_id": "HS00-0.4",
        "setting_id": clean["setting_id"],
        "tray_id": 0,  # ALWAYS 0 (-1 is rejected by single-nozzle firmware)
    }
    env = bambu.kprofile_set_payload(clean, dual=False)["print"]
    assert env["command"] == "extrusion_cali_set" and env["nozzle_diameter"] == "0.4"
    assert (
        bambu.kprofile_set_payload(clean, dual=True)["print"]["filaments"][0]["nozzle_id"]
        == "HH00-0.4"
    )


def test_set_edit_requires_cali_idx_and_keeps_setting_id():
    base_args = {"name": "x", "filament_id": "GFA00", "k_value": 0.03, "setting_id": "PF1"}
    with pytest.raises(cmds.CommandError):
        cmds.validate("kprofile_set", base_args)
    clean = cmds.validate("kprofile_set", {**base_args, "cali_idx": 4})
    assert (clean["setting_id"], clean["cali_idx"]) == ("PF1", 4)


def test_setting_ids_are_unique_pf_format():
    ids = {cmds.new_setting_id() for _ in range(50)}
    assert len(ids) == 50 and all(re.fullmatch(r"PF\d{17}", i) for i in ids)


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "", "filament_id": "GFA00", "k_value": 0.02},
        {"name": "x", "filament_id": "", "k_value": 0.02},
        {"name": "x", "filament_id": "GFA00", "k_value": -1},
        {"name": "x", "filament_id": "GFA00", "k_value": 0.02, "nozzle_diameter": "0.45"},
        {"name": "x", "filament_id": "GFA00", "k_value": True},
    ],
)
def test_set_validation(bad):
    with pytest.raises(cmds.CommandError):
        cmds.validate("kprofile_set", bad)


def test_delete_payload_both_variants():
    clean = cmds.validate(
        "kprofile_delete",
        {"setting_id": "PF1", "cali_idx": 3, "filament_id": "GFA00", "nozzle_diameter": "0.4"},
    )
    x1 = bambu.kprofile_del_payload(clean, dual=False)["print"]
    assert x1 == {
        "command": "extrusion_cali_del",
        "sequence_id": "0",
        "filament_id": "GFA00",
        "cali_idx": 3,
        "nozzle_diameter": "0.4",
        "setting_id": "PF1",
        "nozzle_id": "HS00-0.4",
        "extruder_id": 0,
    }
    h2d = bambu.kprofile_del_payload({**clean, "extruder_id": 1}, dual=True)["print"]
    assert h2d == {
        "command": "extrusion_cali_del",
        "sequence_id": "0",
        "filament_id": "GFA00",
        "cali_idx": 3,
        "nozzle_diameter": "0.4",
        "extruder_id": 1,
        "nozzle_id": "HH00-0.4",
    }
    assert "setting_id" not in h2d


def test_sel_payload_golden():
    sel = bambu.kprofile_sel_payload(1, 2, 7, "GFA00", "0.4")["print"]
    assert sel == {
        "command": "extrusion_cali_sel",
        "cali_idx": 7,
        "filament_id": "GFA00",
        "nozzle_diameter": "0.4",
        "ams_id": 1,
        "tray_id": 6,  # GLOBAL tray id = ams*4 + slot
        "slot_id": 2,
        "sequence_id": "0",
    }
    assert "setting_id" not in sel
    ext = bambu.kprofile_sel_payload(255, 254, -1, "GFA00", "0.4")["print"]
    assert (ext["ams_id"], ext["tray_id"], ext["slot_id"]) == (254, 254, 0)
    ht = bambu.kprofile_sel_payload(128, 0, 2, "GFA00", "0.4")["print"]
    assert (ht["ams_id"], ht["tray_id"], ht["slot_id"]) == (128, 0, 0)


def test_dispatch_flags_and_validation():
    assert not cmds.DISPATCH["kprofile_list"].refuse_printing
    for c in ("kprofile_set", "kprofile_delete", "kprofile_select"):
        assert cmds.DISPATCH[c].refuse_printing
    assert cmds.validate("kprofile_list", {})["nozzle_diameter"] == "0.4"
    assert cmds.validate("kprofile_list", {"nozzle_diameter": 0.6, "extruder_id": 1}) == {
        "nozzle_diameter": "0.6",
        "extruder_id": 1,
    }
    with pytest.raises(cmds.CommandError):
        cmds.validate(
            "kprofile_select", {"ams_id": 0, "tray_id": 9, "cali_idx": 1, "filament_id": "x"}
        )


def test_adapter_picks_delete_variant_by_model(monkeypatch):
    from app.printers.base import PrinterConnection
    from tests.test_printer_ops import CONN  # noqa: F401

    entry = {"setting_id": "PF1", "cali_idx": 2, "filament_id": "GFA00", "nozzle_diameter": "0.4"}
    a, sent = _adapter(monkeypatch)
    a.kprofile_delete(entry)  # CONN serial 0309... = A1 mini
    assert "setting_id" in sent[0]["print"]
    h2d_conn = PrinterConnection(host="h", serial="094ABC", access_code="x", model="H2D")
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: object())
    a2 = bambu.BambuLanAdapter(h2d_conn)
    sent2: list[dict] = []
    monkeypatch.setattr(bambu, "_publish_raw", lambda p, payload: sent2.append(payload))
    a2.kprofile_delete(entry)
    assert "setting_id" not in sent2[0]["print"] and "extruder_id" in sent2[0]["print"]


def test_tray_entry_carries_cali_idx_and_k():
    t = bambu._tray_entry({"id": 0, "cali_idx": 3, "k": 0.021, "tray_info_idx": "GFA00"}, 0, 0)
    assert (t["cali_idx"], t["k_value"], t["tray_info_idx"]) == (3, 0.021, "GFA00")
    t = bambu._tray_entry({"id": 0}, 0, 0)
    assert (t["cali_idx"], t["k_value"], t["tray_info_idx"]) == (None, None, None)


def test_capability_flag():
    from app.printers.capabilities import capabilities_for

    assert capabilities_for("A1 mini", "0309ABC").kprofiles is True
    assert capabilities_for("H2D", "094ABC").kprofiles is True
    assert capabilities_for(None, "ZZZ").kprofiles is False


# -- response parsing -----------------------------------------------------------


def test_parse_with_and_without_per_item_nozzle():
    got = kprofiles.parse_kprofiles(FILAMENTS, "0.4")
    assert got[0]["nozzle_diameter"] == "0.4"  # envelope fallback (#1748)
    assert got[1]["nozzle_diameter"] == "0.6"  # per-item wins
    assert got[0]["k_value"] == 0.02 and got[0]["n_coef"] == 1.0
    assert got[0]["setting_id"] == "PF12345678901234567" and got[1]["setting_id"] is None
    assert got[0]["tray_id"] == -1 and got[0]["ams_id"] == 0
    # no envelope either -> 0.4; malformed entries skipped
    assert kprofiles.parse_kprofiles([{"cali_idx": 1}, "junk", {"cali_idx": "x"}], None) == [
        {**kprofiles.parse_kprofiles([{"cali_idx": 1}], None)[0]}
    ]
    assert kprofiles.parse_kprofiles([{"cali_idx": 1}], None)[0]["nozzle_diameter"] == "0.4"
    assert kprofiles.parse_kprofiles(None, "0.4") == []


# -- printerd: list / write results / cache -------------------------------------


def test_list_command_returns_payload_and_caches(worker, monkeypatch):
    w, pid = worker
    w.adapter.reply = {
        "command": "extrusion_cali_get",
        "nozzle_diameter": "0.4",
        "filaments": FILAMENTS,
    }
    res = w.handle_command("kprofile_list", {"nozzle_diameter": "0.4"}, "r1")
    assert res["ok"] is True and len(res["payload"]["profiles"]) == 2
    assert w.adapter.calls[-1][0] == "kprofile_list"
    cached = kprofiles.read_cache_sync(w.redis, pid, "0.4")
    assert cached["profiles"][0]["name"] == "PLA Basic" and cached["fetched_at"]
    assert w.redis.ttl(kprofiles.cache_key(pid, "0.4")) > 500
    stored = json.loads(w.redis.get(f"printer:{pid}:cmd_result:r1"))
    assert stored["payload"]["nozzle_diameter"] == "0.4"


def test_list_command_times_out_without_reply(worker, monkeypatch):
    import app.printerd as pd

    monkeypatch.setattr(pd, "KPROFILE_WAIT_S", 0.2)
    w, _ = worker
    res = w.handle_command("kprofile_list", {"nozzle_diameter": "0.4"})
    assert res["ok"] is False and res["code"] == "timeout"


def test_late_reply_still_wakes_waiter(worker):
    w, _ = worker
    threading.Timer(
        0.2,
        w.handle_reply,
        [{"command": "extrusion_cali_get", "nozzle_diameter": "0.4", "filaments": FILAMENTS}],
    ).start()
    assert w.handle_command("kprofile_list", {"nozzle_diameter": "0.4"})["ok"] is True


def test_set_returns_setting_id_and_invalidates_cache(worker):
    w, pid = worker
    kprofiles.store_cache(w.redis, pid, "0.4", [])
    res = w.handle_command(
        "kprofile_set",
        {"name": "n", "filament_id": "GFA00", "k_value": 0.03, "nozzle_diameter": "0.4"},
    )
    assert res["ok"] and re.fullmatch(r"PF\d{17}", res["payload"]["setting_id"])
    assert kprofiles.read_cache_sync(w.redis, pid, "0.4") is None
    name, entry = w.adapter.calls[-1]
    assert name == "kprofile_set" and entry["cali_idx"] == -1


def test_set_rejected_by_printer_surfaces_error(worker):
    w, _ = worker
    w.adapter.reply = {"command": "extrusion_cali_set", "result": "fail", "reason": "bad idx"}
    res = w.handle_command("kprofile_set", {"name": "n", "filament_id": "GFA00", "k_value": 0.03})
    assert res == {"ok": False, "code": "error", "message": "bad idx"}


@pytest.mark.parametrize("cmd", ["kprofile_set", "kprofile_delete", "kprofile_select"])
def test_writes_refused_while_printing_list_allowed(worker, cmd):
    w, _ = worker
    w._merged = {"gcode_state": "RUNNING"}
    args = {
        "kprofile_set": {"name": "n", "filament_id": "GFA00", "k_value": 0.03},
        "kprofile_delete": {"setting_id": "PF1", "cali_idx": 1, "filament_id": "GFA00"},
        "kprofile_select": {"ams_id": 0, "tray_id": 0, "cali_idx": 1, "filament_id": "GFA00"},
    }[cmd]
    assert w.handle_command(cmd, args)["code"] == "printing"
    w.adapter.reply = {"command": "extrusion_cali_get", "nozzle_diameter": "0.4", "filaments": []}
    assert w.handle_command("kprofile_list", {})["ok"] is True


# -- drift fixer ----------------------------------------------------------------


def _seed(pid: int, *, with_profile=True, cali_idx=5, setting_id="PF1"):
    with base.sync_session() as s:
        spool = Spool(material_type="PLA")
        s.add(spool)
        s.flush()
        s.add(SpoolSlot(printer_id=pid, ams_unit=0, slot=1, spool_id=spool.id))
        if with_profile:
            s.add(
                SpoolKProfile(
                    spool_id=spool.id,
                    printer_id=pid,
                    nozzle_diameter=0.4,
                    k_value=0.022,
                    cali_idx=cali_idx,
                    setting_id=setting_id,
                )
            )
        s.commit()
        return spool.id


def _tray(cali_idx):
    return {
        "slot": 1,
        "ams_unit": 0,
        "tray": 1,
        "tray_type": "PLA",
        "tray_info_idx": "GFA00",
        "cali_idx": cali_idx,
    }


def _public(cali_idx, gcode_state="IDLE"):
    return PrinterPublicState(gcode_state=gcode_state, trays=[_tray(cali_idx)], nozzle_mm=0.4)


def _sels(w):
    return [c for c in w.adapter.calls if c[0] == "kprofile_select"]


@pytest.mark.parametrize("lost", [-1, None])
def test_reapply_lost_selection(worker, lost):
    w, pid = worker
    _seed(pid)
    w._reapply_on_idle(_public(lost))
    assert _sels(w) == [("kprofile_select", 0, 1, 5, "GFA00", "0.4")]
    with base.sync_session() as s:
        a = s.scalars(select(AuditLog).where(AuditLog.action == "kprofile.reapply")).one()
        assert a.printer_id == pid and a.payload["cali_idx"] == 5 and a.payload["ams_id"] == 0


def test_reapply_never_touches_other_real_profile(worker):
    w, pid = worker
    _seed(pid)
    w._reapply_on_idle(_public(9))  # a different real profile, picked on purpose
    w._reapply_on_idle(_public(5))  # already the stored one
    assert _sels(w) == []


def test_reapply_noop_without_stored_profile_or_spool(worker):
    w, pid = worker
    _seed(pid, with_profile=False)
    w._reapply_on_idle(_public(-1))
    assert _sels(w) == []
    with base.sync_session() as s:
        s.query(SpoolSlot).delete()
        s.commit()
    w._reapply_on_idle(_public(-1))
    assert _sels(w) == []


def test_reapply_debounced_per_tray_and_idle_only(worker):
    w, pid = worker
    _seed(pid)
    w._reapply_on_idle(_public(-1, "RUNNING"))
    w._reapply_on_idle(_public(-1, "PAUSE"))
    assert _sels(w) == []
    w._reapply_on_idle(_public(-1, "FINISH"))
    w._reapply_on_idle(_public(-1, "IDLE"))  # inside the 10 min window
    assert len(_sels(w)) == 1
    assert 0 < w.redis.ttl(kprofiles.debounce_key(pid, 0, 1)) <= 600


def test_reapply_follows_cached_cali_idx(worker):
    w, pid = worker
    _seed(pid, cali_idx=5, setting_id="PF1")
    kprofiles.store_cache(
        w.redis,
        pid,
        "0.4",
        [{"cali_idx": 2, "setting_id": "PF1", "nozzle_diameter": "0.4", "filament_id": "GFA00"}],
    )
    w._reapply_on_idle(_public(-1))
    assert _sels(w)[0][3] == 2  # indices shift when other profiles are deleted


def test_reapply_skips_other_nozzle(worker):
    w, pid = worker
    _seed(pid)
    pub = _public(-1)
    pub.nozzle_mm = 0.6
    w._reapply_on_idle(pub)
    assert _sels(w) == []


def test_execute_reapply_before_start_uses_internal_command(worker):
    w, pid = worker
    _seed(pid)
    w._public = _public(-1)
    res = w.handle_command("kprofile_reapply", {})
    assert res["ok"] and len(res["payload"]["reapplied"]) == 1 and len(_sels(w)) == 1
    # internal only: not reachable through the API validator
    with pytest.raises(cmds.CommandError):
        cmds.validate("kprofile_reapply", {})
    w._public = _public(-1, "RUNNING")
    assert w.handle_command("kprofile_reapply", {})["payload"]["reapplied"] == []


def test_request_reapply_without_printerd_is_false_fast(rds):
    t0 = time.monotonic()
    assert kprofiles.request_reapply_sync(rds, 1) is False
    assert time.monotonic() - t0 < 1


def test_has_lost_trays():
    assert kprofiles.has_lost_trays([_tray(-1)])
    assert kprofiles.has_lost_trays([_tray(None)])
    assert not kprofiles.has_lost_trays([_tray(3)])
    assert not kprofiles.has_lost_trays([{**_tray(-1), "tray_type": None}])  # empty slot


# -- routes: printer side ---------------------------------------------------------


ENTRY = {
    "profiles": kprofiles.parse_kprofiles(FILAMENTS, "0.4"),
    "nozzle_diameter": "0.4",
    "fetched_at": "2026-10-24T00:00:00+00:00",
}


async def test_list_cache_refresh_and_pending_flow(
    authenticated_client, printer_enabled, set_state, printerd_stub, rds
):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    got = printerd_stub(pid, {"ok": True, "payload": ENTRY})

    # cold cache -> command issued, result passed through, note merged in
    await authenticated_client.put(
        f"/api/printers/{pid}/kprofiles/PF12345678901234567/note", json={"note": "good"}
    )
    r = await authenticated_client.get(f"/api/printers/{pid}/kprofiles?nozzle=0.4")
    assert r.status_code == 200, r.text
    assert got[0]["command"] == "kprofile_list" and got[0]["args"] == {"nozzle_diameter": "0.4"}
    body = r.json()
    assert body["nozzle_diameter"] == "0.4" and body["fetched_at"]
    assert body["profiles"][0]["note"] == "good" and body["profiles"][1]["note"] is None

    # warm cache -> no printer round trip; refresh=1 forces one
    kprofiles.store_cache(rds, pid, "0.4", ENTRY["profiles"])
    r = await authenticated_client.get(f"/api/printers/{pid}/kprofiles?nozzle=0.4")
    assert r.status_code == 200 and len(got) == 1
    r = await authenticated_client.get(f"/api/printers/{pid}/kprofiles?nozzle=0.4&refresh=1")
    assert r.status_code == 200 and len(got) == 2

    # extruder filter
    r = await authenticated_client.get(f"/api/printers/{pid}/kprofiles?nozzle=0.4&extruder=1")
    assert r.json()["profiles"] == []

    # list is allowed while printing and with Developer Mode off
    set_state(pid, gcode_state="RUNNING", dev_mode=False)
    r = await authenticated_client.get(f"/api/printers/{pid}/kprofiles?nozzle=0.4&refresh=1")
    assert r.status_code == 200

    assert (
        await authenticated_client.get(f"/api/printers/{pid}/kprofiles?nozzle=9")
    ).status_code == 422


async def test_list_202_then_poll_with_request_id(
    authenticated_client, printer_enabled, set_state, printerd_stub, rds, monkeypatch
):
    import app.api.printer_control as pc

    monkeypatch.setattr(pc, "RESULT_WAIT_S", 0.3)
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    got = printerd_stub(pid, None)  # printerd stays silent
    r = await authenticated_client.get(f"/api/printers/{pid}/kprofiles?nozzle=0.4")
    assert r.status_code == 202 and r.json()["status"] == "pending"
    rid = r.json()["request_id"]
    assert got[0]["request_id"] == rid
    url = f"/api/printers/{pid}/kprofiles?nozzle=0.4&request_id={rid}"
    assert (await authenticated_client.get(url)).status_code == 202
    rds.set(f"printer:{pid}:cmd_result:{rid}", json.dumps({"ok": True, "payload": ENTRY}), ex=30)
    r = await authenticated_client.get(url)
    assert r.status_code == 200 and len(r.json()["profiles"]) == 2
    rds.set(f"printer:{pid}:cmd_result:{rid}", json.dumps({"ok": False, "code": "timeout"}), ex=30)
    assert (await authenticated_client.get(url)).status_code == 504
    assert (
        await authenticated_client.get(f"/api/printers/{pid}/kprofiles?request_id=nothex")
    ).status_code == 422


async def test_create_edit_delete_select_and_audit(
    authenticated_client, printer_enabled, set_state, printerd_stub, rds
):
    pid = await _printer(authenticated_client)
    set_state(pid, **IDLE)
    got = printerd_stub(pid, {"ok": True, "payload": {"setting_id": "PF1"}})
    c = authenticated_client

    r = await c.post(
        f"/api/printers/{pid}/kprofiles",
        json={"name": "N", "filament_id": "GFA00", "k_value": 0.025},
    )
    assert r.status_code == 200 and r.json()["payload"]["setting_id"]
    sent = got[-1]
    assert sent["command"] == "kprofile_set" and sent["args"]["cali_idx"] == -1
    assert re.fullmatch(r"PF\d{17}", sent["args"]["setting_id"])

    # edit without cali_idx: not in cache -> 404; with cache -> resolved
    body = {
        "name": "N2",
        "filament_id": "GFA00",
        "k_value": 0.03,
        "setting_id": "PF12345678901234567",
    }
    assert (await c.post(f"/api/printers/{pid}/kprofiles", json=body)).status_code == 404
    kprofiles.store_cache(rds, pid, "0.4", ENTRY["profiles"])
    assert (await c.post(f"/api/printers/{pid}/kprofiles", json=body)).status_code == 200
    assert (
        got[-1]["args"]["cali_idx"] == 0 and got[-1]["args"]["setting_id"] == "PF12345678901234567"
    )

    # delete: resolved from cache, 404 when unknown
    await c.put(f"/api/printers/{pid}/kprofiles/PF12345678901234567/note", json={"note": "x"})
    r = await c.delete(f"/api/printers/{pid}/kprofiles/PF12345678901234567?nozzle=0.4")
    assert r.status_code == 200
    assert got[-1]["command"] == "kprofile_delete"
    assert got[-1]["args"]["cali_idx"] == 0 and got[-1]["args"]["filament_id"] == "GFA00"
    with base.sync_session() as s:
        assert s.scalars(select(KProfileNote)).all() == []
    assert (await c.delete(f"/api/printers/{pid}/kprofiles/PFNOPE?nozzle=0.4")).status_code == 404
    r = await c.delete(
        f"/api/printers/{pid}/kprofiles/PFNOPE?nozzle=0.4&cali_idx=3&filament_id=GFA00"
    )
    assert r.status_code == 200 and got[-1]["args"]["cali_idx"] == 3

    r = await c.post(
        f"/api/printers/{pid}/kprofiles/select",
        json={"ams_id": 0, "tray_id": 1, "cali_idx": 2, "filament_id": "GFA00"},
    )
    assert r.status_code == 200 and got[-1]["command"] == "kprofile_select"
    bad = await c.post(
        f"/api/printers/{pid}/kprofiles/select",
        json={"ams_id": 0, "tray_id": 7, "cali_idx": 2, "filament_id": "GFA00"},
    )
    assert bad.status_code == 422

    with base.sync_session() as s:
        actions = {a.action for a in s.scalars(select(AuditLog))}
    assert {"kprofile_set", "kprofile_delete", "kprofile_select"} <= actions


async def test_writes_refused_while_printing_via_api(
    authenticated_client, printer_enabled, set_state, printerd_stub
):
    pid = await _printer(authenticated_client)
    set_state(pid, gcode_state="RUNNING", nozzle_temper=200.0, dev_mode=True)
    printerd_stub(pid, {"ok": True})
    r = await authenticated_client.post(
        f"/api/printers/{pid}/kprofiles",
        json={"name": "N", "filament_id": "GFA00", "k_value": 0.025},
    )
    assert r.status_code == 409 and r.json()["code"] == "printing"


async def test_note_crud(authenticated_client, printer_enabled):
    pid = await _printer(authenticated_client)
    url = f"/api/printers/{pid}/kprofiles/PF1/note"
    assert (await authenticated_client.put(url, json={"note": "a"})).json() == {
        "setting_id": "PF1",
        "note": "a",
    }
    await authenticated_client.put(url, json={"note": "b"})  # upsert
    with base.sync_session() as s:
        assert [n.note for n in s.scalars(select(KProfileNote))] == ["b"]
    assert (await authenticated_client.delete(url)).status_code == 204
    with base.sync_session() as s:
        assert s.scalars(select(KProfileNote)).all() == []


# -- slot configure chains extrusion_cali_sel ---------------------------------------


async def test_slot_configure_chains_kprofile_select(
    authenticated_client, printer_enabled, set_state, printerd_stub
):
    c = authenticated_client
    pid = await _printer(c)
    sp = (await c.post("/api/spools", json={"material_type": "PLA"})).json()["id"]
    set_state(pid, **IDLE, nozzle_mm=0.4, trays=[])
    got = printerd_stub(pid, {"ok": True})
    dev = {
        "spool_id": sp,
        "tray_type": "PLA",
        "tray_info_idx": "GFL99",
        "color": "FF0000",
        "nozzle_min": 190,
        "nozzle_max": 230,
    }
    # no stored profile -> only the configure command
    assert (await c.put(f"/api/printers/{pid}/slots/0/1", json=dev)).status_code == 200
    assert [g["command"] for g in got] == ["ams_slot_configure"]

    put = await c.put(
        f"/api/spools/{sp}/kprofiles",
        json=[{"printer_id": pid, "nozzle_diameter": 0.4, "k_value": 0.022, "cali_idx": 6}],
    )
    assert put.status_code == 200, put.text
    assert (await c.put(f"/api/printers/{pid}/slots/0/1", json=dev)).status_code == 200
    assert [g["command"] for g in got] == [
        "ams_slot_configure",
        "ams_slot_configure",
        "kprofile_select",
    ]
    assert got[-1]["args"] == {
        "ams_id": 0,
        "tray_id": 1,
        "cali_idx": 6,
        "filament_id": "GFL99",
        "nozzle_diameter": "0.4",
    }
    # a different nozzle installed -> no select
    set_state(pid, **IDLE, nozzle_mm=0.6, trays=[])
    n = len(got)
    await c.put(f"/api/printers/{pid}/slots/0/1", json=dev)
    assert [g["command"] for g in got[n:]] == ["ams_slot_configure"]


# -- spool side ---------------------------------------------------------------------


async def test_spool_kprofile_crud(authenticated_client, printer_enabled):
    c = authenticated_client
    pid = await _printer(c)
    sp = (await c.post("/api/spools", json={"material_type": "PETG"})).json()["id"]
    assert (await c.get(f"/api/spools/{sp}/kprofiles")).json() == []
    item = {
        "printer_id": pid,
        "extruder": 0,
        "nozzle_diameter": 0.4,
        "nozzle_type": "hardened_steel",
        "k_value": 0.0215,
        "n_coef": 1.0,
        "name": "PETG HF",
        "cali_idx": 3,
        "setting_id": "PF1",
    }
    r = await c.put(f"/api/spools/{sp}/kprofiles", json=[item, {**item, "nozzle_diameter": 0.6}])
    assert r.status_code == 200 and len(r.json()) == 2
    assert r.json()[0] == item
    with base.sync_session() as s:
        assert {x.created_by_user_id is not None for x in s.scalars(select(SpoolKProfile))} == {
            True
        }
    # replace semantics
    r = await c.put(f"/api/spools/{sp}/kprofiles", json=[item])
    assert len(r.json()) == 1
    # duplicate key -> 422, unknown printer -> 404, unknown spool -> 404
    assert (await c.put(f"/api/spools/{sp}/kprofiles", json=[item, item])).status_code == 422
    assert (
        await c.put(f"/api/spools/{sp}/kprofiles", json=[{**item, "printer_id": 9999}])
    ).status_code == 404
    assert (await c.get("/api/spools/99999/kprofiles")).status_code == 404
    # targeted delete
    assert (
        await c.delete(f"/api/spools/{sp}/kprofiles/{pid}?extruder=0&nozzle=0.6")
    ).status_code == 204
    assert len((await c.get(f"/api/spools/{sp}/kprofiles")).json()) == 1
    assert (await c.delete(f"/api/spools/{sp}/kprofiles/{pid}?nozzle=0.4")).status_code == 204
    assert (await c.get(f"/api/spools/{sp}/kprofiles")).json() == []
    # deleting the spool cascades
    await c.put(f"/api/spools/{sp}/kprofiles", json=[item])
    assert (await c.delete(f"/api/spools/{sp}")).status_code == 204
    with base.sync_session() as s:
        assert s.scalars(select(SpoolKProfile)).all() == []


async def test_spool_kprofile_writes_need_inventory_write(
    viewer_client, authenticated_client, printer_enabled
):
    sp = (await authenticated_client.post("/api/spools", json={"material_type": "PLA"})).json()[
        "id"
    ]
    assert (await viewer_client.get(f"/api/spools/{sp}/kprofiles")).status_code == 200
    assert (await viewer_client.put(f"/api/spools/{sp}/kprofiles", json=[])).status_code == 403
    assert (await viewer_client.delete(f"/api/spools/{sp}/kprofiles/1")).status_code == 403
