"""Mosquitto testcontainer fixture for the HA bridge integration suite
(P8 plan Task 3). A REAL broker -- never a fake/mock MQTT server (same
"real infra for integration tests" rule as the Postgres/Redis/MinIO/Samba
fixtures in ``conftest.py``/``storage_containers.py``).

Session-scoped, with a FIXED host port (picked once, before the container
starts) rather than testcontainers' usual ephemeral mapping -- so a broker
restart (``test_reconnect_after_broker_restart``) comes back up on the exact
same host:port the bridge is already configured to reconnect to.

Registered as a pytest plugin from ``tests/conftest.py``.
"""

from __future__ import annotations

import contextlib
import socket
from collections.abc import Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

MQTT_IMAGE = "eclipse-mosquitto:2.0.20"
MQTT_CONTAINER_PORT = 1883


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mosquitto() -> Iterator[DockerContainer]:
    """One real ``eclipse-mosquitto`` container for the whole test session,
    with no-auth config so the bridge/probe clients connect anonymously."""
    host_port = _free_port()
    container = (
        DockerContainer(MQTT_IMAGE)
        .with_command("mosquitto -c /mosquitto-no-auth.conf")
        .with_bind_ports(MQTT_CONTAINER_PORT, host_port)
        .waiting_for(LogMessageWaitStrategy("running").with_startup_timeout(60))
    )
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
def mqtt_url(mosquitto: DockerContainer) -> str:
    host = mosquitto.get_container_host_ip()
    port = mosquitto.get_exposed_port(MQTT_CONTAINER_PORT)
    return f"mqtt://{host}:{port}"


class MqttProbe:
    """Subscribes to a topic filter and collects every retained/live message
    received, decoded as UTF-8 text (raw ``bytes`` kept too for a camera/
    binary payload). ``wait_for_count``/``wait_for_topic`` poll with a
    timeout rather than blocking forever on a broker that never sends."""

    def __init__(self, client) -> None:
        self.client = client
        self.messages: list[tuple[str, bytes, bool]] = []  # (topic, payload, retain)

    def wait_for_count(self, n: int, timeout: float = 10.0) -> bool:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.messages) >= n:
                return True
            time.sleep(0.05)
        return len(self.messages) >= n

    def wait_for(self, predicate, timeout: float = 10.0) -> tuple[str, bytes, bool] | None:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in self.messages:
                if predicate(msg):
                    return msg
            time.sleep(0.05)
        return None

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


@pytest.fixture
def mqtt_probe(mqtt_url: str):
    """Factory: ``mqtt_probe(topic_filter)`` connects a fresh raw paho client
    subscribed to ``topic_filter``, collecting every message it receives."""
    from urllib.parse import urlsplit

    import paho.mqtt.client as mqtt

    probes: list[MqttProbe] = []

    def _make(
        topic_filter: str = "tdmm/#", *, username: str | None = None, password: str | None = None
    ):
        parsed = urlsplit(mqtt_url)
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        if username is not None:
            client.username_pw_set(username, password)
        probe = MqttProbe(client)

        def _on_message(c, userdata, msg):  # noqa: ANN001 -- paho callback signature
            probe.messages.append((msg.topic, msg.payload, bool(msg.retain)))

        def _on_connect(c, userdata, flags, rc, properties=None):  # noqa: ANN001
            if rc == 0:
                c.subscribe(topic_filter)

        client.on_message = _on_message
        client.on_connect = _on_connect
        client.connect(parsed.hostname, parsed.port, keepalive=60)
        client.loop_start()
        probes.append(probe)
        return probe

    yield _make
    for probe in probes:
        with contextlib.suppress(Exception):
            probe.close()
