"""Provider protocol shared by every notification transport (Q6).

Providers are synchronous (Celery workers are sync) and take an injectable
``httpx.Client`` so tests can mount a ``MockTransport``. They raise
``ProviderError`` with a short, secret-free message on failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

MASK = "********"
TIMEOUT_S = 10.0


class ProviderError(Exception):
    """Delivery failed; the message is safe to log and show in the UI."""


@dataclass(frozen=True)
class Message:
    title: str
    body: str
    event: str = ""
    photo: bytes | None = None  # JPEG


def field(
    name: str,
    label: str = "",
    *,
    type: str = "text",
    secret: bool = False,
    required: bool = False,
    placeholder: str | None = None,
    default: str | None = None,
    options: list[str] | None = None,
    multiline: bool = False,
    help: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label or name.replace("_", " ").capitalize(),
        "type": "password" if secret else type,
        "secret": secret,
        "required": required,
        "placeholder": placeholder,
        "default": default,
        "options": options,
        "multiline": multiline,
        "help": help,
    }


class Provider(Protocol):
    kind: str
    label: str
    supports_photo: bool
    fields: list[dict[str, Any]]

    def send(self, config: dict, msg: Message, client: httpx.Client) -> None: ...


def need(config: dict, *names: str) -> list[str]:
    values = [str(config.get(n) or "").strip() for n in names]
    missing = [n for n, v in zip(names, values, strict=True) if not v]
    if missing:
        raise ProviderError(f"missing required field: {', '.join(missing)}")
    return values


def check_url(url: str, label: str = "URL") -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ProviderError(f"{label} must start with http:// or https://")
    return url


def ensure_ok(resp: httpx.Response, label: str) -> None:
    """Fold any non-2xx into a body-free error (a server we don't control
    could echo credentials back)."""
    if not 200 <= resp.status_code < 300:
        raise ProviderError(f"{label} returned HTTP {resp.status_code}")
