"""Tests for the clean-room A1 camera protocol client (P4 T1:
``app.printers.camera_a1``). ``FrameParser`` is exercised directly as a pure
parser (no socket); ``CameraA1Client`` is exercised end-to-end against a
real localhost TLS server, mirroring the TLS-fixture pattern used by
``tests/test_discovery.py``.
"""

from __future__ import annotations

import contextlib
import datetime
import socket
import ssl
import struct
import threading
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.printers.camera_a1 import (
    HEADER_LEN,
    MAX_FRAME_BYTES,
    CameraA1Client,
    CameraAuthError,
    CameraError,
    CameraProtocolError,
    CameraTimeout,
    FrameParser,
    auth_packet,
)

JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"


def frame(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload)) + bytes(12) + payload


def _self_signed_cert() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(minutes=10))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


@pytest.fixture
def _tls_ctx(tmp_path):
    cert_pem, key_pem = _self_signed_cert()
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx


@pytest.fixture
def tls_server(_tls_ctx):
    """Start a localhost TLS server that reads the 80-byte auth packet,
    asserts it matches ``auth_packet("bblp", "12345678")``, then sends two
    JPEG frames and closes. Returns (host, port)."""

    def _start() -> tuple[str, int]:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        host, port = listener.getsockname()

        def _serve() -> None:
            with contextlib.suppress(OSError, ssl.SSLError):
                conn, _addr = listener.accept()
                with _tls_ctx.wrap_socket(conn, server_side=True) as tls:
                    auth = tls.recv(80)
                    assert auth == auth_packet("bblp", "12345678")
                    tls.sendall(frame(JPEG) + frame(JPEG))
            listener.close()

        threading.Thread(target=_serve, daemon=True).start()
        return host, port

    return _start


@pytest.fixture
def tls_server_closing(_tls_ctx):
    """Start a localhost TLS server that reads the auth packet then closes
    the connection immediately, before sending any frame."""

    def _start() -> tuple[str, int]:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        host, port = listener.getsockname()

        def _serve() -> None:
            with contextlib.suppress(OSError, ssl.SSLError):
                conn, _addr = listener.accept()
                with _tls_ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(80)
            listener.close()

        threading.Thread(target=_serve, daemon=True).start()
        return host, port

    return _start


@pytest.fixture
def tls_server_silent(_tls_ctx):
    """Start a localhost TLS server that accepts, completes the handshake,
    and then sends nothing. Returns (host, port)."""

    def _start() -> tuple[str, int]:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        host, port = listener.getsockname()

        def _serve() -> None:
            with contextlib.suppress(OSError, ssl.SSLError):
                conn, _addr = listener.accept()
                with _tls_ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(80)  # consume the auth packet, then idle
                    time.sleep(5)
            listener.close()

        threading.Thread(target=_serve, daemon=True).start()
        return host, port

    return _start


def test_auth_packet_layout():
    p = auth_packet("bblp", "12345678")
    assert len(p) == 80
    assert p[:16] == struct.pack("<IIII", 0x40, 0x3000, 0, 0)
    assert p[16:48] == b"bblp".ljust(32, b"\0")
    assert p[48:80] == b"12345678".ljust(32, b"\0")


def test_auth_packet_rejects_long_code():
    with pytest.raises(ValueError):
        auth_packet("bblp", "x" * 33)


@pytest.mark.parametrize("split", range(1, 2 * (HEADER_LEN + len(JPEG))))
def test_parser_any_chunking(split):
    data = frame(JPEG) + frame(JPEG)
    parser = FrameParser()
    out = parser.feed(data[:split])
    out += parser.feed(data[split:])
    assert out == [JPEG, JPEG]


def test_parser_drops_non_jpeg_payload():
    parser = FrameParser()
    out = parser.feed(frame(b"not a jpeg frame"))
    assert out == []


def test_parser_bad_length_raises():
    parser = FrameParser()
    with pytest.raises(CameraProtocolError):
        parser.feed(struct.pack("<I", 0) + bytes(12))

    parser2 = FrameParser()
    with pytest.raises(CameraProtocolError):
        parser2.feed(struct.pack("<I", MAX_FRAME_BYTES + 1) + bytes(12))


def test_client_sends_auth_and_yields_frames(tls_server):
    host, port = tls_server()
    with CameraA1Client(host, "12345678", port=port) as client:
        frames = list(client.frames())
    assert frames == [JPEG, JPEG]


def test_client_peer_close_is_auth_error(tls_server_closing):
    host, port = tls_server_closing()
    with (
        CameraA1Client(host, "12345678", port=port) as client,
        pytest.raises(CameraAuthError),
    ):
        list(client.frames())


def test_client_read_timeout(tls_server_silent):
    host, port = tls_server_silent()
    start = time.monotonic()
    with (
        pytest.raises(CameraTimeout),
        CameraA1Client(host, "12345678", port=port, read_timeout=0.5) as client,
    ):
        list(client.frames())
    assert time.monotonic() - start < 2.0


def test_close_unblocks_recv(tls_server_silent):
    host, port = tls_server_silent()
    client = CameraA1Client(host, "12345678", port=port, read_timeout=30.0)
    client.__enter__()
    start = time.monotonic()

    def _close_soon() -> None:
        time.sleep(0.1)
        client.close()

    threading.Thread(target=_close_soon, daemon=True).start()
    with contextlib.suppress(CameraError):
        list(client.frames())
    assert time.monotonic() - start < 1.0


def test_stop_event_ends_iteration(tls_server_silent):
    host, port = tls_server_silent()
    stop = threading.Event()
    with CameraA1Client(host, "12345678", port=port, read_timeout=30.0, stop=stop) as client:

        def _stop_soon() -> None:
            time.sleep(0.1)
            stop.set()

        threading.Thread(target=_stop_soon, daemon=True).start()
        start = time.monotonic()
        frames = list(client.frames())
        assert frames == []
        assert time.monotonic() - start < 1.0


def test_close_before_enter_never_hangs():
    client = CameraA1Client("127.0.0.1", "12345678", port=65500)
    client.close()  # must not hang/raise even though __enter__ never ran
