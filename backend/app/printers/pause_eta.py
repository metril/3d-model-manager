"""Pause ETA + heads-up: the pauses a slicer put into a print, and the time to
the next one.

Bambu Studio writes ``M73 P<pct> R<minutes_left>`` before each layer and a
pause as ``;PAUSE_PRINT`` / ``M400 U1`` after a layer-change marker. The slicer's
time left at a pause and the printer's own ``mc_remaining_time`` both count
down to the end of the same print, so the time to a pause is their difference.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Iterable

DEFAULT_HEADS_UP_MIN = 5
MAX_HEADS_UP_MIN = 120
POINTS_TTL_S = 7 * 86400

_LAYER_RE = re.compile(r"^;\s*layer num/total_layer_count:\s*(\d+)")
_M73_RE = re.compile(r"^M73\s+P\d+\s+R(\d+)")


def points_key(printer_id: int) -> str:
    return f"printer:{printer_id}:pause_points"


def warned_key(printer_id: int, job_id: int) -> str:
    return f"printer:{printer_id}:pause_warned:{job_id}"


def parse_pause_points(lines: Iterable[str]) -> list[dict]:
    """``[{"layer": int, "remaining_min": int | None}]`` in print order.

    ``layer`` is the 1-based layer the pause sits on (what the printer's
    ``layer_num`` reads while it waits there); ``remaining_min`` is the
    slicer's time left then. Pauses before the first layer (start G-code) are
    ignored, and a ``;PAUSE_PRINT`` + ``M400 U1`` pair counts once.
    """
    out: list[dict] = []
    layer = 0
    remaining: int | None = None
    paused_on = -1
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            m = _LAYER_RE.match(line)
            if m:
                layer = int(m.group(1))
            elif line.startswith(";PAUSE_PRINT") and layer > 0 and paused_on != layer:
                out.append({"layer": layer, "remaining_min": remaining})
                paused_on = layer
            continue
        m = _M73_RE.match(line)
        if m:
            remaining = int(m.group(1))
        elif (
            (line == "M400 U1" or line.startswith("M400 U1 ")) and layer > 0 and paused_on != layer
        ):
            out.append({"layer": layer, "remaining_min": remaining})
            paused_on = layer
    return out


def pause_points_from_3mf(source: bytes, plate: int) -> list[dict]:
    """Pause points of ``Metadata/plate_<plate>.gcode``; ``[]`` if unreadable."""
    try:
        with (
            zipfile.ZipFile(io.BytesIO(source)) as zf,
            zf.open(f"Metadata/plate_{plate}.gcode") as fh,
        ):
            return parse_pause_points(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 -- bad zip/zlib/EOF/unsupported: never fail a sendable print
        return []


def store_points(redis_client, printer_id: int, job_id: int, points: list[dict]) -> None:
    if not points:
        redis_client.delete(points_key(printer_id))
        return
    redis_client.set(
        points_key(printer_id),
        json.dumps({"job_id": job_id, "points": points}),
        ex=POINTS_TTL_S,
    )


def load_points(redis_client, printer_id: int) -> tuple[int | None, list[dict]]:
    raw = redis_client.get(points_key(printer_id))
    if not raw:
        return None, []
    try:
        data = json.loads(raw)
        return int(data["job_id"]), list(data["points"])
    except (ValueError, KeyError, TypeError):
        return None, []


def clear_points(redis_client, printer_id: int) -> None:
    redis_client.delete(points_key(printer_id))


def next_pause(public, points: list[dict]) -> dict | None:
    """``{"layer", "eta_s"}`` for the first pause above the printing layer.

    Only while RUNNING (a paused print waits at its pause). ``eta_s`` is None
    when the time can't be told: no time left reported yet, or a non-standard
    speed level (the slicer's times are for standard speed).
    """
    if getattr(public, "gcode_state", None) != "RUNNING":
        return None
    layer_now = getattr(public, "layer_num", 0) or 0
    remaining = getattr(public, "mc_remaining_time", 0) or 0
    standard = getattr(public, "speed_level", None) in (None, 2)
    for p in points or ():
        if p["layer"] > layer_now:
            eta_s = None
            if standard and remaining > 0 and p.get("remaining_min") is not None:
                eta_s = max(0, remaining - p["remaining_min"]) * 60
            return {"layer": p["layer"], "eta_s": eta_s}
    return None


def heads_up_minutes(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_HEADS_UP_MIN
    return min(max(n, 1), MAX_HEADS_UP_MIN)


def claim_warning(redis_client, printer_id: int, job_id: int, layer: int) -> bool:
    """True exactly once per (job, pause layer): the one-shot guard."""
    key = warned_key(printer_id, job_id)
    added = redis_client.sadd(key, layer)
    redis_client.expire(key, 86400)
    return bool(added)
