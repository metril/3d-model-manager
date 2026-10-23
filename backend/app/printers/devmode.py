"""Developer Mode detection (Q2, decision 2): never mutates the printer.

Order: (a) the ``fun`` bitmask when the report carries it (X1/H2) -- no
probe; (b) otherwise an echo probe (``ams_filament_setting`` re-sending the
external slot's exact current values) only while IDLE/FINISH, 5 s after the
first report, and only when the slot's full tray record is known; (c) lazy
detection from any command reply (``success`` => on, ``verify failed`` =>
off) or the HMS ``MQTT_VERIFY_FAILED`` code. ``state is None`` = unknown.
Pure logic (no I/O): the worker feeds reports/replies in and publishes the
returned ``ProbeRequest``.
"""

from __future__ import annotations

from dataclasses import dataclass

FUN_BIT = 0x20000000  # set => MQTT command verification on => dev mode OFF
HMS_MQTT_VERIFY_FAILED = "0500050000010007"
PROBE_DELAY_S = 5.0
PROBE_TIMEOUT_S = 10.0
PROBE_SEQ = "9000017"
PROBE_FIELDS = ("tray_info_idx", "tray_type", "tray_color", "nozzle_temp_min", "nozzle_temp_max")
IDLE_STATES = frozenset({"IDLE", "FINISH"})


@dataclass(frozen=True)
class ProbeRequest:
    seq: str
    slot: dict  # the external slot's current tray record, echoed verbatim


def _parse_fun(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 16)
        except ValueError:
            return None
    return None


def _verify_failed(hms: list) -> bool:
    for e in hms or []:
        try:
            if f"{int(e['attr']):08X}{int(e['code']):08X}" == HMS_MQTT_VERIFY_FAILED:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


class DevModeTracker:
    def __init__(self) -> None:
        self.state: bool | None = None
        self.reason: str | None = None
        self._first_seen: float | None = None
        self._last_fun: int | None = None
        self._from_hms = False
        self._probed = False
        self._probe_seq: str | None = None
        self._probe_at = 0.0

    def _set(self, state: bool | None, reason: str | None) -> None:
        self.state, self.reason = state, reason

    def on_report(self, merged: dict, now: float) -> ProbeRequest | None:
        if self._first_seen is None:
            self._first_seen = now
        fun = _parse_fun(merged.get("fun"))
        if _verify_failed(merged.get("hms") or []):
            self._from_hms = True
            self._set(False, "hms_verify_failed")
        elif self._from_hms:  # verdict withdrawn: unknown again, allow a re-probe
            self._from_hms = False
            self._probed = False
            self._set(None, None)
            self._last_fun = None
        if fun is not None and fun != self._last_fun and not self._from_hms:
            self._last_fun = fun
            self._set((fun & FUN_BIT) == 0, "fun_bit")
        if self._probe_seq and now - self._probe_at > PROBE_TIMEOUT_S:
            self._probe_seq = None
            if self.state is None:
                self._set(None, "probe_no_reply")
        if fun is not None or self.state is not None or self._probed:
            return None
        if now - self._first_seen < PROBE_DELAY_S:
            return None
        if merged.get("gcode_state") not in IDLE_STATES:
            return None
        slot = merged.get("ext_tray_raw")
        if not isinstance(slot, dict) or any(slot.get(f) is None for f in PROBE_FIELDS):
            return None
        self._probed = True
        self._probe_seq = PROBE_SEQ
        self._probe_at = now
        return ProbeRequest(PROBE_SEQ, dict(slot))

    def on_reply(self, reply: dict, now: float = 0.0) -> None:
        """A printer reply (a ``print`` dict carrying ``result``)."""
        result = str(reply.get("result", "")).lower()
        reason = str(reply.get("reason", "")).lower()
        is_probe = self._probe_seq is not None and str(reply.get("sequence_id")) == self._probe_seq
        if is_probe:
            self._probe_seq = None
        if result == "failed" and "verify failed" in reason:
            self._set(False, "probe_verify_failed" if is_probe else "command_verify_failed")
        elif result == "success" and not self._from_hms and self._last_fun is None:
            self._set(True, "probe_ok" if is_probe else "command_ok")
        elif is_probe and self.state is None:
            self._set(None, "probe_inconclusive")


def is_verify_failed_reply(reply: dict | None) -> bool:
    return bool(
        reply
        and str(reply.get("result", "")).lower() == "failed"
        and "verify failed" in str(reply.get("reason", "")).lower()
    )
