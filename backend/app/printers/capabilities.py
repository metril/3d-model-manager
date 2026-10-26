"""Per-model printer capability table (Q1). ``model_key`` normalises the
free-text ``Printer.model`` (display name or internal code), falling back to
the serial prefix. Computed, never stored. ``verified`` marks models tested
on real hardware; the UI shows an "untested" hint otherwise."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Literal

from app.services import drying

CameraKind = Literal["a1", "rtsp", "none"]


@dataclass(frozen=True)
class Capabilities:
    model_key: str | None = None
    chamber_heater: bool = False
    chamber_temp: bool = False
    airduct: bool = False
    door: bool = False
    # Dual nozzle (H2D): per-nozzle temps, active-extruder switch and the
    # ams_mapping2 dispatch. Ported from Bambuddy; UNVERIFIED on real hardware
    # (``verified`` stays False for these models).
    dual_nozzle: bool = False
    aux_fan: bool = False
    chamber_fan: bool = False
    camera: CameraKind = "none"
    lidar: bool = False
    max_bed: int | None = None
    max_nozzle: int | None = None
    ams_drying: bool = False
    # T1: P1P/P1S dry from their own screen only; concurrent print+dry needs fw.
    drying_screen_only: bool = False
    ams_drying_while_printing: bool = False
    # T3: xcam modules the model exposes; SD browser / print-from-SD.
    xcam_modules: tuple[str, ...] = ()
    sd_browser: bool = False
    sd_print: bool = False
    # T2: K-profiles (pressure advance) list/set/delete/select.
    kprofiles: bool = False
    verified: bool = False

    def as_dict(self) -> dict:
        d = asdict(self)
        d["xcam_modules"] = list(self.xcam_modules)
        return d


_XCAM_BASIC = ("spaghetti_detector", "first_layer_inspector", "allow_skip_parts")
_XCAM_X1 = (
    "spaghetti_detector",
    "first_layer_inspector",
    "printing_monitor",
    "buildplate_marker_detector",
    "allow_skip_parts",
)
_XCAM_ALL = (*_XCAM_X1, "pileup_detector", "clump_detector", "airprint_detector")


def _caps(key: str, **kw) -> Capabilities:
    kw.setdefault("kprofiles", True)
    return Capabilities(model_key=key, **kw)


CAPABILITIES: dict[str, Capabilities] = {
    "a1mini": _caps(
        "a1mini",
        camera="a1",
        max_bed=80,
        max_nozzle=300,
        xcam_modules=_XCAM_BASIC,
        sd_browser=True,
        sd_print=True,
        verified=True,
    ),
    "a1": _caps(
        "a1",
        aux_fan=True,
        camera="a1",
        max_bed=100,
        max_nozzle=300,
        xcam_modules=_XCAM_BASIC,
        sd_browser=True,
        sd_print=True,
    ),
    "p1p": _caps(
        "p1p",
        aux_fan=True,
        camera="a1",
        max_bed=100,
        max_nozzle=300,
        xcam_modules=_XCAM_BASIC,
        sd_browser=True,
        sd_print=True,
    ),
    "p1s": _caps(
        "p1s",
        aux_fan=True,
        chamber_fan=True,
        camera="a1",
        max_bed=100,
        max_nozzle=300,
        xcam_modules=_XCAM_BASIC,
        sd_browser=True,
        sd_print=True,
    ),
    "x1": _caps(
        "x1",
        chamber_temp=True,
        door=True,
        aux_fan=True,
        chamber_fan=True,
        camera="rtsp",
        lidar=True,
        max_bed=120,
        max_nozzle=300,
        xcam_modules=_XCAM_X1,
        sd_browser=True,
        sd_print=True,
    ),
    "x1c": _caps(
        "x1c",
        chamber_temp=True,
        door=True,
        aux_fan=True,
        chamber_fan=True,
        camera="rtsp",
        lidar=True,
        max_bed=120,
        max_nozzle=300,
        xcam_modules=_XCAM_X1,
        sd_browser=True,
        sd_print=True,
    ),
    "x1e": _caps(
        "x1e",
        chamber_heater=True,
        chamber_temp=True,
        door=True,
        aux_fan=True,
        chamber_fan=True,
        camera="rtsp",
        lidar=True,
        max_bed=120,
        max_nozzle=320,
        xcam_modules=_XCAM_X1,
        sd_browser=True,
        sd_print=True,
    ),
    "p2s": _caps(
        "p2s",
        chamber_temp=True,
        airduct=True,
        door=True,
        aux_fan=True,
        chamber_fan=True,
        camera="rtsp",
        max_bed=100,
        max_nozzle=300,
        xcam_modules=_XCAM_ALL,
        sd_browser=True,
        sd_print=True,
    ),
    "h2s": _caps(
        "h2s",
        chamber_heater=True,
        chamber_temp=True,
        airduct=True,
        door=True,
        aux_fan=True,
        chamber_fan=True,
        camera="rtsp",
        max_bed=120,
        max_nozzle=350,
        xcam_modules=_XCAM_ALL,
        sd_browser=True,
        sd_print=True,
    ),
    "h2d": _caps(
        "h2d",
        chamber_heater=True,
        chamber_temp=True,
        airduct=True,
        door=True,
        dual_nozzle=True,
        aux_fan=True,
        chamber_fan=True,
        camera="rtsp",
        max_bed=120,
        max_nozzle=350,
        xcam_modules=_XCAM_ALL,
        sd_browser=True,
        sd_print=True,
    ),
    "h2dpro": _caps(
        "h2dpro",
        chamber_heater=True,
        chamber_temp=True,
        airduct=True,
        door=True,
        dual_nozzle=True,
        aux_fan=True,
        chamber_fan=True,
        camera="rtsp",
        max_bed=120,
        max_nozzle=350,
        xcam_modules=_XCAM_ALL,
        sd_browser=True,
        sd_print=True,
    ),
}


def _with_drying(caps: Capabilities, firmware: str | None, known_fw: bool) -> Capabilities:
    """Fill the drying flags from the T1 tables. Without a firmware string
    (``known_fw`` False) only the model gate applies."""
    key = caps.model_key
    return replace(
        caps,
        ams_drying=drying.supports_drying(key, firmware, require_firmware=known_fw),
        drying_screen_only=drying.drying_screen_only(key),
        ams_drying_while_printing=drying.supports_drying_while_printing(key, firmware),
    )


CAPABILITIES = {k: _with_drying(v, None, False) for k, v in CAPABILITIES.items()}

# Normalised (lowercase alnum) display names and internal product codes.
_ALIASES: dict[str, str] = {
    "a1mini": "a1mini",
    "n1": "a1mini",
    "a1": "a1",
    "n2s": "a1",
    "p1p": "p1p",
    "c11": "p1p",
    "p1s": "p1s",
    "c12": "p1s",
    "x1": "x1",
    "blp002": "x1",
    "x1c": "x1c",
    "x1carbon": "x1c",
    "blp001": "x1c",
    "x1e": "x1e",
    "c13": "x1e",
    "p2s": "p2s",
    "n7": "p2s",
    "h2s": "h2s",
    "o1s": "h2s",
    "h2d": "h2d",
    "o1d": "h2d",
    "h2dpro": "h2dpro",
    "o1e": "h2dpro",
    "o2d": "h2dpro",
}

# Serial-number prefix (first 3 chars) -> model key. 094 is shared by H2D
# and H2S upstream; model name wins when present, prefix is only a fallback.
_SERIAL_PREFIXES: dict[str, str] = {
    "030": "a1mini",
    "039": "a1",
    "01S": "p1p",
    "01P": "p1s",
    "00M": "x1c",
    "00W": "x1",
    "03W": "x1e",
    "22E": "p2s",
    "093": "h2s",
    "094": "h2d",
    "239": "h2dpro",
}


def _norm(text: str | None) -> str:
    s = (text or "").lower().replace("bambu lab", "").replace("bambulab", "")
    return re.sub(r"[^a-z0-9]", "", s)


def model_key(model: str | None, serial: str | None = None) -> str | None:
    """Canonical key for ``model`` (name or code), else from ``serial``'s
    prefix; ``None`` when neither is recognised."""
    key = _ALIASES.get(_norm(model))
    if key:
        return key
    prefix = (serial or "").strip().upper()[:3]
    return _SERIAL_PREFIXES.get(prefix)


def capabilities_for(
    model: str | None, serial: str | None = None, firmware: str | None = None
) -> Capabilities:
    """Capabilities for a printer; unknown models get a conservative
    all-off, unverified default. ``firmware`` (the OTA version) tightens the
    drying flags; omitted, only the model gate applies."""
    key = model_key(model, serial)
    caps = CAPABILITIES.get(key) if key else None
    if caps is None:
        return Capabilities()
    return _with_drying(caps, firmware, True) if firmware else caps
