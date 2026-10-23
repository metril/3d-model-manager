"""The staged Developer-Mode probe (Round 8 T1/T2 hardening): TCP
reachability -> TLS cert serial cross-match -> raw MQTT auth + status
report, each stage returning early with a SPECIFIC, actionable
``ProbeResult.detail`` instead of a bare exception string bubbling out of
the old lib-backed ``test_connection`` (a black-box "connected but no state"
covered "wrong access code", "wrong serial", "LAN mode off", and "not a
Bambu printer at all" all under one unhelpful message).

Stages are small, individually-stubbable helpers (``_tcp_check``/
``_cert_check``/``_mqtt_probe``) driven by ``staged_probe`` -- tests stub the
earlier stages to drive a single stage in isolation (table-driven in
``tests/test_bambu_probe.py``).

Import-safety: ``app.printers.discovery`` (stdlib + the eager
``cryptography`` dep) is safe to import at module level; ``paho.mqtt.client``
is lazy-imported INSIDE ``_mqtt_probe`` only, so this module stays
importable with the printer feature OFF (see
``tests/test_flag_off_imports.py``).
"""

from __future__ import annotations

import contextlib
import json
import socket
import ssl
import threading
import time

from app.printers.base import PrinterConnection, ProbeResult
from app.printers.discovery import read_cert_cn

_PORT = 8883
_MQTT_USERNAME = "bblp"


def _tcp_check(host: str, *, port: int = _PORT, timeout: float = 4.0) -> ProbeResult | None:
    """Stage 1: can we even open the MQTT port? Returns ``None`` to continue
    to the next stage, or a failed ``ProbeResult`` with a specific detail."""
    try:
        socket.create_connection((host, port), timeout=timeout).close()
    except ConnectionRefusedError:
        return ProbeResult(
            ok=False,
            detail=(
                f"Port {port} is closed on {host}. On the printer, turn on LAN Mode and "
                "Developer Mode (Settings → Network), or check the IP."
            ),
        )
    except OSError:
        return ProbeResult(
            ok=False,
            detail=(
                f"Can't reach {host}:{port}. Check the printer is on the "
                "network and the IP is right."
            ),
        )
    return None


def _cert_check(
    host: str, serial: str, *, port: int = _PORT, timeout: float = 4.0
) -> ProbeResult | None:
    """Stage 2: read the TLS cert's subject CN and cross-match it against
    the printer's configured serial -- catches "right IP, wrong printer" and
    "typo'd the serial" before we ever try to authenticate."""
    try:
        cn = read_cert_cn(host, port, timeout=timeout)
    except Exception:  # noqa: BLE001 -- any read/handshake/parse failure means "can't read the cert"
        return ProbeResult(
            ok=False,
            detail=(
                f"Reached {host} but couldn't read its TLS certificate -- "
                "is this a Bambu printer on the LAN?"
            ),
        )
    if cn and cn != serial:
        return ProbeResult(
            ok=False,
            detail=(
                f"Serial mismatch: {host} reports {cn}, but you entered "
                f"{serial}. Use Detect to fix it."
            ),
        )
    return None


def _mqtt_probe(conn: PrinterConnection, *, timeout: float) -> ProbeResult:
    """Stages 3+4: raw MQTT connect (``bblp`` + the access code) then, once
    authenticated, subscribe the serial-scoped report topic, request a full
    status push, and wait for a report carrying ``print.gcode_state``.

    Uses the VERSION1 callback API deliberately -- against this printer's
    MQTT v3.1.1 broker it hands ``on_connect`` the raw CONNACK byte (a plain
    int: 0 / 5 / ...), matching the rc values verified live against a real
    A1 Mini. VERSION2 would instead wrap it in a ``ReasonCode`` whose
    ``.value`` is the unrelated MQTT v5 reason-code numbering
    (``convert_connack_rc_to_reason_code`` in paho's own client.py) -- not
    what "rc==5" below means.

    NEVER subscribes a wildcard topic -- confirmed live that gets the client
    force-disconnected (rc=7) by this printer's broker; only the
    serial-scoped ``device/{serial}/report`` is ever subscribed.
    """
    import paho.mqtt.client as mqtt

    deadline = time.monotonic() + timeout
    host, serial = conn.host, conn.serial
    report_topic = f"device/{serial}/report"
    request_topic = f"device/{serial}/request"

    outcome: dict[str, object] = {"rc": None, "gcode_state": None}
    connected = threading.Event()
    got_report = threading.Event()

    def _on_connect(client, userdata, flags, rc):  # noqa: ANN001 -- paho callback signature
        outcome["rc"] = rc
        if rc == 0:
            client.subscribe(report_topic)
            client.publish(
                request_topic,
                json.dumps({"pushing": {"sequence_id": "1", "command": "pushall"}}),
            )
        connected.set()

    def _on_message(client, userdata, msg):  # noqa: ANN001 -- paho callback signature
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            # M4 fix-review: valid JSON that isn't an object (a bare
            # array/number/string/null) -- `.get` below would raise
            # `AttributeError`, which paho's network-loop thread otherwise
            # silently swallows (the probe just misses this report and
            # times out with "sent no status" instead of failing fast on a
            # clear signal). A real Bambu broker always sends objects; this
            # is just an explicit guard rather than an implicit crash.
            return
        state = (payload.get("print") or {}).get("gcode_state")
        if state:
            outcome["gcode_state"] = state
            got_report.set()

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
    client.username_pw_set(_MQTT_USERNAME, conn.access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = _on_connect
    client.on_message = _on_message

    try:
        client.connect(host, _PORT, keepalive=60)
        client.loop_start()
        if not connected.wait(timeout):
            return ProbeResult(
                ok=False,
                detail=(
                    f"Can't reach {host}:{_PORT}. Check the printer is on the "
                    "network and the IP is right."
                ),
                stage="connect",
            )
        rc = outcome["rc"]
        if rc != 0:
            if rc == 5:
                return ProbeResult(
                    ok=False,
                    detail=(
                        f"Access code rejected by {host}. Re-copy the LAN access code from the "
                        "printer (Settings → Network)."
                    ),
                    stage="auth",
                )
            return ProbeResult(
                ok=False,
                detail=f"The printer refused the MQTT connection (code {rc}).",
                stage="auth",
            )
        remaining = max(deadline - time.monotonic(), 0.0)
        if not got_report.wait(remaining):
            return ProbeResult(
                ok=False,
                detail=(
                    "Connected and authenticated, but the printer sent no status. Confirm "
                    "Developer Mode is on and the serial is exact."
                ),
                stage="status",
            )
        state = str(outcome["gcode_state"])
        return ProbeResult(
            ok=True, detail=f"Connected. Printer state: {state}.", gcode_state=state, stage="ok"
        )
    finally:
        with contextlib.suppress(Exception):
            client.loop_stop()
        with contextlib.suppress(Exception):
            client.disconnect()


def staged_probe(conn: PrinterConnection, *, timeout: float = 10.0) -> ProbeResult:
    """Run the full staged probe against ``conn``. Each stage gets its own
    fixed budget (TCP/cert stages default to a cheap 4s each); whatever's
    left of ``timeout`` after those two goes to the MQTT stage's wait for a
    status report, floored at 1s so a tiny overall ``timeout`` can't starve
    the MQTT stage before it even connects."""
    deadline = time.monotonic() + timeout
    result = _tcp_check(conn.host)
    if result is not None:
        return result
    result = _cert_check(conn.host, conn.serial)
    if result is not None:
        return result
    remaining = max(deadline - time.monotonic(), 1.0)
    return _mqtt_probe(conn, timeout=remaining)


# -- Q5 structured connection diagnostic ------------------------------------
# Same stages as ``staged_probe`` but every check reports ok|fail|skip + a
# hint, so the UI can render a checklist instead of one error string.

_PORTS = {
    8883: ("mqtt", "MQTT. Turn on LAN Mode + Developer Mode on the printer."),
    990: ("ftps", "FTPS (file upload). Same LAN Mode requirement; check firewalls."),
    6000: ("camera_a1", "Camera stream (A1/P1 models)."),
    322: ("camera_rtsp", "RTSP camera (X1/H2 models); enable LAN-mode liveview."),
}


def _tcp_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
    except OSError:
        return False
    return True


def _in_container() -> bool:
    import os

    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def _source_ip(host: str) -> str | None:
    """Local IPv4 the OS would send from toward ``host`` (no packets sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((host, 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def _check(cid: str, status: str, hint: str, detail: str | None = None) -> dict:
    return {"id": cid, "status": status, "hint": hint, "detail": detail}


def diagnose(
    host: str,
    serial: str,
    access_code: str,
    *,
    model: str | None = None,
    state: dict | None = None,
    timeout: float = 10.0,
) -> dict:
    """Run every connection check and return ``{ok, checks: [...]}``."""
    import ipaddress
    from concurrent.futures import ThreadPoolExecutor

    from app.printers.capabilities import capabilities_for

    checks: list[dict] = []
    cam = capabilities_for(model, serial).camera if (model or serial) else "none"
    with ThreadPoolExecutor(max_workers=len(_PORTS)) as pool:
        opened = dict(zip(_PORTS, pool.map(lambda p: _tcp_open(host, p), _PORTS), strict=True))
    for port, (name, hint) in _PORTS.items():
        cid = f"tcp_{port}"
        if (port == 6000 and cam == "rtsp") or (port == 322 and cam == "a1"):
            checks.append(_check(cid, "skip", f"Not used by this model ({name})."))
        elif opened[port]:
            checks.append(_check(cid, "ok", f"Port {port} reachable."))
        else:
            checks.append(_check(cid, "fail", f"Port {port} unreachable -- {hint}"))

    # container networking + subnet
    src = _source_ip(host)
    in_ct = _in_container()
    if in_ct and src and ipaddress.ip_address(src) in ipaddress.ip_network("172.16.0.0/12"):
        checks.append(
            _check(
                "network_mode",
                "fail",
                "Running in a bridged container: TCP works but SSDP discovery doesn't. "
                "Use host networking or the subnet sweep.",
                "bridge",
            )
        )
        checks.append(_check("subnet", "skip", "Container NAT hides the LAN subnet."))
    else:
        checks.append(
            _check(
                "network_mode",
                "ok",
                "Host networking." if in_ct else "Not running in a container.",
                "host" if in_ct else "native",
            )
        )
        try:
            same = src is not None and ipaddress.ip_address(host) in ipaddress.ip_network(
                f"{src}/24", strict=False
            )
        except ValueError:
            same = None
        if same is None:
            checks.append(_check("subnet", "skip", "Couldn't determine the local subnet."))
        elif same:
            checks.append(_check("subnet", "ok", "Printer is on the same /24 as this server."))
        else:
            checks.append(
                _check(
                    "subnet",
                    "fail",
                    f"Printer {host} is on a different subnet from this server ({src}); "
                    "broadcast discovery won't see it.",
                )
            )

    # TLS serial / MQTT auth / dev mode (only meaningful with 8883 open)
    if not opened[8883]:
        for cid, what in (("tls_serial", "TLS serial"), ("mqtt_auth", "MQTT auth")):
            checks.append(_check(cid, "skip", f"{what} skipped: port 8883 unreachable."))
    else:
        cert = _cert_check(host, serial)
        checks.append(
            _check("tls_serial", "fail", cert.detail)
            if cert
            else _check("tls_serial", "ok", "Certificate serial matches.")
        )
        conn = PrinterConnection(host=host, serial=serial, access_code=access_code, model=model)
        res = _mqtt_probe(conn, timeout=max(timeout, 1.0))
        if res.ok or res.stage == "status":
            checks.append(_check("mqtt_auth", "ok", "Access code accepted."))
        else:
            checks.append(_check("mqtt_auth", "fail", res.detail))
    dev = (state or {}).get("dev_mode")
    if dev is None:
        checks.append(_check("dev_mode", "skip", "Developer Mode state not reported yet."))
    elif dev:
        checks.append(_check("dev_mode", "ok", "Developer Mode is on."))
    else:
        checks.append(
            _check(
                "dev_mode", "fail", (state or {}).get("dev_mode_reason") or "Developer Mode is off."
            )
        )
    advisory = {"tcp_6000", "tcp_322", "network_mode"}  # informational, don't fail the run
    ok = all(c["status"] != "fail" for c in checks if c["id"] not in advisory)
    return {"ok": ok, "checks": checks}
