"""Sanitize slicer profile values Orca rejects (Bambu "auto" sentinels etc.)."""

from __future__ import annotations

import re

# key -> (lo, hi) accepted by OrcaSlicer's CLI.
KNOWN_RANGES: dict[str, tuple[float, float]] = {
    "tree_support_wall_count": (0, 2),
    # Bambu Studio 2.x writes -1 here; Orca only accepts >= 0.
    "raft_first_layer_expansion": (0, 1e9),
}

_RANGE_RE = re.compile(r"(\w+): (-?[\d.]+) not in range \[(-?[\d.]+),\s*(-?[\d.]+)\]")
_DROP = object()


def parse_range_error(msg: str) -> tuple[str, float, float] | None:
    m = _RANGE_RE.search(msg or "")
    if not m:
        return None
    try:
        return m.group(1), float(m.group(3)), float(m.group(4))
    except ValueError:
        return None


def _num(v: object) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _fix_scalar(v: object, lo: float, hi: float) -> object:
    """Return v unchanged, a clamped value of the same type, or _DROP."""
    n = _num(v)
    if n is None or lo <= n <= hi:
        return v
    if n < lo:
        return _DROP
    out = int(hi) if float(hi).is_integer() else hi
    return str(out) if isinstance(v, str) else out


def clamp_key(data: dict, key: str, lo: float, hi: float) -> bool:
    """In-place: drop ``key`` if below ``lo``, clamp if above ``hi``. True if changed."""
    if key not in data:
        return False
    v = data[key]
    if isinstance(v, list):
        fixed = [_fix_scalar(x, lo, hi) for x in v]
        if any(x is _DROP for x in fixed):
            del data[key]
            return True
        if fixed == v:
            return False
        data[key] = fixed
        return True
    fixed_v = _fix_scalar(v, lo, hi)
    if fixed_v is _DROP:
        del data[key]
        return True
    if fixed_v == v and type(fixed_v) is type(v):
        return False
    data[key] = fixed_v
    return True


def sanitize_profile(data: dict) -> tuple[dict, list[str]]:
    out = dict(data)
    changed = [k for k, (lo, hi) in KNOWN_RANGES.items() if clamp_key(out, k, lo, hi)]
    return out, changed
