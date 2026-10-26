"""Dual-nozzle (H2D) parsing, commands and dispatch. Payload shapes are the
ones Bambuddy captured (``device.extruder.info`` temp encoding, state bit 8,
AMS ``info`` extruder bits, ``select_extruder``); unverified on real hardware."""

import pytest

from app.printers import bambu
from app.printers import commands as cmds
from app.printers.bambu import (
    BambuLanAdapter,
    dual_ams_mapping,
    nozzle_temp_gcode,
    project_file_payload,
    select_extruder_payload,
)
from app.printers.base import PrinterConnection, PrintSpec, public_state_from
from app.printers.fake import FakePrinterAdapter
from app.schemas.printers import status_from_state

H2D = PrinterConnection(host="1.2.3.4", serial="09400A123", access_code="12345678", model="H2D")

# right = id 0 (hot, target 220), left = id 1 (cold)
DUMP = {
    "print": {
        "nozzle_temper": 999,
        "device": {
            "extruder": {
                "state": 0x0101,  # bit 8 set -> left active
                "info": [
                    {"id": 0, "temp": 220 * 65536 + 215, "snow": 0x0102},
                    {"id": 1, "temp": 31, "snow": 0xFF00FF},
                ],
            },
            "nozzle": {
                "info": [
                    {"id": 0, "type": "hardened_steel", "diameter": 0.4},
                    {"id": 1, "type": "stainless_steel", "diameter": 0.6},
                ]
            },
        },
        "ams": {
            "ams": [
                {"id": 0, "info": "2003"},
                {"id": 128, "info": "2104"},
                {"id": 1, "info": "2e03"},
            ]
        },
    }
}


def test_extruders_decode_temps_and_hardware():
    ex = bambu._extruders(DUMP)
    assert [e["side"] for e in ex] == ["right", "left"]
    assert (ex[0]["temp"], ex[0]["target"], ex[0]["heating"]) == (215.0, 220.0, True)
    assert (ex[1]["temp"], ex[1]["target"], ex[1]["heating"]) == (31.0, 0.0, False)
    assert (ex[0]["diameter"], ex[0]["type"]) == (0.4, "hardened_steel")
    assert ex[1]["diameter"] == 0.6


def test_flat_nozzle_hw_fallback():
    dump = {
        "print": {
            "left_nozzle_diameter": "0.2",
            "right_nozzle_type": "stainless_steel",
            "device": {"extruder": {"info": [{"id": 0, "temp": 20}, {"id": 1, "temp": 21}]}},
        }
    }
    ex = bambu._extruders(dump)
    assert ex[1]["diameter"] == 0.2 and ex[0]["type"] == "stainless_steel"


def test_single_nozzle_report_has_no_extruders():
    one = {"print": {"device": {"extruder": {"info": [{"id": 0, "temp": 20}], "state": 0}}}}
    assert bambu._extruders(one) == []
    assert bambu._extruders({"print": {}}) == []


def test_active_extruder_bit_8():
    assert bambu._active_extruder(DUMP) == 1
    assert bambu._active_extruder({"print": {"device": {"extruder": {"state": 0x0001}}}}) == 0
    assert bambu._active_extruder({"print": {}}) is None


def test_ams_extruder_map_skips_unrouted():
    assert bambu._ams_extruder_map(DUMP) == {"0": 0, "128": 1}


class _Stub:
    def __init__(self, dump):
        self._dump = dump
        self.gcodes = []

    def mqtt_dump(self):
        return self._dump

    def get_state(self):
        return "RUNNING"

    def get_percentage(self):
        return 1

    def current_layer_num(self):
        return 1

    def total_layer_num(self):
        return 2

    def get_time(self):
        return 3

    def subtask_name(self):
        return "x"

    def get_file_name(self):
        return "x"

    def get_nozzle_temperature(self):
        return 999.0

    def get_bed_temperature(self):
        return 60.0

    def get_light_state(self):
        return "on"

    def gcode(self, line, gcode_check=True):
        self.gcodes.append((line, gcode_check))
        return True


def test_snapshot_dual_overrides_single_nozzle_with_left(monkeypatch):
    stub = _Stub(DUMP)
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: stub)
    snap = BambuLanAdapter(H2D)._snapshot()
    assert snap["nozzle_temper"] == 31.0 and snap["nozzle_target"] == 0.0
    assert snap["active_extruder"] == 1
    assert snap["ams_extruder_map"] == {"0": 0, "128": 1}
    pub = public_state_from(snap)
    assert len(pub.extruders) == 2
    out = status_from_state(
        {"extruders": pub.extruders, "ams_extruder_map": snap["ams_extruder_map"]}
    )
    assert out.extruders[0].side == "right" and out.ams_extruder_map["128"] == 1


def test_set_temperature_per_nozzle_uses_m104_tool(monkeypatch):
    stub = _Stub(DUMP)
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: stub)
    a = BambuLanAdapter(H2D)
    a.set_temperature("nozzle", 210, 1)
    a.set_temperature("nozzle", 0, 0)
    assert stub.gcodes == [("M104 T1 S210", False), ("M104 T0 S0", False)]
    assert nozzle_temp_gcode(1, 5) == "M104 T1 S5"


def test_select_extruder_payload_shape():
    assert select_extruder_payload(1) == {
        "print": {"command": "select_extruder", "extruder_index": 1, "sequence_id": "0"}
    }


def test_commands_validate_and_run():
    assert cmds.validate("select_extruder", {"extruder_id": 1}) == {"extruder_id": 1}
    with pytest.raises(cmds.CommandError):
        cmds.validate("select_extruder", {"extruder_id": 2})
    clean = cmds.validate("set_temperature", {"target": "nozzle", "value": 200, "extruder_id": 0})
    assert clean["extruder_id"] == 0
    with pytest.raises(cmds.CommandError):
        cmds.validate("set_temperature", {"target": "bed", "value": 50, "extruder_id": 0})
    assert "extruder_id" not in cmds.validate("set_temperature", {"target": "bed", "value": 50})
    assert cmds.DISPATCH["select_extruder"].refuse_printing


def test_fake_adapter_records_both():
    f = FakePrinterAdapter.__new__(FakePrinterAdapter)
    calls = []
    f._record = lambda *a: calls.append(a)
    cmds.DISPATCH["select_extruder"].run(f, {"extruder_id": 1})
    cmds.DISPATCH["set_temperature"].run(f, {"target": "nozzle", "value": 5, "extruder_id": 1})
    cmds.DISPATCH["set_temperature"].run(f, {"target": "bed", "value": 5})
    assert calls == [
        ("select_extruder", 1),
        ("set_temperature", "nozzle", 5, 1),
        ("set_temperature", "bed", 5),
    ]


def test_dual_ams_mapping():
    flat, detail = dual_ams_mapping([5, 254, 255, -1, 128])
    assert flat == [5, -1, -1, -1, 128]
    assert detail == [
        {"ams_id": 1, "slot_id": 1},
        {"ams_id": 254, "slot_id": 0},
        {"ams_id": 255, "slot_id": 0},
        {"ams_id": 255, "slot_id": 255},
        {"ams_id": 128, "slot_id": 0},
    ]


def test_project_file_dual_adds_ams_mapping2_single_does_not():
    spec = PrintSpec(
        source_path=None,
        remote_name="a.gcode.3mf",
        plate=1,
        subtask_name="a",
        use_ams=True,
        ams_mapping=(2, 254),
    )
    dual = project_file_payload(spec, True)["print"]
    assert dual["ams_mapping"] == [2, -1]
    assert dual["ams_mapping2"][1] == {"ams_id": 254, "slot_id": 0}
    single = project_file_payload(spec)["print"]
    assert single["ams_mapping"] == [2, 254] and "ams_mapping2" not in single
