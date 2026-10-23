"""Developer Mode state machine (decision 2): fun bit -> guarded echo probe
-> lazy detection; ``None`` = unknown."""

from app.printers.devmode import (
    HMS_MQTT_VERIFY_FAILED,
    PROBE_DELAY_S,
    PROBE_SEQ,
    PROBE_TIMEOUT_S,
    DevModeTracker,
)

SLOT = {
    "tray_info_idx": "GFA00",
    "tray_type": "PLA",
    "tray_color": "FF0000FF",
    "nozzle_temp_min": 190,
    "nozzle_temp_max": 230,
}
IDLE = {"gcode_state": "IDLE", "ext_tray_raw": SLOT}
VERIFY_HMS = [
    {"attr": int(HMS_MQTT_VERIFY_FAILED[:8], 16), "code": int(HMS_MQTT_VERIFY_FAILED[8:], 16)}
]


def test_fun_bit_decides_without_probe():
    t = DevModeTracker()
    assert t.on_report({**IDLE, "fun": "0x20000000"}, 0) is None
    assert t.state is False and t.reason == "fun_bit"
    assert t.on_report({**IDLE, "fun": "0x0"}, 99) is None  # never probes
    assert t.state is True


def test_probe_waits_for_delay_and_idle_and_full_slot():
    t = DevModeTracker()
    assert t.on_report(IDLE, 0) is None  # first report starts the clock
    assert t.on_report(IDLE, PROBE_DELAY_S - 1) is None
    assert t.on_report({**IDLE, "gcode_state": "RUNNING"}, PROBE_DELAY_S + 1) is None
    partial = {k: v for k, v in SLOT.items() if k != "tray_color"}
    assert t.on_report({**IDLE, "ext_tray_raw": partial}, PROBE_DELAY_S + 1) is None
    req = t.on_report(IDLE, PROBE_DELAY_S + 1)
    assert req is not None and req.slot == SLOT and req.seq == PROBE_SEQ
    assert t.on_report(IDLE, PROBE_DELAY_S + 2) is None  # one-shot


def _probed(now=0.0):
    t = DevModeTracker()
    t.on_report(IDLE, now)
    assert t.on_report(IDLE, now + PROBE_DELAY_S + 1) is not None
    return t


def test_probe_success_turns_on():
    t = _probed()
    t.on_reply({"command": "ams_filament_setting", "sequence_id": PROBE_SEQ, "result": "success"})
    assert t.state is True and t.reason == "probe_ok"


def test_probe_verify_failed_turns_off():
    t = _probed()
    t.on_reply(
        {"sequence_id": PROBE_SEQ, "result": "failed", "reason": "mqtt message verify failed"}
    )
    assert t.state is False and t.reason == "probe_verify_failed"


def test_probe_no_reply_stays_unknown():
    t = _probed()
    t.on_report(IDLE, PROBE_DELAY_S + 1 + PROBE_TIMEOUT_S + 1)
    assert t.state is None and t.reason == "probe_no_reply"


def test_probe_bare_reply_is_inconclusive():
    t = _probed()
    t.on_reply({"command": "ams_filament_setting", "sequence_id": PROBE_SEQ})
    assert t.state is None


def test_lazy_detection_from_command_replies():
    t = DevModeTracker()
    t.on_reply({"command": "gcode_line", "result": "success"})
    assert t.state is True and t.reason == "command_ok"
    t.on_reply(
        {"command": "gcode_line", "result": "failed", "reason": "mqtt message verify failed"}
    )
    assert t.state is False and t.reason == "command_verify_failed"


def test_hms_verify_failed_latches_then_unlatches_to_unknown():
    t = DevModeTracker()
    t.on_report({**IDLE, "hms": VERIFY_HMS}, 0)
    assert t.state is False and t.reason == "hms_verify_failed"
    t.on_reply({"command": "gcode_line", "result": "success"})  # HMS outranks success
    assert t.state is False
    t.on_report({**IDLE, "hms": []}, 1)
    assert t.state is None


def test_no_probe_when_state_already_known():
    t = DevModeTracker()
    t.on_reply({"result": "success"})
    t.on_report(IDLE, 0)
    assert t.on_report(IDLE, PROBE_DELAY_S + 1) is None
