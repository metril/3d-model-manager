from __future__ import annotations

from app.services.ams_backup import configure_args


def test_configure_args_fills_generic_id_when_missing():
    slot = {"ams_id": 0, "tray_id": 1, "tray_type": "HIPS", "tray_info_idx": None}
    assert configure_args(slot)["tray_info_idx"] == "GFS98"
    assert configure_args({**slot, "tray_info_idx": ""})["tray_info_idx"] == "GFS98"
    assert configure_args({**slot, "tray_info_idx": "GFSNL03"})["tray_info_idx"] == "GFSNL03"
    assert configure_args({**slot, "tray_type": "Mystery"})["tray_info_idx"] == ""
