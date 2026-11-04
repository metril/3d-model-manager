"""Filter MakerWorld print profiles down to the printers the user owns."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.importers.base import ImportFile

# Bambu dev codes -> normalized product name (see `_norm`).
_DEV_CODES = {
    "n1": "a1mini",
    "n2s": "a1",
    "c11": "p1p",
    "c12": "p1s",
    "blp001": "x1carbon",
    "blp002": "x1",
    "c13": "x1e",
    "o1d": "h2d",
    "o1e": "h2dpro",
    "o1s": "h2s",
    "o1c2": "h2c",
    "n7": "p2s",
    "n6": "x2d",
    "n9": "a2l",
    "n10": "h3c",
}
_ALIASES = {"x1c": "x1carbon"}


def _norm(name: str | None) -> str:
    s = re.sub(r"[\s_-]+", "", (name or "").lower())
    if s.startswith("bambulab"):
        s = s[len("bambulab") :]
    s = _DEV_CODES.get(s, s)
    return _ALIASES.get(s, s)


def _entry_matches(entry: dict, printer: Any) -> bool:
    want = _norm(getattr(printer, "model", None))
    if not want:
        return False
    if want not in {_norm(entry.get("product")), _norm(entry.get("model"))}:
        return False
    noz, have = entry.get("nozzle"), getattr(printer, "nozzle_mm", None)
    return not (noz and have and abs(float(noz) - float(have)) > 0.01)


def filter_profiles(
    files: list[ImportFile], printers: Iterable[Any], mode: str
) -> list[ImportFile]:
    """``mode="owned"``: keep profile files compatible with any enabled printer;
    files without profile metadata are always kept. If no profile matches (or
    no printer has a model), everything is kept."""
    if mode != "owned":
        return files
    owned = [p for p in printers if getattr(p, "enabled", True) and getattr(p, "model", None)]
    if not owned:
        return files
    kept = [
        f
        for f in files
        if not (f.meta or {}).get("compat")
        or any(_entry_matches(e, p) for e in f.meta["compat"] for p in owned)
    ]
    if not any((f.meta or {}).get("compat") for f in kept):
        return files
    return kept
