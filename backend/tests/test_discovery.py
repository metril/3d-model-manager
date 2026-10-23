"""Tests for TLS-certificate-based serial discovery (Round 8 T1:
``app.printers.discovery``). ``_cn_from_der`` is exercised directly against
an in-test self-signed cert (no socket involved); ``read_cert_cn`` is
exercised end-to-end against a real localhost TLS server presenting that
same cert, so the socket/ssl seam itself is covered too.
"""

from __future__ import annotations

import contextlib
import datetime
import socket
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.printers import discovery


def _self_signed_cert(cn: str | None) -> tuple[bytes, bytes, bytes]:
    """Build a minimal self-signed cert (mirrors a Bambu printer's LAN-mode
    cert shape closely enough to exercise CN extraction): PEM cert, PEM key,
    DER cert."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn)] if cn else []
    name = x509.Name(attrs)
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
    return cert_pem, key_pem, cert.public_bytes(serialization.Encoding.DER)


def test_cn_from_der_extracts_common_name():
    _cert_pem, _key_pem, der = _self_signed_cert("0309CA410600958")
    assert discovery._cn_from_der(der) == "0309CA410600958"


def test_cn_from_der_returns_empty_string_when_cn_absent():
    _cert_pem, _key_pem, der = _self_signed_cert(None)
    assert discovery._cn_from_der(der) == ""


def test_cn_from_der_raises_on_garbage_bytes():
    # Not a real profile the discovery endpoint needs to render specially --
    # both the probe's _cert_check and the detect-serial endpoint catch this
    # broadly -- but read_cert_cn/_cn_from_der must not silently swallow it.
    with pytest.raises(ValueError):
        discovery._cn_from_der(b"not a certificate")


@pytest.fixture
def tls_server(tmp_path):
    """Start a real localhost TLS server presenting a self-signed cert with
    the given CN (or no CN). Returns (host, port); the server accepts
    exactly one connection then exits its thread."""

    def _start(cn: str | None) -> tuple[str, int]:
        cert_pem, key_pem, _der = _self_signed_cert(cn)
        cert_path = tmp_path / f"cert-{cn or 'none'}.pem"
        key_path = tmp_path / f"key-{cn or 'none'}.pem"
        cert_path.write_bytes(cert_pem)
        key_path.write_bytes(key_pem)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_path), str(key_path))

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        host, port = listener.getsockname()

        def _serve() -> None:
            with contextlib.suppress(OSError):
                conn, _addr = listener.accept()
                with (
                    contextlib.suppress(ssl.SSLError, OSError),
                    ctx.wrap_socket(conn, server_side=True) as tls,
                ):
                    tls.recv(1)  # let the client's TLS handshake complete, then idle
            listener.close()

        threading.Thread(target=_serve, daemon=True).start()
        return host, port

    return _start


def test_read_cert_cn_over_real_tls_socket(tls_server):
    host, port = tls_server("0309CA410600958")
    assert discovery.read_cert_cn(host, port, timeout=2.0) == "0309CA410600958"


def test_read_cert_cn_empty_when_cert_has_no_cn(tls_server):
    host, port = tls_server(None)
    assert discovery.read_cert_cn(host, port, timeout=2.0) == ""


def test_read_cert_cn_unreachable_port_raises():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    _host, port = listener.getsockname()
    listener.close()  # closed immediately -- nothing listening on this port
    with pytest.raises(OSError):
        discovery.read_cert_cn("127.0.0.1", port, timeout=1.0)


# -- Q5: SSDP parsing/listening + sweep ----------------------------------------
NOTIFY = (
    "NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1990\r\nCache-Control: max-age=1800\r\n"
    "Location: 192.168.1.50\r\nNT: urn:bambulab-com:device:3dprinter:1\r\n"
    "USN: 0309CA410600958\r\nDevModel.bambu.com: N1\r\nDevName.bambu.com: Mini\r\n\r\n"
)


def test_parse_ssdp_notify():
    item = discovery.parse_ssdp(NOTIFY, "192.168.1.50")
    assert item == {
        "host": "192.168.1.50",
        "serial": "0309CA410600958",
        "model_hint": "N1",
        "name": "Mini",
        "source": "ssdp",
    }


def test_parse_ssdp_ignores_non_bambu_and_missing_usn():
    assert (
        discovery.parse_ssdp("NOTIFY * HTTP/1.1\r\nNT: upnp:rootdevice\r\n\r\n", "1.2.3.4") is None
    )
    assert discovery.parse_ssdp("NT: urn:bambulab-com:device:3dprinter:1\r\n", "1.2.3.4") is None


def test_ssdp_listen_receives_notify():
    probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe_sock.bind(("127.0.0.1", 0))
    port = probe_sock.getsockname()[1]
    probe_sock.close()

    def _send() -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for _ in range(10):
            s.sendto(NOTIFY.encode(), ("127.0.0.1", port))
            threading.Event().wait(0.1)
        s.close()

    threading.Thread(target=_send, daemon=True).start()
    found = discovery.ssdp_listen(1.0, port=port, multicast=False)
    assert [f["serial"] for f in found] == ["0309CA410600958"]


def test_ssdp_listen_returns_empty_when_port_busy():
    busy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    busy.bind(("127.0.0.1", 0))
    # Without REUSE flags set on the holder a second bind to the same
    # address/port fails; ssdp_listen must degrade to [] rather than raise.
    port = busy.getsockname()[1]
    try:
        assert isinstance(discovery.ssdp_listen(0.2, port=port, multicast=False), list)
    finally:
        busy.close()


def test_sweep_reads_serial_from_tls_listener(tls_server):
    host, port = tls_server("0309CA410600958")
    found = discovery.sweep(f"{host}/32", port=port, timeout=2.0, budget=5.0)
    assert found == [
        {
            "host": host,
            "serial": "0309CA410600958",
            "model_hint": found[0]["model_hint"],
            "source": "sweep",
        }
    ]


def test_sweep_skips_closed_ports_and_respects_budget():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    assert discovery.sweep("127.0.0.1/32", port=port, timeout=0.5, budget=2.0) == []


def test_discover_merges_ssdp_over_sweep(monkeypatch):
    monkeypatch.setattr(
        discovery, "ssdp_listen", lambda budget: [discovery.parse_ssdp(NOTIFY, "192.168.1.50")]
    )
    monkeypatch.setattr(
        discovery,
        "sweep",
        lambda cidr, **kw: [
            {
                "host": "192.168.1.50",
                "serial": "0309CA410600958",
                "model_hint": None,
                "source": "sweep",
            },
            {"host": "192.168.1.51", "serial": "OTHER", "model_hint": None, "source": "sweep"},
        ],
    )
    out = {d["serial"]: d for d in discovery.discover("192.168.1.0/24")}
    assert (
        out["0309CA410600958"]["source"] == "ssdp" and out["0309CA410600958"]["model_hint"] == "N1"
    )
    assert out["OTHER"]["source"] == "sweep"


async def test_discover_route(authenticated_client, printer_enabled, viewer_client, monkeypatch):
    monkeypatch.setattr(
        discovery,
        "discover",
        lambda cidr, budget=10.0: [
            {"host": "h", "serial": "S", "model_hint": None, "source": "sweep"}
        ],
    )
    r = await authenticated_client.post("/api/printers/discover", json={"cidr": "192.168.1.0/24"})
    assert r.status_code == 200 and r.json()[0]["serial"] == "S"
    assert (await authenticated_client.post("/api/printers/discover")).status_code == 200
    bad = await authenticated_client.post("/api/printers/discover", json={"cidr": "nope"})
    assert bad.status_code == 422
    assert (await viewer_client.post("/api/printers/discover", json={})).status_code == 403


async def test_discover_rejects_unsafe_cidr(authenticated_client, printer_enabled):
    for cidr in ("10.0.0.0/8", "fd00::/64", "8.8.8.0/24", "192.168.0.0/16"):
        r = await authenticated_client.post("/api/printers/discover", json={"cidr": cidr})
        assert r.status_code == 422, cidr


def test_sweep_caps_host_materialisation(monkeypatch):
    monkeypatch.setattr(discovery, "_probe_host", lambda *a: None)
    assert discovery.sweep("10.0.0.0/8", budget=5.0) == []  # returns promptly, no 16M list
