"""Structured logging setup.

Configures stdlib ``logging`` with a single structured line format and wires
uvicorn's own loggers into the same handler/formatter so application and
server logs look consistent regardless of which component emitted them.
"""

import logging
import logging.config
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
