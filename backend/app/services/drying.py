"""AMS drying: support tables, preflight, humidity and preset helpers (T1).

A leaf module (no app imports at import time) so ``printers.capabilities``
can use the tables. Ported from upstream ``drying_preflight`` /
``printer_manager`` / ``ams_humidity``; the tables are keyed by our
``capabilities.model_key`` rather than free-text model names."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# -- support tables ----------------------------------------------------------
# Models that cannot dry at all (no AMS 2 Pro / AMS-HT compatibility).
DRYING_UNSUPPORTED_MODELS = frozenset({"a1", "a1mini"})
# AMS can dry but only from the printer's own screen (the firmware acks the
# command and ignores it, upstream #2533).
DRYING_SCREEN_ONLY_MODELS = frozenset({"p1p", "p1s"})
# Remote drying minimum firmware (exact model keys; absent = allowed).
DRYING_MIN_FIRMWARE: dict[str, str] = {
    "h2d": "01.02.30.00",
    "h2s": "01.02.00.00",
    "x1": "01.09.00.00",
    "x1c": "01.09.00.00",
    "p2s": "01.02.00.00",
}
# "Print while drying" (concurrent with a print); absent = not supported.
DRY_WHILE_PRINTING_MIN_FIRMWARE: dict[str, str] = {
    "h2d": "01.03.00.00",
    "h2dpro": "01.02.00.00",
    "h2s": "01.02.00.00",
    "x1c": "01.11.02.00",
    "p2s": "01.02.00.00",
}

SCREEN_ONLY_DETAIL = "This printer only supports AMS drying from its own screen"
UNSUPPORTED_DETAIL = "Drying not supported for this printer model or firmware version"

# dry_status (info bits 4-7)
ACTIVE_DRY_STATUSES = frozenset({1, 2, 3})  # checking, drying, cooling

DRY_SF_REASON_MESSAGES = {
    0: "Printer is busy",
    1: "Insufficient power: too many AMS drying or external PSU required",
    2: "AMS is busy",
    3: "Filament is at the AMS outlet, retract it first",
    4: "AMS is already starting a drying cycle",
    5: "Not supported in 2D mode",
    6: "AMS is already drying",
    7: "AMS firmware is upgrading",
    8: "Plug in the external AMS power adapter to start drying",
}
POWER_REASON_CODES = frozenset({1, 8})
RETRACT_REASON_CODE = 3
WAITING_REASON_POWER = "ams_power_required"
WAITING_REASON_RETRACT = "ams_retract_filament"
WAITING_REASON_BLOCKED = "ams_blocked"
WAITING_REASON_BUSY = "printer_busy"
WAITING_REASON_OFFLINE = "printer_offline"

DRY_TEMP_RANGE = (45, 85)
DRY_HOURS_RANGE = (1, 24)

DEFAULT_PRESETS: dict[str, dict[str, int]] = {
    "PLA": {"n3f_temp": 45, "n3s_temp": 45, "n3f_hours": 12, "n3s_hours": 12},
    "PETG": {"n3f_temp": 65, "n3s_temp": 65, "n3f_hours": 12, "n3s_hours": 12},
    "TPU": {"n3f_temp": 65, "n3s_temp": 75, "n3f_hours": 12, "n3s_hours": 18},
    "ABS": {"n3f_temp": 65, "n3s_temp": 80, "n3f_hours": 12, "n3s_hours": 8},
    "ASA": {"n3f_temp": 65, "n3s_temp": 80, "n3f_hours": 12, "n3s_hours": 8},
    "PA": {"n3f_temp": 65, "n3s_temp": 85, "n3f_hours": 12, "n3s_hours": 12},
    "PC": {"n3f_temp": 65, "n3s_temp": 80, "n3f_hours": 12, "n3s_hours": 8},
    "PVA": {"n3f_temp": 65, "n3s_temp": 85, "n3f_hours": 12, "n3s_hours": 18},
}
FILAMENT_KEY_ALIASES = {
    "NYLON": "PA",
    "PA6": "PA",
    "PA11": "PA",
    "PA12": "PA",
    "PAHT": "PA",
    "PPA": "PA",
}


# -- firmware / support --------------------------------------------------------
def _fw_tuple(fw: str | None) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in str(fw).strip().split(".")) if fw else None
    except ValueError:
        return None


def _fw_at_least(fw: str | None, minimum: str) -> bool:
    have, need = _fw_tuple(fw), _fw_tuple(minimum)
    return have is not None and need is not None and have >= need


def drying_screen_only(model_key: str | None) -> bool:
    return bool(model_key) and model_key in DRYING_SCREEN_ONLY_MODELS


def supports_drying(model_key: str | None, firmware: str | None, *, require_firmware: bool = True):
    """Remote drying allowed? Unknown model -> False; models without a table
    row are allowed (the firmware rejects gracefully); ``require_firmware``
    False skips the firmware gate (no live status to read it from)."""
    if not model_key or model_key in DRYING_UNSUPPORTED_MODELS | DRYING_SCREEN_ONLY_MODELS:
        return False
    minimum = DRYING_MIN_FIRMWARE.get(model_key)
    if minimum is None or not require_firmware:
        return True
    return _fw_at_least(firmware, minimum)


def supports_drying_while_printing(model_key: str | None, firmware: str | None) -> bool:
    minimum = DRY_WHILE_PRINTING_MIN_FIRMWARE.get(model_key or "")
    return minimum is not None and _fw_at_least(firmware, minimum)


def check_drying_supported(
    model_key: str | None, firmware: str | None, *, require_firmware: bool = True
) -> tuple[bool, bool, str | None]:
    """``(supported, screen_only, reason)``; reason is ``None`` |
    ``"screen_only"`` | ``"unsupported"``."""
    if drying_screen_only(model_key):
        return False, True, "screen_only"
    if not supports_drying(model_key, firmware, require_firmware=require_firmware):
        return False, False, "unsupported"
    return True, False, None


# -- AMS unit helpers ------------------------------------------------------------
def find_ams_unit(state: Mapping | None, ams_id: int) -> dict | None:
    """Locate a unit in a printerd public state (``ams_units``)."""
    for unit in (state or {}).get("ams_units") or []:
        try:
            if int(unit.get("id", -1)) == ams_id:
                return unit
        except (TypeError, ValueError, AttributeError):
            continue
    return None


def unit_trays(state: Mapping | None, ams_id: int) -> list[dict]:
    return [t for t in (state or {}).get("trays") or [] if t.get("ams_unit") == ams_id]


def blocking_reason_codes(unit: Mapping | None) -> list[int]:
    codes: list[int] = []
    for code in (unit or {}).get("dry_sf_reason") or []:
        try:
            c = int(code)
        except (TypeError, ValueError):
            continue
        if c in DRY_SF_REASON_MESSAGES:
            codes.append(c)
    return codes


def primary_reason_code(codes: list[int]) -> int | None:
    for code in codes:
        if code in POWER_REASON_CODES:
            return code
    if RETRACT_REASON_CODE in codes:
        return RETRACT_REASON_CODE
    return codes[0] if codes else None


def waiting_reason_for_codes(codes: list[int]) -> str:
    code = primary_reason_code(codes)
    if code in POWER_REASON_CODES:
        return WAITING_REASON_POWER
    if code == RETRACT_REASON_CODE:
        return WAITING_REASON_RETRACT
    return WAITING_REASON_BLOCKED


def resolve_filament(trays: list[dict], filament: str) -> str:
    """Empty filament -> first loaded tray's type, else PLA (the printer
    rejects an empty type)."""
    if filament:
        return filament
    for t in trays:
        if t.get("tray_type"):
            return str(t["tray_type"])
    return "PLA"


def ams_humidity_percent(ams_data: Any) -> float | None:
    """Relative humidity % for one unit (``humidity_raw``), else ``None``.
    The 1-5 ``humidity`` index runs the opposite way and is never converted."""
    if not isinstance(ams_data, Mapping):
        return None
    raw = ams_data.get("humidity_raw")
    try:
        return None if raw is None else float(raw)
    except (TypeError, ValueError):
        return None


def module_type(ams_id: int) -> str:
    """``n3s`` (AMS-HT, ids 128+) or ``n3f`` (AMS 2 Pro)."""
    return "n3s" if ams_id >= 128 else "n3f"


def validate_preflight(
    *,
    model_key: str | None,
    firmware: str | None,
    state: Mapping,
    ams_id: int,
    filament: str,
    stop: bool = False,
) -> dict:
    """Shared by the API gate and the scheduler. Returns ``{"filament": str}``
    or raises ``DryingRefused(http, code, detail, extra)``."""
    ok, screen_only, reason = check_drying_supported(model_key, firmware)
    if screen_only:
        raise DryingRefused(409, "screen_only", SCREEN_ONLY_DETAIL)
    if not ok:
        raise DryingRefused(422, "unsupported", UNSUPPORTED_DETAIL)
    unit = find_ams_unit(state, ams_id)
    if unit is None:
        raise DryingRefused(422, "invalid", f"unknown AMS unit {ams_id}")
    if stop:
        return {"filament": ""}
    printing = state.get("gcode_state") in ("RUNNING", "PAUSE")
    if printing and not supports_drying_while_printing(model_key, firmware):
        raise DryingRefused(409, "printing", "drying while printing is not supported")
    codes = blocking_reason_codes(unit)
    if codes:
        raise DryingRefused(
            409,
            "blocked",
            DRY_SF_REASON_MESSAGES[primary_reason_code(codes)],
            {"codes": codes, "messages": [DRY_SF_REASON_MESSAGES[c] for c in codes]},
        )
    return {"filament": resolve_filament(unit_trays(state, ams_id), filament)}


class DryingRefused(Exception):
    def __init__(self, http: int, code: str, detail: str, extra: dict | None = None) -> None:
        super().__init__(detail)
        self.http, self.code, self.detail, self.extra = http, code, detail, extra or {}


# -- presets / thresholds ---------------------------------------------------------
def resolve_filament_key(tray_type: str | None, table: Mapping[str, object]) -> str | None:
    """Table key answering for a tray material ("PLA Basic" -> PLA,
    "PA6-CF" -> PA). Exact match wins, then the base before ``-``, then
    aliases; ``None`` when nothing matches."""
    raw = str(tray_type or "").strip().upper()
    if not raw:
        return None
    raw = raw.split()[0] if " " in raw else raw
    for cand in (raw, raw.split("-")[0]):
        key = FILAMENT_KEY_ALIASES.get(cand, cand)
        if key in table:
            return key
    return None


def resolve_humidity_threshold(
    trays: list[dict], thresholds: Mapping[str, int], fallback: int
) -> int:
    """Lowest threshold across the unit's loaded filament types; ``default``
    key / ``fallback`` (= ams_humidity_fair) otherwise."""
    if not thresholds:
        return fallback
    default = thresholds.get("default", fallback)
    cands: list[int] = []
    for t in trays:
        tt = str(t.get("tray_type") or "").strip()
        if not tt:
            continue
        key = resolve_filament_key(tt, thresholds)
        cands.append(thresholds[key] if key is not None else default)
    return min(cands) if cands else default


def conservative_params(
    trays: list[dict], ams_id: int, presets: Mapping[str, Mapping[str, int]]
) -> tuple[int, int, str] | None:
    """``(temp, hours, filament)``: lowest temp / longest time over the
    unit's loaded trays that have a preset row; ``None`` if none do."""
    mod = module_type(ams_id)
    temp: int | None = None
    hours: int | None = None
    fil = ""
    for t in trays:
        key = resolve_filament_key(t.get("tray_type"), presets)
        if key is None or not presets[key]:
            continue
        p = presets[key]
        tp, hr = p.get(f"{mod}_temp", 55), p.get(f"{mod}_hours", 12)
        temp = tp if temp is None or tp < temp else temp
        hours = hr if hours is None or hr > hours else hours
        fil = fil or key
    return None if temp is None else (temp, hours or 12, fil)
