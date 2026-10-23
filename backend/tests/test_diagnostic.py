"""Q5 connection diagnostic: per-check ok|fail|skip + hints, and the routes."""

from __future__ import annotations

import pytest

from app.printers import probe
from app.printers.base import ProbeResult


@pytest.fixture
def stub(monkeypatch):
    """All four ports open, cert matches, MQTT ok, native host on the same /24."""
    monkeypatch.setattr(probe, "_tcp_open", lambda host, port, timeout=3.0: True)
    monkeypatch.setattr(probe, "_cert_check", lambda host, serial, **kw: None)
    monkeypatch.setattr(
        probe, "_mqtt_probe", lambda conn, timeout: ProbeResult(True, "ok", "IDLE", "ok")
    )
    monkeypatch.setattr(probe, "_in_container", lambda: False)
    monkeypatch.setattr(probe, "_source_ip", lambda host: "192.168.1.10")


def _by_id(report):
    return {c["id"]: c for c in report["checks"]}


def test_all_good(stub):
    r = probe.diagnose("192.168.1.50", "S", "c", model="A1 Mini", state={"dev_mode": True})
    c = _by_id(r)
    assert r["ok"] and c["tcp_8883"]["status"] == "ok"
    assert c["tcp_322"]["status"] == "skip"  # A1 uses 6000, not RTSP
    assert c["subnet"]["status"] == "ok" and c["dev_mode"]["status"] == "ok"
    assert all(x["status"] in {"ok", "fail", "skip"} and x["hint"] for x in r["checks"])


def test_ports_closed_skips_dependent_checks(stub, monkeypatch):
    monkeypatch.setattr(probe, "_tcp_open", lambda host, port, timeout=3.0: False)
    r = probe.diagnose("192.168.1.50", "S", "c", model="A1 Mini")
    c = _by_id(r)
    assert not r["ok"] and c["tcp_8883"]["status"] == "fail"
    assert c["tls_serial"]["status"] == "skip" and c["mqtt_auth"]["status"] == "skip"
    assert c["dev_mode"]["status"] == "skip"  # unknown, not guessed


def test_serial_mismatch_and_bad_code(stub, monkeypatch):
    monkeypatch.setattr(
        probe, "_cert_check", lambda host, serial, **kw: ProbeResult(False, "Serial mismatch")
    )
    monkeypatch.setattr(
        probe,
        "_mqtt_probe",
        lambda conn, timeout: ProbeResult(False, "Access code rejected", None, "auth"),
    )
    c = _by_id(probe.diagnose("192.168.1.50", "S", "c"))
    assert c["tls_serial"]["status"] == "fail" and "mismatch" in c["tls_serial"]["hint"]
    assert c["mqtt_auth"]["status"] == "fail"


def test_auth_ok_even_without_status(stub, monkeypatch):
    monkeypatch.setattr(
        probe, "_mqtt_probe", lambda conn, timeout: ProbeResult(False, "no status", None, "status")
    )
    r = probe.diagnose(
        "192.168.1.50", "S", "c", state={"dev_mode": False, "dev_mode_reason": "off"}
    )
    c = _by_id(r)
    assert c["mqtt_auth"]["status"] == "ok" and c["dev_mode"]["status"] == "fail"


def test_bridged_container_and_subnet_mismatch(stub, monkeypatch):
    monkeypatch.setattr(probe, "_in_container", lambda: True)
    monkeypatch.setattr(probe, "_source_ip", lambda host: "172.18.0.5")
    c = _by_id(probe.diagnose("192.168.1.50", "S", "c"))
    assert c["network_mode"]["status"] == "fail" and c["network_mode"]["detail"] == "bridge"
    assert c["subnet"]["status"] == "skip"
    monkeypatch.setattr(probe, "_in_container", lambda: False)
    monkeypatch.setattr(probe, "_source_ip", lambda host: "10.0.0.5")
    assert _by_id(probe.diagnose("192.168.1.50", "S", "c"))["subnet"]["status"] == "fail"


async def test_routes(authenticated_client, printer_enabled, stub):
    body = {"host": "192.168.1.50", "serial": "0309ABC", "access_code": "12345678"}
    r = await authenticated_client.post("/api/printers/diagnostic", json=body)
    assert r.status_code == 200 and r.json()["ok"] is True
    pid = (
        await authenticated_client.post(
            "/api/printers", json={**body, "name": "p", "model": "A1 Mini"}
        )
    ).json()["id"]
    r = await authenticated_client.get(f"/api/printers/{pid}/diagnostic")
    assert r.status_code == 200 and {c["id"] for c in r.json()["checks"]} >= {
        "tcp_8883",
        "mqtt_auth",
    }
    assert (await authenticated_client.get("/api/printers/9999/diagnostic")).status_code == 404


async def test_routes_need_settings_write(viewer_client, printer_enabled):
    body = {"host": "h", "serial": "S", "access_code": "c"}
    assert (await viewer_client.post("/api/printers/diagnostic", json=body)).status_code == 403
    assert (await viewer_client.get("/api/printers/1/diagnostic")).status_code == 403
