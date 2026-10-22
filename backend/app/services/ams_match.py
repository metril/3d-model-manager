"""Match sliced filament needs to AMS trays: exact material type first,
then nearest colour by OKLab perceptual distance (P5 spool tray plan)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

DE_OK = 0.10
UNMAPPED = -1


@dataclass(frozen=True)
class TrayInfo:
    tray: int
    type: str | None
    color: str | None


@dataclass(frozen=True)
class FilamentNeed:
    filament_id: int
    type: str | None
    color: str | None
    grams: float | None


@dataclass(frozen=True)
class Match:
    filament_id: int
    tray: int | None
    status: Literal["ok", "color", "type", "none"]
    delta_e: float | None


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_oklab(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")[:6]  # tolerate 8-hex RRGGBBAA
    r, g, b = (_lin(int(h[i : i + 2], 16) / 255) for i in (0, 2, 4))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b  # noqa: E741
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (v ** (1 / 3) for v in (l, m, s))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def delta_e_ok(a: str, b: str) -> float:
    return math.dist(hex_to_oklab(a), hex_to_oklab(b))


def _norm_type(t: str | None) -> str | None:
    return t.strip().upper() if t is not None else None


def _types_match(a: str | None, b: str | None) -> bool:
    return _norm_type(a) == _norm_type(b) and _norm_type(a) is not None


def _color_delta(a: str | None, b: str | None) -> float:
    if a is None or b is None:
        return 0.0
    return delta_e_ok(a, b)


def match(
    needs: list[FilamentNeed],
    trays: list[TrayInfo],
    overrides: dict[int, int] | None = None,
) -> list[Match]:
    overrides = overrides or {}
    by_tray = {t.tray: t for t in trays}
    results: list[Match] = []
    for need in needs:
        override_tray = overrides.get(need.filament_id)
        if override_tray is not None:
            tray_info = by_tray.get(override_tray)
            if tray_info is not None and _types_match(need.type, tray_info.type):
                de = _color_delta(need.color, tray_info.color)
                status: Literal["ok", "color", "type", "none"] = "ok" if de <= DE_OK else "color"
                results.append(Match(need.filament_id, override_tray, status, de))
            else:
                results.append(Match(need.filament_id, override_tray, "type", None))
            continue

        candidates = [t for t in trays if _types_match(need.type, t.type)]
        if not candidates:
            results.append(Match(need.filament_id, None, "none", None))
            continue

        best = min(candidates, key=lambda t: _color_delta(need.color, t.color))
        de = _color_delta(need.color, best.color)
        status = "ok" if de <= DE_OK else "color"
        results.append(Match(need.filament_id, best.tray, status, de))
    return results


def to_ams_mapping(matches: list[Match]) -> list[int]:
    if not matches:
        return []
    max_id = max(m.filament_id for m in matches)
    mapping = [UNMAPPED] * max_id
    for m in matches:
        mapping[m.filament_id - 1] = m.tray if m.tray is not None else UNMAPPED
    return mapping
