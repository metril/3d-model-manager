"""Command bus v2 dispatch table (Q2): per-command arg validation + the
adapter call. Shared by printerd (execution) and the API (early 422)."""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from app.printers.base import PrinterAdapter
from app.services import drying

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
    "screen_only": 409,
    "blocked": 409,
    "invalid": 422,
    "error": 502,
    "timeout": 504,
}


class CommandError(Exception):
    def __init__(self, code: str, message: str = "", extra: dict | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.extra = extra or {}


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


XCAM_MODULES = (
    "spaghetti_detector",
    "first_layer_inspector",
    "printing_monitor",
    "buildplate_marker_detector",
    "allow_skip_parts",
    "pileup_detector",
    "clump_detector",
    "airprint_detector",
)
XCAM_SENSITIVITIES = ("low", "medium", "high", "never_halt")
PRINT_OPTIONS = ("auto_recovery", "auto_switch_filament")
_HEX = re.compile(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?")


def _v_xcam(args: dict) -> dict:
    return {
        "module": _choice(args, "module", XCAM_MODULES),
        "enable": _bool(args, "enable"),
        "sensitivity": _choice({"s": args.get("sensitivity", "medium")}, "s", XCAM_SENSITIVITIES),
    }


def _v_print_option(args: dict) -> dict:
    return {"option": _choice(args, "option", PRINT_OPTIONS), "value": _bool(args, "value")}


def _ams_id(args: dict) -> int:
    ams_id = _num(args, "ams_id", 0, 255, integer=True)
    if not (ams_id <= 3 or 128 <= ams_id <= 135 or ams_id == 255):
        raise CommandError("invalid", "ams_id must be 0-3, 128-135 or 255")
    return ams_id


def _tray_id(args: dict, ams_id: int) -> int:
    if ams_id == 255:
        return _num(args, "tray_id", 254, 254, integer=True)
    return _num(args, "tray_id", 0, 3, integer=True)


def normalize_color(color: object) -> str:
    """``RRGGBB[AA]`` (optional ``#``) -> UPPERCASE ``RRGGBBAA`` (P1S firmware
    reads lowercase hex as zeros)."""
    c = str(color or "").strip().lstrip("#")
    if not _HEX.fullmatch(c):
        raise CommandError("invalid", "color must be RRGGBB or RRGGBBAA hex")
    return (c + "FF" if len(c) == 6 else c).upper()


def _short_str(args: dict, key: str, limit: int) -> str:
    v = args.get(key, "")
    if not isinstance(v, str) or len(v) > limit:
        raise CommandError("invalid", f"{key} must be a string of at most {limit} chars")
    return v


def _v_slot_configure(args: dict) -> dict:
    ams_id = _ams_id(args)
    lo = _num(args, "nozzle_min", 0, 350, integer=True)
    hi = _num(args, "nozzle_max", 0, 350, integer=True)
    if lo > hi:
        raise CommandError("invalid", "nozzle_min must not exceed nozzle_max")
    tray_type = _short_str(args, "tray_type", 32).strip()
    if not tray_type:
        raise CommandError("invalid", "tray_type is required")
    return {
        "ams_id": ams_id,
        "tray_id": _tray_id(args, ams_id),
        "tray_info_idx": _short_str(args, "tray_info_idx", 32),
        "tray_type": tray_type,
        "sub_brand": _short_str(args, "sub_brand", 64),
        "color": normalize_color(args.get("color")),
        "nozzle_min": lo,
        "nozzle_max": hi,
    }


def _v_slot_reset(args: dict) -> dict:
    ams_id = _ams_id(args)
    return {"ams_id": ams_id, "tray_id": _tray_id(args, ams_id)}


def normalize_sd_path(path: object) -> str:
    """Printer-SD path: absolute, no ``..``/NUL/backslash segments, collapsed
    slashes. Raises ``CommandError('invalid')``."""
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 512:
        raise CommandError("invalid", "path must be absolute")
    if "\x00" in path or "\\" in path:
        raise CommandError("invalid", "path contains illegal characters")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise CommandError("invalid", "path must not contain '..'")
    return "/" + "/".join(parts)


def is_sd_printable(path: str) -> bool:
    low = path.lower()
    return low.endswith(".3mf") or low.endswith(".gcode.3mf")


def _v_sd_print(args: dict) -> dict:
    path = normalize_sd_path(args.get("path"))
    if path == "/" or not is_sd_printable(path):
        raise CommandError("invalid", "only .3mf / .gcode.3mf files can be printed from the SD")
    plate = args.get("plate")
    if plate is not None:
        plate = _num({"plate": plate}, "plate", 1, 99, integer=True)
    return {"path": path, "plate": plate, "use_ams": _bool(args, "use_ams", True)}


def _drying_ams_id(args: dict) -> int:
    ams_id = _num(args, "ams_id", 0, 135, integer=True)
    if not (ams_id <= 3 or ams_id >= 128):
        raise CommandError("invalid", "ams_id must be 0-3 or 128-135")
    return ams_id


def _v_drying_start(args: dict) -> dict:
    return {
        "ams_id": _drying_ams_id(args),
        "temp": _num(args, "temp", *drying.DRY_TEMP_RANGE, integer=True),
        "duration_hours": _num(args, "duration_hours", *drying.DRY_HOURS_RANGE, integer=True),
        "filament": _short_str(args, "filament", 32).strip(),
        "rotate_tray": _bool(args, "rotate_tray", False),
    }


def _v_drying_stop(args: dict) -> dict:
    return {"ams_id": _drying_ams_id(args)}


def _pre_drying_start(clean: dict, state: dict) -> None:
    """State-only preflight (printerd + API; model/firmware gates live in
    ``services.drying.validate_preflight``): backfill the filament from the
    loaded tray, refuse an unknown unit or one reporting ``dry_sf_reason``."""
    ams_id = clean["ams_id"]
    unit = drying.find_ams_unit(state, ams_id)
    if unit is None:
        raise CommandError("invalid", f"unknown AMS unit {ams_id}")
    codes = drying.blocking_reason_codes(unit)
    if codes:
        raise CommandError(
            "blocked",
            drying.DRY_SF_REASON_MESSAGES[drying.primary_reason_code(codes)],
            {"codes": codes, "messages": [drying.DRY_SF_REASON_MESSAGES[c] for c in codes]},
        )
    clean["filament"] = drying.resolve_filament(drying.unit_trays(state, ams_id), clean["filament"])


# -- T2 K-profiles ----------------------------------------------------------
NOZZLE_DIAMETERS = ("0.2", "0.4", "0.6", "0.8", "1.0", "1.2")
_SETTING_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


def new_setting_id() -> str:
    """``PF`` + 17 random digits (the id format BambuStudio mints)."""
    return f"PF{10**16 + secrets.randbelow(9 * 10**16)}"


def _nozzle_diameter(args: dict, key: str = "nozzle_diameter") -> str:
    v = args.get(key, "0.4")
    try:
        if isinstance(v, bool):
            raise TypeError
        text = f"{float(v):.1f}"
    except (TypeError, ValueError) as e:
        raise CommandError("invalid", f"{key} must be a nozzle diameter") from e
    if text not in NOZZLE_DIAMETERS:
        raise CommandError("invalid", f"{key} must be one of {', '.join(NOZZLE_DIAMETERS)}")
    return text


def _extruder_id(args: dict) -> int:
    return _num(args, "extruder_id", 0, 1, integer=True)


def _v_kprofile_list(args: dict) -> dict:
    out: dict = {"nozzle_diameter": _nozzle_diameter(args)}
    if args.get("extruder_id") is not None:
        out["extruder_id"] = _extruder_id(args)
    return out


def _filament_id(args: dict) -> str:
    fid = _short_str(args, "filament_id", 32).strip()
    if not fid:
        raise CommandError("invalid", "filament_id is required")
    return fid


def _opt_setting_id(args: dict) -> str:
    sid = args.get("setting_id") or ""
    if sid and (not isinstance(sid, str) or not _SETTING_ID.fullmatch(sid)):
        raise CommandError("invalid", "setting_id has an invalid format")
    return sid


def _v_kprofile_set(args: dict) -> dict:
    name = _short_str(args, "name", 64).strip()
    if not name:
        raise CommandError("invalid", "name is required")
    out = {
        "name": name,
        "filament_id": _filament_id(args),
        "k_value": float(_num(args, "k_value", 0, 2)),
        "n_coef": float(_num({"n": args.get("n_coef", 0)}, "n", 0, 2)),
        "nozzle_diameter": _nozzle_diameter(args),
        "extruder_id": _extruder_id({"extruder_id": args.get("extruder_id", 0)}),
        "nozzle_id": _short_str(args, "nozzle_id", 32),
    }
    setting_id = _opt_setting_id(args)
    if setting_id:  # edit in place: the slot index is required
        out["cali_idx"] = _num(args, "cali_idx", 0, 4095, integer=True)
    else:
        setting_id = new_setting_id()
        out["cali_idx"] = -1
    out["setting_id"] = setting_id
    return out


def _v_kprofile_delete(args: dict) -> dict:
    return {
        "setting_id": _opt_setting_id(args),
        "cali_idx": _num(args, "cali_idx", 0, 4095, integer=True),
        "filament_id": _filament_id(args),
        "nozzle_diameter": _nozzle_diameter(args),
        "extruder_id": _extruder_id({"extruder_id": args.get("extruder_id", 0)}),
        "nozzle_id": _short_str(args, "nozzle_id", 32),
    }


def _v_kprofile_select(args: dict) -> dict:
    ams_id = _ams_id(args)
    if ams_id == 255 and args.get("tray_id") in (0, 1):  # dual-nozzle external spools
        tray_id = args["tray_id"]
    else:
        tray_id = _tray_id(args, ams_id)
    return {
        "ams_id": ams_id,
        "tray_id": tray_id,
        "cali_idx": _num(args, "cali_idx", -1, 4095, integer=True),
        "filament_id": _filament_id(args),
        "nozzle_diameter": _nozzle_diameter(args),
    }


@dataclass(frozen=True)
class Spec:
    validate: Callable[[dict], dict]
    run: Callable[[PrinterAdapter, dict], None]
    refuse_printing: bool = False  # refused server-side while RUNNING/PAUSE
    needs_printing: bool = False
    cold_check: bool = False
    # T1: ``(clean, state)`` hook run after the generic guards; may backfill
    # ``clean`` in place or raise ``CommandError`` (with ``extra``).
    preflight: Callable[[dict, dict], None] | None = None


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
    "set_xcam": Spec(_v_xcam, lambda a, x: a.set_xcam(x["module"], x["enable"], x["sensitivity"])),
    "set_print_option": Spec(
        _v_print_option, lambda a, x: a.set_print_option(x["option"], x["value"])
    ),
    "ams_slot_configure": Spec(
        _v_slot_configure,
        lambda a, x: a.ams_slot_configure(
            x["ams_id"],
            x["tray_id"],
            x["tray_info_idx"],
            x["tray_type"],
            x["sub_brand"],
            x["color"],
            x["nozzle_min"],
            x["nozzle_max"],
        ),
    ),
    "ams_slot_reset": Spec(_v_slot_reset, lambda a, x: a.ams_slot_reset(x["ams_id"], x["tray_id"])),
    "sd_print": Spec(
        _v_sd_print,
        lambda a, x: a.start_sd_print(x["path"], x["plate"], x["use_ams"]),
        refuse_printing=True,
    ),
    "drying_start": Spec(
        _v_drying_start,
        lambda a, x: a.drying_start(
            x["ams_id"], x["temp"], x["duration_hours"], x["filament"], x["rotate_tray"]
        ),
        preflight=_pre_drying_start,
    ),
    "drying_stop": Spec(_v_drying_stop, lambda a, x: a.drying_stop(x["ams_id"])),
    # T2: list is a query (allowed while printing); writes are not.
    "kprofile_list": Spec(
        _v_kprofile_list, lambda a, x: a.kprofile_list(x["nozzle_diameter"], x.get("extruder_id"))
    ),
    "kprofile_set": Spec(_v_kprofile_set, lambda a, x: a.kprofile_set(x), refuse_printing=True),
    "kprofile_delete": Spec(
        _v_kprofile_delete, lambda a, x: a.kprofile_delete(x), refuse_printing=True
    ),
    "kprofile_select": Spec(
        _v_kprofile_select,
        lambda a, x: a.kprofile_select(
            x["ams_id"], x["tray_id"], x["cali_idx"], x["filament_id"], x["nozzle_diameter"]
        ),
        refuse_printing=True,
    ),
}
ALWAYS_ALLOWED = frozenset({"pause", "resume", "stop"})  # attempted even if dev mode is off


def validate(command: str, args: dict | None) -> dict:
    spec = DISPATCH.get(command)
    if spec is None:
        raise CommandError("unsupported", f"unknown command {command!r}")
    return spec.validate(args or {})
