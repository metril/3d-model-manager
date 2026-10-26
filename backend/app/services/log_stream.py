"""Cross-service log capture for the admin log viewer.

Every long-lived process (api, workers, beat, printerd, virtual printer)
attaches a ``RedisLogHandler`` that appends each record, as one JSON line, to
a single capped Redis list (``tdmm:logs``) with a monotonically increasing
``id``. The API tails that list for the viewer (``app.api.admin_logs``).
Redis is already required by every service, so this needs no shared volume.

The handler is fire-and-forget: short socket timeouts, a re-entrancy guard,
and a back-off after a failure, so logging can never stall or crash a caller.
Secrets are scrubbed (``redact_line``) both when a record is stored and again
when it is read (rules may be tightened after lines were stored).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time

LOG_LIST_KEY = "tdmm:logs"
LOG_SEQ_KEY = "tdmm:logs:seq"
MAX_ENTRIES = 5000
MAX_MESSAGE_CHARS = 8000
RETRY_AFTER_S = 15.0
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
SERVICES = ("api", "worker-io", "worker-cpu", "beat", "printerd", "virtual-printer")

_URL_CREDS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^@\s/]+@")
_KV_SECRET = re.compile(
    r"(?i)\b(access[_-]?code|password|passwd|secret|token|api[_-]?key|apikey|authorization"
    r"|private[_-]?key|credential|cookie)(\"?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"')}\]]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")


def redact_line(line: str) -> str:
    """Scrub URL credentials, ``key=value`` secrets and bearer tokens."""
    line = _URL_CREDS.sub(r"\1***@", line)
    line = _BEARER.sub(lambda m: f"{m.group(1)} ***", line)
    return _KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}***", line)


def detect_service() -> str:
    """Name this process for the log viewer (env/argv based; no config)."""
    explicit = os.environ.get("TDMM_SERVICE")
    if explicit:
        return explicit
    argv = " ".join(sys.argv)
    if "printerd" in argv:
        return "printerd"
    if "virtual_printer" in argv:
        return "virtual-printer"
    if "celery" in argv and " beat" in argv:
        return "beat"
    if os.environ.get("ROLE") == "worker":
        return f"worker-{os.environ.get('QUEUES', 'io').split(',')[0]}"
    return "api"


class RedisLogHandler(logging.Handler):
    def __init__(self, redis_url: str, service: str, level: int = logging.DEBUG) -> None:
        super().__init__(level)
        self.redis_url = redis_url
        self.service = service
        self._client = None
        self._pid = -1
        self._retry_at = 0.0
        self._local = threading.local()

    def _redis(self):
        if self._client is None or self._pid != os.getpid():  # fork-safe (celery prefork)
            import redis

            self._client = redis.Redis.from_url(
                self.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0
            )
            self._pid = os.getpid()
        return self._client

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._local, "busy", False) or record.name.startswith(
            ("redis", "urllib3", "asyncio")
        ):
            return
        now = time.monotonic()
        if now < self._retry_at:
            return
        self._local.busy = True
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg = f"{msg}\n{logging.Formatter().formatException(record.exc_info)}"
            r = self._redis()
            seq = r.incr(LOG_SEQ_KEY)
            entry = {
                "id": int(seq),
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "service": self.service,
                "msg": redact_line(msg)[:MAX_MESSAGE_CHARS],
            }
            pipe = r.pipeline(transaction=False)
            pipe.lpush(LOG_LIST_KEY, json.dumps(entry))
            pipe.ltrim(LOG_LIST_KEY, 0, MAX_ENTRIES - 1)
            pipe.execute()
        except Exception:  # noqa: BLE001 - logging must never break the caller
            self._retry_at = now + RETRY_AFTER_S
            self._client = None
        finally:
            self._local.busy = False


def install(redis_url: str, service: str | None = None, logger: logging.Logger | None = None):
    """Attach one ``RedisLogHandler`` to ``logger`` (root by default); idempotent."""
    target = logger or logging.getLogger()
    for h in target.handlers:
        if isinstance(h, RedisLogHandler):
            return h
    handler = RedisLogHandler(redis_url, service or detect_service())
    target.addHandler(handler)
    return handler


# -- reader (API side) -------------------------------------------------------


def _level_no(name: str) -> int:
    return {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}.get(
        name.upper(), 0
    )


def parse_entries(raw: list[bytes | str]) -> list[dict]:
    out: list[dict] = []
    for item in raw:
        try:
            e = json.loads(item)
            e["msg"] = redact_line(str(e.get("msg", "")))
            out.append(e)
        except (ValueError, TypeError):
            continue
    return out


def filter_entries(
    entries: list[dict],
    *,
    service: str | None = None,
    level: str | None = None,
    q: str | None = None,
    after: int | None = None,
) -> list[dict]:
    """``entries`` newest-first in, newest-first out."""
    min_no = _level_no(level) if level else 0
    needle = q.lower() if q else None
    out = []
    for e in entries:
        if after is not None and e.get("id", 0) <= after:
            continue
        if service and e.get("service") != service:
            continue
        if min_no and _level_no(str(e.get("level", ""))) < min_no:
            continue
        if needle and needle not in (e.get("msg", "") + " " + e.get("logger", "")).lower():
            continue
        out.append(e)
    return out


def format_entry(e: dict) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(e.get("ts", 0))))
    return (
        f"{ts} {e.get('level', ''):<8} [{e.get('service', '')}] "
        f"{e.get('logger', '')} {e.get('msg', '')}"
    )
