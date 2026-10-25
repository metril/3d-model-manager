"""Tiny in-process hook registry. Subscribers register at import/startup;
``fire`` never raises into the caller (a bad subscriber must not break the
request that triggered it)."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable

log = logging.getLogger(__name__)

_SUBSCRIBERS: dict[str, list[Callable[..., None]]] = defaultdict(list)


def subscribe(name: str, fn: Callable[..., None]) -> None:
    if fn not in _SUBSCRIBERS[name]:
        _SUBSCRIBERS[name].append(fn)


def unsubscribe(name: str, fn: Callable[..., None]) -> None:
    if fn in _SUBSCRIBERS[name]:
        _SUBSCRIBERS[name].remove(fn)


def fire(name: str, *args: object, **kw: object) -> None:
    for fn in list(_SUBSCRIBERS[name]):
        try:
            fn(*args, **kw)
        except Exception:  # noqa: BLE001
            log.exception("hook %s subscriber failed", name)


def plate_cleared(printer_id: int, user_id: int | None) -> None:
    """Fired after ``POST /printers/{id}/clear-plate``; subscribers take
    ``(printer_id, user_id)``."""
    fire("plate_cleared", printer_id, user_id)


def on_plate_cleared(fn: Callable[[int, int | None], None]) -> None:
    subscribe("plate_cleared", fn)
