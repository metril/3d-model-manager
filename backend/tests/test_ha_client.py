"""`app.services.ha_client.HaClient` (P8 Task 4): the thin REST client for
Home Assistant's `/api/` surface, mocked via respx. Never a live instance.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from app.services.ha_client import HaClient, HaUnreachable

BASE_URL = "http://homeassistant.local:8123"
TOKEN = "super-secret-long-lived-token"


@respx.mock
def test_ping_returns_message():
    respx.get(f"{BASE_URL}/api/").mock(
        return_value=httpx.Response(200, json={"message": "API running."})
    )
    client = HaClient(BASE_URL, TOKEN)
    assert client.ping() == "API running."


@respx.mock
def test_ping_sends_bearer_token():
    route = respx.get(f"{BASE_URL}/api/").mock(
        return_value=httpx.Response(200, json={"message": "API running."})
    )
    HaClient(BASE_URL, TOKEN).ping()
    assert route.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"


@respx.mock
def test_state_returns_entity_state():
    respx.get(f"{BASE_URL}/api/states/sensor.printer_plug_energy").mock(
        return_value=httpx.Response(200, json={"state": "10.4", "attributes": {}})
    )
    client = HaClient(BASE_URL, TOKEN)
    assert client.state("sensor.printer_plug_energy") == {"state": "10.4", "attributes": {}}


@respx.mock
def test_state_404_returns_none():
    respx.get(f"{BASE_URL}/api/states/sensor.unknown").mock(return_value=httpx.Response(404))
    client = HaClient(BASE_URL, TOKEN)
    assert client.state("sensor.unknown") is None


@respx.mock
def test_state_401_raises_unreachable():
    respx.get(f"{BASE_URL}/api/states/sensor.door").mock(return_value=httpx.Response(401))
    client = HaClient(BASE_URL, TOKEN)
    with pytest.raises(HaUnreachable):
        client.state("sensor.door")


@respx.mock
def test_state_5xx_raises_unreachable():
    respx.get(f"{BASE_URL}/api/states/sensor.door").mock(return_value=httpx.Response(503))
    client = HaClient(BASE_URL, TOKEN)
    with pytest.raises(HaUnreachable):
        client.state("sensor.door")


@respx.mock
def test_state_transport_error_raises_unreachable():
    respx.get(f"{BASE_URL}/api/states/sensor.door").mock(side_effect=httpx.ConnectError("refused"))
    client = HaClient(BASE_URL, TOKEN)
    with pytest.raises(HaUnreachable):
        client.state("sensor.door")


@respx.mock
def test_ha_token_never_logged(caplog: pytest.LogCaptureFixture):
    respx.get(f"{BASE_URL}/api/states/sensor.door").mock(return_value=httpx.Response(500))
    client = HaClient(BASE_URL, TOKEN)
    with caplog.at_level(logging.DEBUG), pytest.raises(HaUnreachable) as excinfo:
        client.state("sensor.door")
    assert TOKEN not in str(excinfo.value)
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
