"""HMS fault decoding: ``hms[{attr, code}]`` / ``print_error`` -> code string,
severity, module, English description, wiki URL and remedy actions.

Catalogs ship in ``app/data`` (Bambu Studio's HMS tables + the
``hms_actions.json`` remedy map keyed by 3-char serial prefix). Lookups never
raise: an unknown code still yields its raw code and wiki link.
"""

from __future__ import annotations

import json
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
WIKI_URL = "https://wiki.bambulab.com/en/x1/troubleshooting/hmscode/{code}"

_catalog: dict | None = None
_actions: dict | None = None

# Bambu Studio's HMS module ids (attr >> 24).
MODULES = {0x03: "mainboard", 0x05: "mc", 0x07: "ams", 0x08: "toolhead", 0x0C: "xcam"}
SEVERITIES = {1: "fatal", 2: "serious", 3: "common", 4: "info"}


def _load() -> tuple[dict, dict]:
    global _catalog, _actions
    if _catalog is None:
        _catalog = json.loads((_DATA / "hms_catalog.json").read_text(encoding="utf-8"))
        _actions = json.loads((_DATA / "hms_actions.json").read_text(encoding="utf-8"))
    return _catalog, _actions or {}


def lookup_fault(full_code: str | None, model: str | None = None) -> str | None:
    """Catalog text for a 16-hex ``hms`` / 8-hex ``print_error`` code ("" =
    listed without text, None = not listed). A model-specific text (3-char
    serial prefix) wins over the merged one."""
    if not full_code:
        return None
    code = full_code.strip().upper()
    catalog, _ = _load()
    key, by_model = {16: ("hms", "hms_by_model"), 8: ("error", "error_by_model")}.get(
        len(code), (None, None)
    )
    if key is None:
        return None
    if model:
        specific = catalog.get(by_model, {}).get(model.upper(), {}).get(code)
        if specific is not None:
            return specific
    return catalog.get(key, {}).get(code)


def actions_for(model: str | None, code: str) -> list[str]:
    """Remedy actions for a code (``code`` = hex without separators)."""
    _, actions = _load()
    return list(actions.get((model or "").upper(), {}).get(code.upper(), []))


def _entry(full_code: str, severity: int, module: int, model: str | None, kind: str) -> dict:
    code = full_code.upper()
    grouped = "_".join(code[i : i + 4] for i in range(0, len(code), 4))
    # Remedy tables key on the short 8-hex form; hms[] 16-hex codes fall back
    # to (module+part, error) -> first/last four groups, as the printers do.
    acts = actions_for(model, code) or (
        actions_for(model, code[:4] + code[-4:]) if len(code) == 16 else []
    )
    return {
        "code": grouped,
        "full_code": code,
        "kind": kind,
        "severity": SEVERITIES.get(severity, "info" if severity else "unknown"),
        "severity_level": severity,
        "module": MODULES.get(module, f"0x{module:02X}"),
        "description": lookup_fault(code, model) or None,
        "wiki_url": WIKI_URL.format(code=grouped),
        "actions": acts,
    }


def decode_hms(attr: int, code: int, model: str | None = None) -> dict:
    """Decode one ``hms[]`` item. Never raises."""
    try:
        attr, code = int(attr) & 0xFFFFFFFF, int(code) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return _entry("0" * 16, 0, 0, model, "hms")
    return _entry(f"{attr:08X}{code:08X}", (code >> 16) & 0xFFFF, (attr >> 24) & 0xFF, model, "hms")


def decode_print_error(value: int, model: str | None = None) -> dict:
    """Decode a non-zero ``print_error`` word (level lives in the error's top nibble)."""
    value = int(value) & 0xFFFFFFFF
    level = {0x4: 1, 0x8: 2, 0xC: 3}.get((value >> 12) & 0xF, 0)
    return _entry(f"{value:08X}", level, (value >> 24) & 0xFF, model, "print_error")


def decode_state(state: dict | None, serial: str | None = None) -> list[dict]:
    """All active faults in a printer state dict (hms[] then print_error)."""
    state = state or {}
    model = (serial or "")[:3] or None
    out = []
    items = state.get("hms")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            code = int(item.get("code") or 0)
        except (TypeError, ValueError):
            out.append(decode_hms(None, None, model))  # malformed -> "unknown" entry
            continue
        # Codes < 0x4000 are status/phase indicators, not faults.
        if code >= 0x4000:
            out.append(decode_hms(item.get("attr", 0), code, model))
    pe = state.get("print_error")
    if isinstance(pe, int) and pe:
        out.append(decode_print_error(pe, model))
    return out
