"""Structured logging setup.

Configures stdlib ``logging`` with a single structured line format and wires
uvicorn's own loggers into the same handler/formatter so application and
server logs look consistent regardless of which component emitted them.
"""

import logging
import logging.config
import time
from collections import deque
from typing import Any

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"


def build_log_config(level: str = "INFO") -> dict[str, Any]:
    """Return a ``logging.config.dictConfig``-compatible configuration dict."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": LOG_FORMAT},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            },
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["console"], "level": level, "propagate": False},
        },
    }


class RingBufferHandler(logging.Handler):
    """Keeps the last ``capacity`` formatted records for the support bundle."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self.lines: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # noqa: BLE001
            self.handleError(record)


RING = RingBufferHandler()
RING.setFormatter(logging.Formatter(LOG_FORMAT))


def configure_logging(level: str = "INFO") -> None:
    """Apply the structured logging configuration to the process."""
    logging.config.dictConfig(build_log_config(level))
    root = logging.getLogger()
    if RING not in root.handlers:
        root.addHandler(RING)


_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def apply_level(name: str) -> bool:
    """Set the root (and uvicorn) log level at runtime. Returns ``False``
    without touching anything when the level is already in effect."""
    level = logging.getLevelName(name.upper())
    if name.upper() not in _LEVELS or not isinstance(level, int):
        return False
    root = logging.getLogger()
    if root.level == level:
        return False
    root.setLevel(level)
    for logger_name in _UVICORN_LOGGERS:
        logging.getLogger(logger_name).setLevel(level)
    return True


class LevelPoller:
    """Applies the DB-backed ``server.log_level`` at most once per ``ttl_s``
    per process (so hot paths like celery's ``task_prerun`` stay cheap)."""

    def __init__(self, ttl_s: float = 30.0) -> None:
        self.ttl_s = ttl_s
        self._last: float | None = None

    def _due(self) -> bool:
        now = time.monotonic()
        if self._last is not None and now - self._last < self.ttl_s:
            return False
        self._last = now
        return True

    def maybe_apply_sync(self, session_factory) -> bool:
        if not self._due():
            return False
        from app.services.server_config import get_server_config_sync

        try:
            with session_factory() as session:
                return apply_level(get_server_config_sync(session).log_level)
        except Exception:  # noqa: BLE001 - never break the caller over a log level
            logging.getLogger(__name__).debug("log level poll failed", exc_info=True)
            return False

    async def maybe_apply(self, db_factory) -> bool:
        if not self._due():
            return False
        from app.services.server_config import get_server_config

        try:
            async with db_factory() as db:
                return apply_level((await get_server_config(db)).log_level)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).debug("log level poll failed", exc_info=True)
            return False
