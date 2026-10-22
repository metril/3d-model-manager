"""``app.services.signed_urls`` (R10-C): HMAC-signed, short-lived download
tokens for desktop slicer deep links.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import signed_urls

pytestmark = pytest.mark.usefixtures("data_dir")


def test_round_trip() -> None:
    settings = get_settings()
    token = signed_urls.sign_file_download(settings, 42)

    assert signed_urls.verify(settings, token) == 42


def test_expired_token_rejected() -> None:
    settings = get_settings()
    token = signed_urls.sign_file_download(settings, 42, ttl_s=-1)

    assert signed_urls.verify(settings, token) is None


def test_tampered_signature_rejected() -> None:
    settings = get_settings()
    token = signed_urls.sign_file_download(settings, 42)
    payload_b64, _, sig_b64 = token.partition(".")
    mid = len(sig_b64) // 2  # last char only carries padding bits; tamper a middle one
    flipped = "A" if sig_b64[mid] != "A" else "B"
    tampered = f"{payload_b64}.{sig_b64[:mid]}{flipped}{sig_b64[mid + 1 :]}"

    assert signed_urls.verify(settings, tampered) is None


def test_tampered_payload_rejected() -> None:
    settings = get_settings()
    token = signed_urls.sign_file_download(settings, 42)
    payload_b64, _, sig_b64 = token.partition(".")
    other_payload = signed_urls.sign_file_download(settings, 99).partition(".")[0]
    stitched = f"{other_payload}.{sig_b64}"

    assert stitched != token
    assert signed_urls.verify(settings, stitched) is None


def test_token_does_not_verify_for_a_different_file_id() -> None:
    settings = get_settings()
    token = signed_urls.sign_file_download(settings, 1)

    assert signed_urls.verify(settings, token) != 2


def test_malformed_token_rejected() -> None:
    settings = get_settings()

    assert signed_urls.verify(settings, "not-a-real-token") is None
    assert signed_urls.verify(settings, "") is None
    assert signed_urls.verify(settings, "onlyonepart") is None


def test_scoped_token_roundtrip_and_isolation() -> None:
    settings = get_settings()
    scope = "camera:1:5"
    scoped_token = signed_urls.sign_scoped(settings, scope)
    file_token = signed_urls.sign_file_download(settings, 1)

    assert signed_urls.verify_scoped(settings, scoped_token, "camera:1:") == scope
    assert signed_urls.verify_scoped(settings, scoped_token, "camera:2:") is None
    # A file token must never verify as scoped, and vice versa.
    assert signed_urls.verify_scoped(settings, file_token, "camera:1:") is None
    assert signed_urls.verify(settings, scoped_token) is None

    expired = signed_urls.sign_scoped(settings, scope, ttl_s=-1)
    assert signed_urls.verify_scoped(settings, expired, "camera:1:") is None
