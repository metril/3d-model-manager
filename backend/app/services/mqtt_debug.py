"""Per-printer raw MQTT debug feed (admin modal).

printerd's adapter tap hands every raw report/reply here; each is masked,
pushed onto a short capped Redis list (so a freshly opened modal has history)
and published on a pub/sub channel the API relays over SSE. Outgoing printer
commands are echoed on the same feed (``dir == "cmd"``).
"""

from __future__ import annotations

import json
import re
import time

KEEP = 50
LIST_TTL_S = 3600
MAX_PAYLOAD_CHARS = 65536
_SECRET = re.compile(r"(token|password|secret|access_?code|api_?key|credential)", re.I)


def channel(printer_id: int) -> str:
    return f"printer:{printer_id}:mqtt"


def list_key(printer_id: int) -> str:
    return f"printer:{printer_id}:mqtt:log"


def mask(value: object) -> object:
    if isinstance(value, dict):
        return {k: ("***" if _SECRET.search(str(k)) and v else mask(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


def build_entry(direction: str, topic: str, payload: bytes | str | dict) -> dict:
    body: object
    if isinstance(payload, bytes | str):
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
        try:
            body = mask(json.loads(text))
        except ValueError:
            body = text[:MAX_PAYLOAD_CHARS]
    else:
        body = mask(payload)
    if len(json.dumps(body, default=str)) > MAX_PAYLOAD_CHARS:
        body = {"_truncated": True}
    return {"ts": time.time(), "dir": direction, "topic": topic, "payload": body}


def emit_sync(r, printer_id: int, entry: dict) -> None:
    """Best effort; callers wrap in ``suppress``."""
    data = json.dumps(entry, default=str)
    pipe = r.pipeline(transaction=False)
    pipe.lpush(list_key(printer_id), data)
    pipe.ltrim(list_key(printer_id), 0, KEEP - 1)
    pipe.expire(list_key(printer_id), LIST_TTL_S)
    pipe.publish(channel(printer_id), data)
    pipe.execute()
