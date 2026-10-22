"""Short-lived, unauthenticated signed download tokens for desktop slicer
deep links (R10-C, plan item 11). Desktop slicers (OrcaSlicer, Bambu Studio,
PrusaSlicer, Elegoo Slicer) open ``<scheme>://open?file=<url>`` and fetch
that URL themselves -- with no session cookie -- so
``GET /files/{id}/download`` needs an alternate, signed-token auth path
alongside its normal cookie auth (``app.api.files``).

HMAC-SHA256 over ``file_id:exp`` keyed by the same on-disk/env secret
``app.crypto`` already uses for at-rest encryption
(``load_or_create_printer_key``) -- one app secret to provision and rotate,
not a second one. Token shape is urlsafe-base64 ``<payload>.<sig>`` where
``payload`` is itself urlsafe-base64-encoded ``file_id:exp``, so the whole
thing round-trips through a URL query string with no extra escaping.
"""

from __future__ import annotations

import base64
import hmac
import time
from hashlib import sha256

from app.config import Settings
from app.crypto import load_or_create_printer_key

DEFAULT_TTL_S = 600


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _hmac(settings: Settings, payload: bytes) -> bytes:
    key = load_or_create_printer_key(settings)
    return hmac.new(key, payload, sha256).digest()


def sign_file_download(settings: Settings, file_id: int, ttl_s: int = DEFAULT_TTL_S) -> str:
    """Mint a token authorizing ``GET .../files/{file_id}/download`` for the
    next ``ttl_s`` seconds with no session cookie required."""
    exp = int(time.time()) + ttl_s
    payload = f"{file_id}:{exp}".encode("ascii")
    sig = _hmac(settings, payload)
    return f"{_b64encode(payload)}.{_b64encode(sig)}"


def verify(settings: Settings, token: str) -> int | None:
    """Validate ``token``, returning the ``file_id`` it authorizes, or
    ``None`` if it's malformed, tampered with, or expired. Signature
    comparison is constant-time (``hmac.compare_digest``)."""
    payload_b64, sep, sig_b64 = token.partition(".")
    if not sep or not payload_b64 or not sig_b64:
        return None
    try:
        payload = _b64decode(payload_b64)
        sig = _b64decode(sig_b64)
    except ValueError:  # malformed base64 -> reject
        return None

    expected_sig = _hmac(settings, payload)
    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        file_id_str, exp_str = payload.decode("ascii").split(":")
        file_id = int(file_id_str)
        exp = int(exp_str)
    except ValueError:
        return None

    if time.time() >= exp:
        return None

    return file_id


def sign_scoped(settings: Settings, scope: str, ttl_s: int = DEFAULT_TTL_S) -> str:
    """Mint a short-lived token authorizing an arbitrary ``scope`` string
    (P4 Task 4: ``f"camera:{printer_id}:{user_id}"``) rather than a single
    file id. Payload shape ``s|{scope}|{exp}`` -- the ``|`` delimiter (vs.
    the file token's ``:``) keeps the two token families from ever
    cross-verifying: a file token's payload has no ``|``, and a scoped
    token's payload has no bare ``int:int`` shape ``verify()`` expects.
    """
    exp = int(time.time()) + ttl_s
    payload = f"s|{scope}|{exp}".encode("ascii")
    sig = _hmac(settings, payload)
    return f"{_b64encode(payload)}.{_b64encode(sig)}"


def verify_scoped(settings: Settings, token: str, scope_prefix: str) -> str | None:
    """Validate ``token``, returning the scope it authorizes iff that scope
    starts with ``scope_prefix`` -- or ``None`` if malformed, tampered,
    expired, or scoped to something else entirely."""
    payload_b64, sep, sig_b64 = token.partition(".")
    if not sep or not payload_b64 or not sig_b64:
        return None
    try:
        payload = _b64decode(payload_b64)
        sig = _b64decode(sig_b64)
    except ValueError:
        return None

    expected_sig = _hmac(settings, payload)
    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        kind, scope, exp_str = payload.decode("ascii").split("|")
        exp = int(exp_str)
    except ValueError:
        return None

    if kind != "s":
        return None
    if time.time() >= exp:
        return None
    if not scope.startswith(scope_prefix):
        return None

    return scope
