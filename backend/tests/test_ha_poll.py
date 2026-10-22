"""The P8 Home Assistant plan's REST poll beat (Task 4): `evaluate`/
`counter_delta` (pure), `poll_once` (orchestration, respx-mocked HA), and
the `app.tasks.home_assistant.poll_ha` beat entry's registration/routing.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import redis as redis_lib
import respx

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models.enums import HaRole, PrinterKind
from app.models.home_assistant import HaLink
from app.models.printing import Printer
from app.services.ha_config import HaConfig
from app.services.ha_state import (
    STATUS_KEY,
    alert_key,
    energy_acc,
    energy_acc_key,
    energy_key,
    read_alert,
)
from app.services.home_assistant import counter_delta, evaluate, poll_once
from app.tasks import base

BASE_URL = "http://homeassistant.local:8123"
TOKEN = "super-secret-long-lived-token"


def _cfg(**overrides) -> HaConfig:
    fields = dict(
        ha_mqtt_url=None,
        ha_mqtt_user=None,
        ha_mqtt_password=None,
        ha_url=BASE_URL,
        ha_token=TOKEN,
    )
    fields.update(overrides)
    return HaConfig(**fields)


def _seed_printer(session, *, name="P1") -> Printer:
    p = Printer(
        name=name,
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.1",
        serial=name,
        access_code_enc=encrypt_secret(get_settings(), "12345678"),
        enabled=True,
    )
    session.add(p)
    session.commit()
    return p


def _state_route(entity_id: str, state: str):
    return respx.get(f"{BASE_URL}/api/states/{entity_id}").mock(
        return_value=httpx.Response(200, json={"state": state, "attributes": {}})
    )


# ---------------------------------------------------------------------------
# evaluate -- pure condition matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cond,state,expected",
    [
        ({"op": "eq", "value": "on"}, "on", True),
        ({"op": "eq", "value": "on"}, "off", False),
        ({"op": "ne", "value": "on"}, "off", True),
        ({"op": "ne", "value": "on"}, "on", False),
        ({"op": "gt", "value": 30}, "35.2", True),
        ({"op": "gt", "value": 30}, "20.0", False),
        ({"op": "lt", "value": 10}, "5.0", True),
        ({"op": "lt", "value": 10}, "15.0", False),
        ({"op": "gte", "value": 30}, "30.0", True),
        ({"op": "gte", "value": 30}, "29.9", False),
        ({"op": "lte", "value": 10}, "10.0", True),
        ({"op": "lte", "value": 10}, "10.1", False),
        ({"op": "in", "value": ["on", "open"]}, "open", True),
        ({"op": "in", "value": ["on", "open"]}, "off", False),
        ({"op": "eq", "value": "on"}, "unavailable", False),
        ({"op": "eq", "value": "on"}, "unknown", False),
        ({"op": "eq", "value": "on"}, None, False),
        (None, "on", False),
        ({"op": "gt", "value": 30}, "not-a-number", False),
    ],
)
def test_evaluate_matrix(cond, state, expected):
    assert evaluate(cond, state) is expected


# ---------------------------------------------------------------------------
# counter_delta -- pure counter-reset math
# ---------------------------------------------------------------------------


def test_counter_reset_delta():
    samples = [10.0, 10.4, 0.1, 0.3]
    prev = None
    total = 0.0
    for cur in samples:
        total += counter_delta(prev, cur)
        prev = cur
    # 0.0 (baseline) + 0.4 (10.0->10.4) + 0.1 (reset: 10.4->0.1) + 0.2 (0.1->0.3)
    assert total == pytest.approx(0.7)


def test_counter_delta_first_sample_is_baseline_only():
    assert counter_delta(None, 10.0) == 0.0


def test_counter_delta_reset_takes_new_reading_whole():
    assert counter_delta(10.4, 0.1) == 0.1


def test_counter_delta_jitter_dip_adds_zero():
    """A small dip (sensor jitter around a flat reading) is NOT a reset --
    it must add 0, not the (negative, nonsensical) difference."""
    assert counter_delta(10.0, 9.95) == 0.0


def test_counter_delta_real_reset_adds_whole_reading():
    """A drop under 90% of the previous reading IS a real reset."""
    assert counter_delta(10.0, 8.0) == 8.0


# ---------------------------------------------------------------------------
# poll_once -- orchestration
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("redis_url")
def test_noop_without_ha_url_or_token(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        result = poll_once(s, r, _cfg(ha_url=None))
    assert result == {"ok": True, "skipped": True}


@pytest.mark.usefixtures("redis_url")
def test_noop_with_no_links(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        result = poll_once(s, r, _cfg())
    assert result == {"ok": True, "skipped": True}


@respx.mock
def test_alert_written_with_reason_and_ttl(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    _state_route("binary_sensor.door", "on")
    with base.sync_session() as s:
        printer = _seed_printer(s)
        s.add(
            HaLink(
                printer_id=printer.id,
                entity_id="binary_sensor.door",
                role=HaRole.DOOR,
                alert_condition={"op": "eq", "value": "on"},
                hold_queue=True,
            )
        )
        s.commit()
        poll_once(s, r, _cfg())

    alert = read_alert(r, printer.id)
    assert alert == {
        "reason": "Waiting on binary_sensor.door",
        "entities": ["binary_sensor.door"],
        "hold": True,
    }
    assert r.ttl(alert_key(printer.id)) <= 60
    assert r.ttl(alert_key(printer.id)) > 0


@respx.mock
def test_null_printer_link_applies_to_all(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    _state_route("binary_sensor.shared_door", "on")
    with base.sync_session() as s:
        p1 = _seed_printer(s, name="P1")
        p2 = _seed_printer(s, name="P2")
        s.add(
            HaLink(
                printer_id=None,
                entity_id="binary_sensor.shared_door",
                role=HaRole.DOOR,
                alert_condition={"op": "eq", "value": "on"},
                hold_queue=False,
            )
        )
        s.commit()
        poll_once(s, r, _cfg())

    for pid in (p1.id, p2.id):
        alert = read_alert(r, pid)
        assert alert is not None
        assert alert["entities"] == ["binary_sensor.shared_door"]
        assert alert["hold"] is False


@respx.mock
def test_clear_deletes_key(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    route = _state_route("binary_sensor.door", "on")
    with base.sync_session() as s:
        printer = _seed_printer(s)
        s.add(
            HaLink(
                printer_id=printer.id,
                entity_id="binary_sensor.door",
                role=HaRole.DOOR,
                alert_condition={"op": "eq", "value": "on"},
                hold_queue=True,
            )
        )
        s.commit()
        poll_once(s, r, _cfg())
        assert read_alert(r, printer.id) is not None

        route.respond(200, json={"state": "off", "attributes": {}})
        poll_once(s, r, _cfg())

    assert read_alert(r, printer.id) is None


@respx.mock
def test_unreachable_fails_open(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        printer = _seed_printer(s)
        s.add(
            HaLink(
                printer_id=printer.id,
                entity_id="binary_sensor.door",
                role=HaRole.DOOR,
                alert_condition={"op": "eq", "value": "on"},
                hold_queue=True,
            )
        )
        s.commit()
        r.set(alert_key(printer.id), json.dumps({"reason": "stale", "entities": [], "hold": True}))

        respx.get(f"{BASE_URL}/api/states/binary_sensor.door").mock(
            return_value=httpx.Response(503)
        )
        result = poll_once(s, r, _cfg())

    assert result["ok"] is False
    assert read_alert(r, printer.id) is None
    status = json.loads(r.get(STATUS_KEY))
    assert status["ok"] is False


@respx.mock
def test_404_entity_reported(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    respx.get(f"{BASE_URL}/api/states/binary_sensor.missing").mock(return_value=httpx.Response(404))
    with base.sync_session() as s:
        printer = _seed_printer(s)
        s.add(
            HaLink(
                printer_id=printer.id,
                entity_id="binary_sensor.missing",
                role=HaRole.DOOR,
                alert_condition={"op": "eq", "value": "on"},
                hold_queue=False,
            )
        )
        s.commit()
        poll_once(s, r, _cfg())

    status = json.loads(r.get(STATUS_KEY))
    assert status["ok"] is True
    assert status["errors"] == ["binary_sensor.missing"]
    assert "checked_at" in status
    assert r.ttl(STATUS_KEY) <= 120
    assert r.ttl(STATUS_KEY) > 0


@respx.mock
def test_energy_counter_reset_tracked_across_polls(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    route = _state_route("sensor.plug_energy", "10.0")
    with base.sync_session() as s:
        printer = _seed_printer(s)
        s.add(
            HaLink(
                printer_id=printer.id,
                entity_id="sensor.plug_energy",
                role=HaRole.ENERGY_KWH,
            )
        )
        s.commit()
        poll_once(s, r, _cfg())  # baseline

        route.respond(200, json={"state": "10.4", "attributes": {}})
        poll_once(s, r, _cfg())

        route.respond(200, json={"state": "0.1", "attributes": {}})  # reset
        poll_once(s, r, _cfg())

    assert energy_acc(r, printer.id) == pytest.approx(0.5)
    assert float(r.get(energy_key(printer.id))) == pytest.approx(0.5)


@respx.mock
def test_energy_update_is_atomic_across_overlapping_polls(redis_url: str):
    """Final review I7: the raw-prev read + accumulate must be one atomic
    Redis operation (a Lua script), not a GET-then-SET race -- otherwise
    two overlapping ticks (the Celery lock normally prevents this, but
    `poll_once` itself must still be safe) could both read the same `prev`
    and each add their own delta on top of it, double-counting."""
    r = redis_lib.Redis.from_url(redis_url)
    route = _state_route("sensor.plug_energy", "10.0")
    with base.sync_session() as s:
        printer = _seed_printer(s)
        link = HaLink(
            printer_id=printer.id,
            entity_id="sensor.plug_energy",
            role=HaRole.ENERGY_KWH,
        )
        s.add(link)
        s.commit()
        # redis_url is session-scoped (ids restart per test on the Postgres
        # side) -- clear any stale energy keys an earlier test left behind
        # under the same printer/link id before asserting an absolute total.
        for key in (
            energy_key(printer.id),
            energy_acc_key(printer.id),
            f"ha:energy:raw:{link.id}",
        ):
            r.delete(key)
        poll_once(s, r, _cfg())  # baseline: 10.0

        route.respond(200, json={"state": "11.0", "attributes": {}})
        # Two "overlapping" ticks reading the same new state -- each must
        # see the OTHER's raw-key write, not stale data, so the total
        # delta is 1.0 once, not 1.0 counted twice.
        poll_once(s, r, _cfg())
        poll_once(s, r, _cfg())

    assert energy_acc(r, printer.id) == pytest.approx(1.0)


@respx.mock
def test_energy_null_printer_link_ignored(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    _state_route("sensor.unlinked_energy", "10.0")
    with base.sync_session() as s:
        s.add(HaLink(printer_id=None, entity_id="sensor.unlinked_energy", role=HaRole.ENERGY_KWH))
        s.commit()
        result = poll_once(s, r, _cfg())

    assert result["ok"] is True


@respx.mock
def test_ha_token_never_logged(redis_url: str, caplog: pytest.LogCaptureFixture):
    r = redis_lib.Redis.from_url(redis_url)
    respx.get(f"{BASE_URL}/api/states/binary_sensor.door").mock(return_value=httpx.Response(500))
    with base.sync_session() as s:
        printer = _seed_printer(s)
        s.add(
            HaLink(
                printer_id=printer.id,
                entity_id="binary_sensor.door",
                role=HaRole.DOOR,
                alert_condition={"op": "eq", "value": "on"},
                hold_queue=False,
            )
        )
        s.commit()
        with caplog.at_level(logging.DEBUG):
            poll_once(s, r, _cfg())

    for record in caplog.records:
        assert TOKEN not in record.getMessage()


# ---------------------------------------------------------------------------
# beat registration
# ---------------------------------------------------------------------------


def test_beat_entry_routes_io():
    from app.tasks import home_assistant as ha_task
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["ha-poll"]
    assert entry["task"] == "app.tasks.home_assistant.poll_ha"
    assert entry["schedule"] == 15.0
    route = celery_app.amqp.router.route({}, "app.tasks.home_assistant.poll_ha")
    assert route["queue"].name == "io"
    assert ha_task.poll_ha  # importable, registered
    assert ha_task.poll_ha.soft_time_limit == 12
    assert ha_task.poll_ha.time_limit == 14
