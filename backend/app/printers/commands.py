"""Command bus v2 dispatch table (Q2): per-command arg validation + the
adapter call. Shared by printerd (execution) and the API (early 422)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.printers.base import PrinterAdapter

JOG_STEPS = (1, 10, 50)
COLD_EXTRUDE_C = 170
GCODE_MAX_BYTES = 1024
PRINTING_STATES = frozenset({"RUNNING", "PAUSE"})
# error code -> HTTP status used by the API
HTTP_STATUS = {
    "dev_mode_off": 409,
    "printing": 409,
    "not_printing": 409,
    "cold_extrude": 409,
    "unsupported": 422,
    "invalid": 422,
    "error": 502,
}


class CommandError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _num(args: dict, key: str, lo: float, hi: float, *, integer: bool = False):
    v = args.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise CommandError("invalid", f"{key} must be a number")
    if integer:
        v = int(round(v))
    if not lo <= v <= hi:
        raise CommandError("invalid", f"{key} must be within {lo}..{hi}")
    return v


def _choice(args: dict, key: str, options: tuple[str, ...]) -> str:
    v = str(args.get(key, "")).lower()
    if v not in options:
        raise CommandError("invalid", f"{key} must be one of {', '.join(options)}")
    return v


def _bool(args: dict, key: str, default: bool | None = None) -> bool:
    v = args.get(key, default)
    if not isinstance(v, bool):
        raise CommandError("invalid", f"{key} must be a boolean")
    return v


def _v_hms_action(args: dict) -> dict:
    from app.printers.bambu import hms_action_payload

    err = str(args.get("print_error", ""))
    if not re.fullmatch(r"[0-9A-Fa-f]{8}|[0-9A-Fa-f]{16}", err):
        raise CommandError("invalid", "print_error must be 8 or 16 hex chars")
    action = str(args.get("action", ""))
    if hms_action_payload(err, action, None) is None:
        raise CommandError("unsupported", f"unsupported HMS action {action!r}")
    job_id = args.get("job_id")
    return {"print_error": err, "action": action, "job_id": str(job_id)[:64] if job_id else None}


_TEMP_MAX = {"nozzle": 350, "bed": 120, "chamber": 65}


def _v_none(args: dict) -> dict:
    return {}


def _v_temperature(args: dict) -> dict:
    target = _choice(args, "target", tuple(_TEMP_MAX))
    return {"target": target, "value": _num(args, "value", 0, _TEMP_MAX[target], integer=True)}


def _v_fan(args: dict) -> dict:
    return {
        "fan": _choice(args, "fan", ("part", "aux", "chamber")),
        "pct": _num(args, "pct", 0, 100, integer=True),
    }


def _v_light(args: dict) -> dict:
    return {"on": _bool(args, "on")}


def _v_speed(args: dict) -> dict:
    return {"level": _num(args, "level", 1, 4, integer=True)}


def clamp_step(mm: float) -> float:
    """Snap |mm| to the nearest allowed jog step, keeping the sign."""
    step = min(JOG_STEPS, key=lambda s: abs(s - abs(mm)))
    return float(step if mm > 0 else -step)


def _v_jog(args: dict) -> dict:
    axis = str(args.get("axis", "")).upper()
    if axis not in ("X", "Y", "Z"):
        raise CommandError("invalid", "axis must be X, Y or Z")
    mm = _num(args, "mm", -1000, 1000)
    if mm == 0:
        raise CommandError("invalid", "mm must be non-zero")
    return {"axis": axis, "mm": clamp_step(mm)}


def _v_extrude(args: dict) -> dict:
    mm = _num(args, "mm", -100, 100)
    if mm == 0:
        raise CommandError("invalid", "mm must be non-zero")
    return {"mm": float(mm)}


def _v_gcode(args: dict) -> dict:
    g = args.get("gcode")
    if not isinstance(g, str) or not g.strip():
        raise CommandError("invalid", "gcode must be a non-empty string")
    if len(g.encode()) > GCODE_MAX_BYTES:
        raise CommandError("invalid", f"gcode exceeds {GCODE_MAX_BYTES} bytes")
    return {"gcode": g.strip()}


def _v_airduct(args: dict) -> dict:
    return {"mode": _choice(args, "mode", ("cooling", "heating"))}


def _v_calibrate(args: dict) -> dict:
    return {
        "bed_leveling": _bool(args, "bed_leveling", True),
        "vibration": _bool(args, "vibration", True),
        "motor_noise": _bool(args, "motor_noise", True),
    }


def _v_skip(args: dict) -> dict:
    ids = args.get("object_ids")
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) > 64
        or any(isinstance(i, bool) or not isinstance(i, int) for i in ids)
    ):
        raise CommandError("invalid", "object_ids must be 1-64 integers")
    return {"object_ids": sorted(set(ids))}


def _v_load(args: dict) -> dict:
    return {"tray": _num(args, "tray", 0, 255, integer=True)}


@dataclass(frozen=True)
class Spec:
    validate: Callable[[dict], dict]
    run: Callable[[PrinterAdapter, dict], None]
    refuse_printing: bool = False  # refused server-side while RUNNING/PAUSE
    needs_printing: bool = False
    cold_check: bool = False


DISPATCH: dict[str, Spec] = {
    "pause": Spec(_v_none, lambda a, _: a.pause()),
    "resume": Spec(_v_none, lambda a, _: a.resume()),
    "stop": Spec(_v_none, lambda a, _: a.stop()),
    "set_temperature": Spec(
        _v_temperature, lambda a, x: a.set_temperature(x["target"], x["value"])
    ),
    "set_fan": Spec(_v_fan, lambda a, x: a.set_fan(x["fan"], x["pct"])),
    "set_light": Spec(_v_light, lambda a, x: a.set_light(x["on"])),
    "set_speed": Spec(_v_speed, lambda a, x: a.set_speed(x["level"])),
    "home": Spec(_v_none, lambda a, _: a.home(), refuse_printing=True),
    "jog": Spec(_v_jog, lambda a, x: a.jog(x["axis"], x["mm"]), refuse_printing=True),
    "extrude": Spec(
        _v_extrude, lambda a, x: a.extrude(x["mm"]), refuse_printing=True, cold_check=True
    ),
    "gcode": Spec(_v_gcode, lambda a, x: a.send_gcode(x["gcode"]), refuse_printing=True),
    "set_airduct": Spec(_v_airduct, lambda a, x: a.set_airduct(x["mode"])),
    "calibrate": Spec(
        _v_calibrate,
        lambda a, x: a.calibrate(x["bed_leveling"], x["vibration"], x["motor_noise"]),
        refuse_printing=True,
    ),
    "clear_hms": Spec(_v_none, lambda a, _: a.clear_hms()),
    "hms_action": Spec(
        _v_hms_action,
        lambda a, x: a.execute_hms_action(x["print_error"], x["action"], x["job_id"]),
    ),
    "skip_objects": Spec(
        _v_skip, lambda a, x: a.skip_objects(x["object_ids"]), needs_printing=True
    ),
    "load_filament": Spec(_v_load, lambda a, x: a.load_filament(x["tray"]), refuse_printing=True),
    "unload_filament": Spec(_v_none, lambda a, _: a.unload_filament(), refuse_printing=True),
}
ALWAYS_ALLOWED = frozenset({"pause", "resume", "stop"})  # attempted even if dev mode is off


def validate(command: str, args: dict | None) -> dict:
    spec = DISPATCH.get(command)
    if spec is None:
        raise CommandError("unsupported", f"unknown command {command!r}")
    return spec.validate(args or {})
