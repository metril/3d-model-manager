"""TLS certificate-based serial discovery for a Bambu printer on the LAN
(Round 8 T1). With LAN Mode + Developer Mode on, the printer's MQTT port
(:8883) serves a self-signed cert (issuer "BBL CA") whose subject Common
Name IS the printer's serial -- reading it needs nothing more than a raw TLS
handshake, no MQTT login. Powers both the "Detect" button
(``POST /printers/detect-serial``) and the probe's cert cross-match
(``app.printers.probe``).

Import-safety: stdlib ``socket``/``ssl`` + ``cryptography`` only --
``cryptography`` is already a direct, eagerly-imported dependency elsewhere
(``app.crypto``), so this module is safe to import at module level (no
``bambulabs_api``/``paho`` here; see ``tests/test_flag_off_imports.py``).
"""

from __future__ import annotations

import contextlib
import ipaddress
import itertools
import re
import socket
import ssl
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from cryptography import x509
from cryptography.x509.oid import NameOID


def _cn_from_der(der: bytes) -> str:
    """Pure parse: a DER-encoded X.509 certificate -> its subject Common
    Name, or ``""`` if the cert has none. Factored out of ``read_cert_cn``
    so tests can drive the parsing logic directly against an in-test
    self-signed cert, without a real socket/TLS handshake."""
    cert = x509.load_der_x509_certificate(der)
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(names[0].value) if names else ""


def read_cert_cn(host: str, port: int = 8883, *, timeout: float = 4.0) -> str:
    """Open a raw TLS connection to ``(host, port)`` and return the peer
    certificate's subject Common Name. Never verifies the cert (self-signed,
    issuer "BBL CA") -- this is a read-only discovery probe, not an auth
    step; ``check_hostname``/``verify_mode`` are deliberately relaxed the
    same way ``app.printers.probe`` relaxes them for the MQTT TLS session.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection((host, port), timeout=timeout) as sock,
        ctx.wrap_socket(sock, server_hostname=host) as tls,
    ):
        der = tls.getpeercert(binary_form=True)
    return _cn_from_der(der or b"")


# -- Q5 network discovery ----------------------------------------------------
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 2021  # Bambu printers NOTIFY on a non-standard port
BAMBU_SEARCH_TARGET = "urn:bambulab-com:device:3dprinter:1"
_MAX_SWEEP_HOSTS = 1024  # /22


def parse_ssdp(message: str, ip: str) -> dict | None:
    """Bambu SSDP NOTIFY/response -> ``{host, serial, model_hint, name, source}``,
    or ``None`` for anything that isn't a Bambu printer."""
    if BAMBU_SEARCH_TARGET not in message and "bambulab" not in message.lower():
        return None
    usn = re.search(r"USN:\s*(?:uuid:)?([^\s\r\n]+)", message, re.IGNORECASE)
    if not usn:
        return None
    model = re.search(r"DevModel\.bambu\.com:\s*(.+?)(?:\r\n|\n|$)", message, re.IGNORECASE)
    name = re.search(r"DevName\.bambu\.com:\s*(.+?)(?:\r\n|\n|$)", message, re.IGNORECASE)
    if not model:
        model = re.search(r"NT:\s*urn:bambulab-com:device:([^:\s]+)", message, re.IGNORECASE)
    serial = usn.group(1).strip()
    return {
        "host": ip,
        "serial": serial,
        "model_hint": model.group(1).strip() if model else None,
        "name": name.group(1).strip() if name else serial,
        "source": "ssdp",
    }


def ssdp_listen(duration: float, *, port: int = SSDP_PORT, multicast: bool = True) -> list[dict]:
    """Listen ``duration`` seconds for Bambu NOTIFYs (and answer with an
    M-SEARCH). Returns ``[]`` when the port can't be bound (bridged
    container / port in use) -- the sweep covers that case."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    found: dict[str, dict] = {}
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with contextlib.suppress(AttributeError, OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", port))
        if multicast:
            mreq = struct.pack("4sl", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            msearch = (
                f"M-SEARCH * HTTP/1.1\r\nHOST: {SSDP_ADDR}:{port}\r\n"
                f'MAN: "ssdp:discover"\r\nMX: 3\r\nST: {BAMBU_SEARCH_TARGET}\r\n\r\n'
            )
            with contextlib.suppress(OSError):
                sock.sendto(msearch.encode(), (SSDP_ADDR, port))
        deadline = time.monotonic() + duration
        while (left := deadline - time.monotonic()) > 0:
            sock.settimeout(min(left, 0.5))
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                break
            item = parse_ssdp(data.decode("utf-8", errors="ignore"), addr[0])
            if item:
                found.setdefault(item["serial"], item)
    except OSError:
        return []
    finally:
        sock.close()
    return list(found.values())


def _model_hint(serial: str) -> str | None:
    from app.printers.capabilities import model_key

    return model_key(None, serial)


def _probe_host(host: str, port: int, timeout: float) -> dict | None:
    try:
        cn = read_cert_cn(host, port, timeout=timeout)
    except Exception:  # noqa: BLE001 -- closed port / non-TLS / no cert: not a printer
        return None
    if not cn:
        return None
    return {"host": host, "serial": cn, "model_hint": _model_hint(cn), "source": "sweep"}


def local_cidr() -> str | None:
    """This host's /24 (via the default-route source address), or None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 1))  # TEST-NET; no packet is sent
            ip = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None
    return str(ipaddress.ip_network(f"{ip}/24", strict=False))


def sweep(
    cidr: str,
    *,
    port: int = 8883,
    timeout: float = 1.5,
    concurrency: int = 64,
    budget: float = 10.0,
) -> list[dict]:
    """Bounded-concurrency TCP sweep: hosts answering TLS on ``port`` with a
    certificate CN are reported as printers. Stops at ``budget`` seconds."""
    net = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(h) for h in itertools.islice(net.hosts(), _MAX_SWEEP_HOSTS)]
    if not hosts:  # /32 has no .hosts() in older semantics; treat as single
        hosts = [str(ipaddress.ip_network(cidr, strict=False).network_address)]
    found: list[dict] = []
    pool = ThreadPoolExecutor(max_workers=concurrency)
    try:
        futures = [pool.submit(_probe_host, h, port, timeout) for h in hosts]
        try:
            for fut in as_completed(futures, timeout=budget):
                if (item := fut.result()) is not None:
                    found.append(item)
        except TimeoutError:
            pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return found


def discover(cidr: str | None = None, *, budget: float = 10.0, port: int = 8883) -> list[dict]:
    """SSDP listen + /24 sweep in parallel, merged by serial (SSDP entries,
    which carry model/name, win). Returns ``[{host, serial, model_hint, source}]``."""
    target = cidr or local_cidr()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ssdp_f = pool.submit(ssdp_listen, budget)
        sweep_f = pool.submit(sweep, target, port=port, budget=budget) if target else None
        results = ssdp_f.result()
        swept = sweep_f.result() if sweep_f else []
    merged: dict[str, dict] = {}
    for item in [*swept, *results]:  # later (ssdp) overrides earlier (sweep)
        merged[item["serial"]] = {
            "host": item["host"],
            "serial": item["serial"],
            "model_hint": item.get("model_hint"),
            "source": item["source"],
        }
    return list(merged.values())
