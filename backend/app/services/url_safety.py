"""SSRF guard shared by the importers and admin "test connection" probes.

``is_safe_url`` resolves the host and rejects loopback, link-local (cloud
metadata at 169.254.169.254), unspecified, multicast, reserved and CGNAT
addresses. RFC1918/ULA private ranges are rejected unless ``allow_private``
(admin-configured LAN services such as Home Assistant or Obico legitimately
live there; importer redirects never should). DNS rebinding between this
check and the connect is not fully closed, but every redirect hop re-checks.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_EC2_V6_METADATA = ipaddress.ip_address("fd00:ec2::254")


def _ip_blocked(ip: ipaddress._BaseAddress, allow_private: bool) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
        or ip == _EC2_V6_METADATA
    ):
        return True
    if allow_private:
        return False
    return ip.is_private or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT)


def is_safe_url(
    url: str,
    *,
    allow_private: bool = False,
    https_only: bool = False,
    unresolvable_ok: bool = False,
) -> bool:
    """True when ``url`` is http(s) (https only if asked) and every address
    its host resolves to is allowed."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in (("https",) if https_only else ("http", "https")) or not host:
        return False
    try:
        infos = socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError):
        return unresolvable_ok  # nothing to connect to; the probe itself will fail
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            return False
        if _ip_blocked(ip, allow_private):
            return False
    return True


def ssrf_request_hook(request: httpx.Request) -> None:
    """httpx request event hook: runs on the first request and every redirect
    hop. Importers: https only, no private ranges."""
    if not is_safe_url(str(request.url), https_only=True):
        raise httpx.RequestError("blocked: url resolves to a disallowed address", request=request)
