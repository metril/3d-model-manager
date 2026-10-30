"""D2: AMS slot backup / restore."""

from __future__ import annotations

from tests.test_printer_ops import (  # noqa: F401, F811  (fixtures)
    IDLE,
    _printer,
    printerd_stub,
    rds,
    set_state,
)

_TRAYS = [
    {
        "slot": 0,
        "ams_unit": 0,
        "tray": 0,
        "color": "#FF0000",
        "tray_type": "PLA",
        "tray_info_idx": "GFL99",
        "sub_brand": "PLA Basic",
        "nozzle_min": 190,
        "nozzle_max": 230,
        "cali_idx": 3,
        "k_value": 0.02,
    },
    {"slot": 1, "ams_unit": 0, "tray": 1, "color": None, "tray_type": None},  # empty: skipped
]


async def test_backup_and_restore(authenticated_client, printer_enabled, set_state, printerd_stub):  # noqa: F811
    pid = await _printer(authenticated_client)
    assert (await authenticated_client.get(f"/api/printers/{pid}/ams/backup")).json() is None
    assert (await authenticated_client.post(f"/api/printers/{pid}/ams/restore")).status_code == 404
    set_state(pid, **IDLE, trays=_TRAYS, nozzle_mm=0.4)
    r = await authenticated_client.post(f"/api/printers/{pid}/ams/backup")
    assert r.status_code == 200, r.text
    assert [(s["ams_id"], s["tray_id"], s["tray_type"], s["color"]) for s in r.json()["slots"]] == [
        (0, 0, "PLA", "FF0000")
    ]
    got_backup = (await authenticated_client.get(f"/api/printers/{pid}/ams/backup")).json()
    assert got_backup["slots"][0]["cali_idx"] == 3

    got = printerd_stub(pid, {"ok": True})
    r = await authenticated_client.post(f"/api/printers/{pid}/ams/restore")
    assert r.status_code == 200, r.text
    assert r.json() == {"restored": 1, "failed": 0, "pending": 0}
    cfg = next(g for g in got if g["command"] == "ams_slot_configure")["args"]
    assert cfg["tray_info_idx"] == "GFL99" and cfg["color"] == "FF0000FF"
    assert (cfg["nozzle_min"], cfg["nozzle_max"]) == (190, 230)
    sel = next(g for g in got if g["command"] == "kprofile_select")["args"]
    assert (sel["cali_idx"], sel["filament_id"]) == (3, "GFL99")


async def test_backup_offline_is_409(authenticated_client, printer_enabled):
    pid = await _printer(authenticated_client)
    assert (await authenticated_client.post(f"/api/printers/{pid}/ams/backup")).status_code == 409
