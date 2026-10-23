import json

import pytest

from app.models.enums import PrintJobState
from app.printers import bambu
from app.printers.bambu import BambuLanAdapter
from app.printers.base import PrinterConnection, PrinterPublicState, PrintSpec
from tests.cassettes import bambu_snapshots as snap

CONN = PrinterConnection(host="1.2.3.4", serial="0309ABC", access_code="12345678")


def test_printer_connection_repr_hides_access_code():
    """M6 A2: ``access_code`` must never appear in an object repr/traceback
    dump -- ``field(repr=False)`` on the dataclass, not a full ``SecretStr``
    (the M4 scrub call sites below still compare it as a plain ``str``)."""
    c = PrinterConnection(host="h", serial="s", access_code="12345678")
    assert "12345678" not in repr(c)
    assert c.access_code == "12345678"  # reads stay a plain str -- no call-site churn


class StubPrinter:
    """Edge stub for a bambulabs_api.Printer -- records calls, canned status."""

    def __init__(self) -> None:
        self.connected = self.disconnected = False
        self.paused = self.resumed = self.stopped = 0
        self.uploaded: list[str] = []
        self.started: list[tuple] = []

    def mqtt_start(self):
        self.connected = True

    def disconnect(self):
        self.disconnected = True

    def get_state(self):
        return "RUNNING"

    def get_percentage(self):
        return 55

    def current_layer_num(self):
        return 66

    def total_layer_num(self):
        return 120

    def get_time(self):
        return 21

    def get_nozzle_temperature(self):
        return 220.0

    def get_bed_temperature(self):
        return 60.0

    def subtask_name(self):
        return "widget"

    def get_file_name(self):
        return "widget.gcode.3mf"

    def mqtt_dump(self):
        return {"print_error": 0, "wifi_signal": "-45dBm"}

    def upload_file(self, file, filename):
        self.uploaded.append(filename)
        return f"/cache/{filename}"

    def start_print(
        self,
        filename,
        plate_number,
        use_ams=True,
        ams_mapping=(0,),
        skip_objects=None,
        flow_calibration=True,
    ):
        self.started.append((filename, plate_number, use_ams, tuple(ams_mapping), flow_calibration))
        return True

    def pause_print(self):
        self.paused += 1
        return True

    def resume_print(self):
        self.resumed += 1
        return True

    def stop_print(self):
        self.stopped += 1
        return True


def _stub_adapter(monkeypatch) -> tuple[BambuLanAdapter, StubPrinter]:
    stub = StubPrinter()
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: stub)
    return BambuLanAdapter(CONN), stub


def test_public_state_extracts_from_snapshot():
    ps = BambuLanAdapter(CONN).public_state(snap.SNAPSHOT_PRINTING)
    assert ps.gcode_state == "RUNNING" and ps.mc_percent == 55
    assert ps.layer_num == 66 and ps.total_layer_num == 120
    assert ps.nozzle_temper == 220.0 and ps.bed_temper == 60.0


def test_job_state_mapping():
    a = BambuLanAdapter(CONN)
    assert a.job_state(PrinterPublicState(gcode_state="RUNNING")) == PrintJobState.PRINTING
    assert a.job_state(PrinterPublicState(gcode_state="PAUSE")) == PrintJobState.PAUSED
    assert a.job_state(PrinterPublicState(gcode_state="FINISH")) == PrintJobState.FINISHED
    assert a.job_state(PrinterPublicState(gcode_state="IDLE")) is None
    assert (
        a.job_state(PrinterPublicState(gcode_state="RUNNING", print_error=83935248))
        == PrintJobState.FAILED
    )


def test_snapshot_normalizes_lib_accessors(monkeypatch):
    a, _stub = _stub_adapter(monkeypatch)
    s = a._snapshot()
    assert s["gcode_state"] == "RUNNING" and s["mc_percent"] == 55
    assert s["layer_num"] == 66 and s["total_layer_num"] == 120 and s["mc_remaining_time"] == 21
    assert s["nozzle_temper"] == 220.0 and s["bed_temper"] == 60.0
    assert s["subtask_name"] == "widget" and s["print_error"] == 0 and s["wifi_signal"] == "-45dBm"


def test_request_full_status_emits_snapshot(monkeypatch):
    a, _stub = _stub_adapter(monkeypatch)
    seen: list[dict] = []
    a.set_report_handler(seen.append)
    a.request_full_status()
    assert seen and a.public_state(seen[0]).gcode_state == "RUNNING"


def test_upload_and_start_uploads_file(monkeypatch, tmp_path):
    """``upload_and_start`` still uploads via the lib; the project_file
    publish itself (and never calling ``start_print``) is covered in
    ``tests/test_bambu_project_file.py`` against a fake ``mqtt_client``
    (this stub has no ``mqtt_client`` attribute)."""
    a, stub = _stub_adapter(monkeypatch)
    src = tmp_path / "job.gcode.3mf"
    src.write_bytes(b"zip")
    with pytest.raises(AttributeError):
        a.upload_and_start(
            PrintSpec(
                source_path=src,
                remote_name="tdmm-7.gcode.3mf",
                plate=2,
                subtask_name="w",
                use_ams=True,
                ams_mapping=(1,),
            )
        )
    assert stub.uploaded == ["tdmm-7.gcode.3mf"]
    assert stub.started == []


def test_commands_call_lib(monkeypatch):
    a, stub = _stub_adapter(monkeypatch)
    a.pause()
    a.resume()
    a.stop()
    assert stub.paused == 1 and stub.resumed == 1 and stub.stopped == 1


# ``test_connection`` itself now delegates to ``app.printers.probe.staged_probe``
# (Round 8 T1/T2) -- see ``tests/test_bambu_probe.py`` for its coverage,
# including the delegation wiring, the access-code redaction wrapper, and the
# now-structurally-impossible-to-regress unbounded-camera-thread hang this
# used to guard against (the probe never touches ``self._client()``/the
# camera at all any more).


# Fast deterministic coverage for the two best-effort branches the M4
# backlog minor #4 flagged (M6 C3b) -- no real socket/thread involved.
def test_dump_get_reads_nested_print_dict():
    # The real v2.6.6 mqtt_dump() nests fields under "print"; _dump_get must
    # fall back into it (and prefer a top-level hit, and degrade to None).
    assert bambu._dump_get({"print": {"gcode_state": "RUNNING"}}, "gcode_state") == "RUNNING"
    assert bambu._dump_get({"print_error": 0}, "print_error") == 0  # top-level precedence
    assert bambu._dump_get({}, "gcode_state") is None  # neither present -> None, never raises


def test_hex6_strips_ams_alpha_and_tolerates_garbage():
    assert bambu._hex6("00FF00FF") == "#00FF00"  # RRGGBBAA -> #RRGGBB
    assert bambu._hex6("#abcdef") == "#ABCDEF"
    assert bambu._hex6("FFF") is None  # too short
    assert bambu._hex6("zzzzzz") is None
    assert bambu._hex6(None) is None
    assert bambu._hex6("") is None


def _tray(slot, ams_unit, tray, color=None, material=None, tray_uuid=None, remain_pct=None):
    return {
        "slot": slot,
        "ams_unit": ams_unit,
        "tray": tray,
        "color": color,
        "material": material,
        "tray_type": material,
        "tray_uuid": tray_uuid,
        "remain_pct": remain_pct,
    }


def test_ams_trays_parses_loaded_slots_and_tolerates_missing_ams():
    dump = {
        "print": {
            "ams": {
                "ams": [
                    {
                        "tray": [
                            {"tray_color": "FF0000FF", "tray_type": "PLA"},
                            {"tray_color": "00FF00FF", "tray_type": "PETG"},
                            {"tray_color": "", "tray_type": ""},  # empty slot -> still reported
                        ]
                    }
                ]
            }
        }
    }
    assert bambu._ams_trays(dump) == [
        _tray(0, 0, 0, color="#FF0000", material="PLA"),
        _tray(1, 0, 1, color="#00FF00", material="PETG"),
        _tray(2, 0, 2),
    ]
    # no AMS / unexpected shape -> [] (never raises)
    assert bambu._ams_trays({}) == []
    assert bambu._ams_trays({"print": {"ams": {}}}) == []
    assert bambu._ams_trays({"print": {"ams": {"ams": "nope"}}}) == []


def test_ams_trays_unit_and_rfid():
    zero_uuid = "0" * 32
    dump = {
        "print": {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": "0",
                                "tray_color": "FF0000FF",
                                "tray_type": "PLA",
                                "tray_uuid": "ABCDEF0123456789ABCDEF0123456789",
                                "remain": "63",
                            },
                            {"id": "1", "tray_color": "", "tray_type": "", "tray_uuid": zero_uuid},
                        ],
                    },
                    {
                        "id": "1",
                        "tray": [
                            {
                                "id": "0",
                                "tray_color": "0000FFFF",
                                "tray_type": "PETG",
                                "remain": "-1",
                            }
                        ],
                    },
                ]
            },
            "vt_tray": {
                "tray_color": "00FF00FF",
                "tray_type": "ABS",
                "tray_uuid": "11112222333344445555666677778888",
                "remain": "80",
            },
        }
    }
    trays = bambu._ams_trays(dump)
    assert trays[0] == _tray(
        0,
        0,
        0,
        color="#FF0000",
        material="PLA",
        tray_uuid="ABCDEF0123456789ABCDEF0123456789",
        remain_pct=63,
    )
    assert trays[1] == _tray(1, 0, 1, tray_uuid=None)  # all-zero uuid -> None
    assert trays[2] == _tray(4, 1, 0, color="#0000FF", material="PETG", remain_pct=None)
    assert trays[3] == _tray(
        254,
        255,
        254,
        color="#00FF00",
        material="ABS",
        tray_uuid="11112222333344445555666677778888",
        remain_pct=80,
    )


def test_snapshot_includes_ams_trays(monkeypatch):
    a, stub = _stub_adapter(monkeypatch)
    stub.mqtt_dump = lambda: {
        "print": {"ams": {"ams": [{"tray": [{"tray_color": "112233FF", "tray_type": "PLA"}]}]}}
    }
    assert a._snapshot()["trays"] == [_tray(0, 0, 0, color="#112233", material="PLA")]


def test_public_state_carries_ams_trays():
    tray = _tray(0, 0, 0, color="#ff0000", material="PLA")
    ps = BambuLanAdapter(CONN).public_state({"trays": [tray]})
    assert ps.trays == [tray]


class A1MiniStub(StubPrinter):
    """StubPrinter serving the recorded A1 Mini pushall dump."""

    def mqtt_dump(self):
        return snap.A1_MINI_DUMP

    def get_state(self):
        return "IDLE"

    def get_light_state(self):
        return "on"

    def nozzle_type(self):
        return "stainless_steel"


def test_snapshot_a1_mini_v2_fields(monkeypatch):
    stub = A1MiniStub()
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: stub)
    a = BambuLanAdapter(CONN)
    ps = a.public_state(a.merge_report(None, a._snapshot()))
    assert ps.nozzle_target == 0.0 and ps.bed_target == 60.0
    assert ps.chamber_temper is None and ps.chamber_target is None
    assert ps.fan_part_pct == 53 and ps.fan_aux_pct == 0 and ps.fan_chamber_pct == 0
    assert ps.light_on is True and ps.speed_level == 2 and ps.stage is None
    assert ps.hms == [{"attr": 83886336, "code": 65545}]
    assert ps.sdcard is True and ps.nozzle_type == "hardened_steel" and ps.ipcam is True
    assert ps.firmware == {"ota": "01.07.00.00", "ams": [{"id": 0, "sw_ver": "00.00.06.49"}]}
    assert ps.ams_units == [
        {"id": 0, "humidity": 31.0, "temp": 24.6, "serial": "AMS123", "fw": "00.00.06.49"}
    ]
    assert ps.ext_tray is not None and ps.ext_tray["material"] == "PLA"
    assert ps.dev_mode is None and ps.last_report_at


def test_fan_pct_scale():
    assert [bambu._fan_pct(v) for v in (0, "15", 8, "x", None, 255)] == [
        0,
        100,
        53,
        None,
        None,
        100,
    ]


def test_snapshot_without_extras_degrades_to_none(monkeypatch):
    a, _stub = _stub_adapter(monkeypatch)
    ps = a.public_state(a._snapshot())
    assert ps.nozzle_target is None and ps.firmware is None and ps.hms == []
    assert ps.light_on is None and ps.fan_part_pct is None and ps.ams_units == []


def test_last_report_at_only_moves_when_dump_changes(monkeypatch):
    stub = A1MiniStub()
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: stub)
    a = BambuLanAdapter(CONN)
    t1 = a._snapshot()["last_report_at"]
    assert a._snapshot()["last_report_at"] == t1
    stub.mqtt_dump = lambda: {
        **snap.A1_MINI_DUMP,
        "print": {**snap.A1_MINI_DUMP["print"], "spd_lvl": 3},
    }
    a._snapshot()
    assert a._dump_sig != json.dumps(snap.A1_MINI_DUMP, sort_keys=True, default=str)


def test_nozzle_type_none_when_unreported(monkeypatch):
    a, _stub = _stub_adapter(monkeypatch)
    assert a._snapshot()["nozzle_type"] is None


# -- Q2 controls -----------------------------------------------------------


class _Pub:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    def is_connected(self):
        return True

    def publish(self, topic, payload):
        self.sent.append((topic, json.loads(payload)))
        return self

    def wait_for_publish(self, timeout=None):
        pass

    def is_published(self):
        return True


class _MqttStub:
    command_topic = "device/S/request"

    def __init__(self):
        self._client = _Pub()
        self.calls: list[tuple] = []
        self.on_message_handler = None

    def set_nozzle_temperature(self, v, override=False):
        self.calls.append(("nozzle", v, override))
        return True

    def set_bed_temperature(self, v, override=False):
        self.calls.append(("bed", v, override))
        return True


class _CtlPrinter:
    def __init__(self):
        self.mqtt_client = _MqttStub()
        self.calls: list[tuple] = []
        self.ok = True

    def mqtt_start(self):
        pass

    def _rec(self, *c):
        self.calls.append(c)
        return self.ok

    def gcode(self, g, gcode_check=True):
        return self._rec("gcode", g, gcode_check)

    def set_part_fan_speed(self, v):
        return self._rec("part", v)

    def set_aux_fan_speed(self, v):
        return self._rec("aux", v)

    def set_chamber_fan_speed(self, v):
        return self._rec("chamber", v)

    def turn_light_on(self):
        return self._rec("light", True)

    def turn_light_off(self):
        return self._rec("light", False)

    def set_print_speed(self, v):
        return self._rec("speed", v)

    def home_printer(self):
        return self._rec("home")

    def skip_objects(self, ids):
        return self._rec("skip", ids)

    def unload_filament_spool(self):
        return self._rec("unload")

    def calibrate_printer(self, bed, motor, vib):
        return self._rec("calibrate", bed, motor, vib)


@pytest.fixture
def ctl(monkeypatch):
    stub = _CtlPrinter()
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: stub)
    return BambuLanAdapter(CONN), stub


def test_ctl_temperatures_override_lib_guard(ctl):
    a, p = ctl
    a.set_temperature("nozzle", 0)
    a.set_temperature("bed", 60)
    a.set_temperature("chamber", 40)
    assert p.mqtt_client.calls == [("nozzle", 0, True), ("bed", 60, True)]
    assert p.calls == [("gcode", "M141 S40", False)]


def test_ctl_fans_scale_pct_to_0_255(ctl):
    a, p = ctl
    a.set_fan("part", 100)
    a.set_fan("aux", 50)
    a.set_fan("chamber", 0)
    assert p.calls == [("part", 255), ("aux", 128), ("chamber", 0)]


def test_ctl_light_speed_home_calibrate_skip_unload(ctl):
    a, p = ctl
    a.set_light(True)
    a.set_light(False)
    a.set_speed(3)
    a.home()
    a.calibrate(True, False, True)
    a.skip_objects([4, 9])
    a.unload_filament()
    assert p.calls == [
        ("light", True),
        ("light", False),
        ("speed", 3),
        ("home",),
        ("calibrate", True, True, False),
        ("skip", [4, 9]),
        ("unload",),
    ]


def test_ctl_jog_extrude_gcode_use_relative_gcode(ctl):
    a, p = ctl
    a.jog("X", -10)
    a.jog("Z", 1)
    a.extrude(5)
    a.send_gcode("G28\nM104 S0")
    assert p.calls == [
        ("gcode", ["G91", "G0 X-10 F3000", "G90"], False),
        ("gcode", ["G91", "G0 Z1 F600", "G90"], False),
        ("gcode", ["M83", "G1 E5 F300"], False),
        ("gcode", ["G28", "M104 S0"], True),
    ]


def test_ctl_lib_refusal_raises(ctl):
    a, p = ctl
    p.ok = False
    with pytest.raises(OSError):
        a.set_light(True)


def test_ctl_raw_airduct_clear_hms_load_and_probe(ctl):
    a, p = ctl
    a.set_airduct("heating")
    a.set_airduct("cooling")
    a.clear_hms()
    a.load_filament(2)
    a.send_devmode_probe(
        {
            "tray_info_idx": "GFA00",
            "tray_type": "PLA",
            "tray_color": "FF0000FF",
            "nozzle_temp_min": 190,
            "nozzle_temp_max": 230,
            "setting_id": "S1",
        },
        "9000017",
    )
    sent = [pl["print"] for _, pl in p.mqtt_client._client.sent]
    assert sent[0]["command"] == "set_airduct" and sent[0]["modeId"] == 1
    assert sent[1]["modeId"] == 0
    assert sent[2]["command"] == "clean_print_error"
    assert sent[3]["command"] == "ams_change_filament" and sent[3]["target"] == 2
    probe = sent[4]
    assert probe["command"] == "ams_filament_setting" and probe["sequence_id"] == "9000017"
    assert probe["tray_info_idx"] == "GFA00" and probe["nozzle_temp_max"] == 230
    assert probe["setting_id"] == "S1" and probe["ams_id"] == 255


def test_ctl_reply_tap_forwards_results_only(ctl):
    a, p = ctl
    got = []
    a.set_reply_handler(got.append)
    a.connect()

    class Msg:
        def __init__(self, d):
            self.payload = json.dumps(d).encode()

    tap = p.mqtt_client.on_message_handler
    tap(None, None, None, Msg({"print": {"command": "push_status", "gcode_state": "IDLE"}}))
    tap(None, None, None, Msg({"print": {"command": "gcode_line", "result": "success"}}))
    tap(None, None, None, Msg({"nonsense": 1}))
    assert got == [{"command": "gcode_line", "result": "success"}]


def test_snapshot_carries_fun_and_ext_tray_raw(monkeypatch):
    stub = StubPrinter()
    stub.mqtt_dump = lambda: {"print": {"fun": "0x1", "vt_tray": {"tray_type": "PLA"}}}
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: stub)
    snap_ = BambuLanAdapter(CONN)._snapshot()
    assert snap_["fun"] == "0x1" and snap_["ext_tray_raw"] == {"tray_type": "PLA"}
