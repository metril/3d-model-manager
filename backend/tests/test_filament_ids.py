from __future__ import annotations

import pytest

from app.printers.filament_ids import generic_filament_id
from app.schemas.printer_ops import SlotConfigIn


@pytest.mark.parametrize(
    ("material", "expected"),
    [
        ("PLA", "GFL99"),
        ("pla", "GFL99"),
        (" PLA  Silk ", "GFL96"),
        ("PLA High Speed", "GFL95"),
        ("PLA-CF", "GFL98"),
        ("PETG", "GFG99"),
        ("PETG-CF", "GFG98"),
        ("PETG HF", "GFG96"),
        ("PCTG", "GFG97"),
        ("ABS", "GFB99"),
        ("ASA", "GFB98"),
        ("PC", "GFC99"),
        ("PA", "GFN99"),
        ("Nylon", "GFN99"),
        ("PA-CF", "GFN98"),
        ("TPU", "GFU99"),
        ("PVA", "GFS99"),
        ("HIPS", "GFS98"),
        ("PE", "GFP99"),
        ("PP", "GFP97"),
        ("Unobtanium", ""),
        (None, ""),
    ],
)
def test_generic_filament_id(material, expected):
    assert generic_filament_id(material) == expected


def test_slot_config_fills_missing_generic_id():
    base = {"tray_type": "HIPS", "color": "FFFFFF", "nozzle_min": 200, "nozzle_max": 240}
    assert SlotConfigIn(**base).device_args()["tray_info_idx"] == "GFS98"
    # an explicit (e.g. vendor) id is never overridden
    assert SlotConfigIn(**base, tray_info_idx="GFSNL03").device_args()["tray_info_idx"] == "GFSNL03"
