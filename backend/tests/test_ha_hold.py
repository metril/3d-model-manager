"""P8 Home Assistant plan Task 5: dispatcher hold (`dispatcher
.printer_hold_reason` -> `ha_state.hold_reason`) and edge-triggered
pause-on-alert (`app.services.home_assistant.poll_once`/`_handle_pause_edge`).
"""

from __future__ import annotations

import json

import httpx
import pytest
import redis as redis_lib
import respx

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models.enums import HaRole, PrinterKind
from app.models.home_assistant import HaLink
from app.models.library import PrintQueueEntry
from app.models.printing import Printer, PrintJob
from app.printers.base import command_channel, state_key
from app.services import dispatcher
from app.services.ha_config import HaConfig
from app.services.ha_state import alert_key, fired_key
from app.services.home_assistant import poll_once
from app.tasks import base
from tests.test_dispatcher import (
    IDLE_STATE,
    _seed_admin,
    _seed_entry,
    _seed_model_file,
    _seed_printer,
    _seed_state,
)

pytestmark = pytest.mark.usefixtures("data_dir", "printer_enabled", "library_root")


@pytest.fixture(autouse=True)
def _clean_ha_redis_keys(redis_url: str):
    """``redis_url`` is session-scoped (shared across every test module)
    while the Postgres schema is truncated-with-``RESTART IDENTITY`` per
    test -- printer/link ids get REUSED across tests. Any ``ha:*``/
    ``printer:*:state`` key this module sets without a TTL must not survive
    past its own test, or it'll wrongly hold/pause an unrelated printer
    that happens to land on the same id in a later test."""
    yield
    r = redis_lib.Redis.from_url(redis_url)
    try:
        for pattern in ("ha:*", "printer:*:state"):
            for key in r.scan_iter(pattern):
                r.delete(key)
    finally:
        r.close()


BASE_URL = "http://homeassistant.local:8123"
TOKEN = "super-secret-long-lived-token"

RUNNING_STATE = {**IDLE_STATE, "gcode_state": "RUNNING"}


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


def _state_route(entity_id: str, state: str):
    return respx.get(f"{BASE_URL}/api/states/{entity_id}").mock(
        return_value=httpx.Response(200, json={"state": state, "attributes": {}})
    )


def _seed_printer_raw(session, *, name: str) -> Printer:
    p = Printer(
        name=name,
        kind=PrinterKind.BAMBU_LAN,
        host=f"10.0.0.{len(name)}",
        serial=name,
        access_code_enc=encrypt_secret(get_settings(), "12345678"),
        enabled=True,
    )
    session.add(p)
    session.commit()
    return p


# ---------------------------------------------------------------------------
# dispatcher hold
# ---------------------------------------------------------------------------


def test_dispatcher_skips_held(redis_url: str, fake_adapter):
    """A ready item with no fixed printer, candidate printers A (held) and
    B (idle), goes to B -- A is never picked even though it's otherwise
    compatible/idle on the Redis state side."""
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="ha-hold-a")
        printer_a = _seed_printer(s, name="HoldA")
        printer_b = _seed_printer(s, name="HoldB")
        admin_id = _seed_admin(s)
        entry = _seed_entry(s, model=model, file=file, state="ready", created_by=admin_id)
        entry_id, pid_a, pid_b = entry.id, printer_a.id, printer_b.id
    _seed_state(redis_url, pid_a, IDLE_STATE)
    _seed_state(redis_url, pid_b, IDLE_STATE)

    r = redis_lib.Redis.from_url(redis_url)
    r.set(alert_key(pid_a), json.dumps({"reason": "Waiting on x", "entities": ["x"], "hold": True}))

    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, r, settings)
    r.close()

    assert counts["sent"] == 1
    with base.sync_session() as s:
        entry_after = s.get(PrintQueueEntry, entry_id)
        assert entry_after.state == "dispatched"
        job = s.get(PrintJob, entry_after.print_job_id)
        assert job.printer_id == pid_b


def test_dispatcher_held_with_only_held_printer(redis_url: str, fake_adapter):
    """A ready item pinned to a held printer, with no other candidate,
    dispatches nowhere and stays `ready`."""
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="ha-hold-a-only")
        printer_a = _seed_printer(s, name="HoldOnlyA")
        admin_id = _seed_admin(s)
        entry = _seed_entry(
            s, model=model, file=file, printer_id=printer_a.id, state="ready", created_by=admin_id
        )
        entry_id, pid_a = entry.id, printer_a.id
    _seed_state(redis_url, pid_a, IDLE_STATE)

    r = redis_lib.Redis.from_url(redis_url)
    r.set(alert_key(pid_a), json.dumps({"reason": "Waiting on x", "entities": ["x"], "hold": True}))

    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, r, settings)
    r.close()

    assert counts["sent"] == 0
    with base.sync_session() as s:
        assert s.get(PrintQueueEntry, entry_id).state == "ready"


def test_hold_false_alert_does_not_hold(redis_url: str, fake_adapter):
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="ha-hold-false")
        printer = _seed_printer(s, name="HoldFalseP")
        admin_id = _seed_admin(s)
        entry = _seed_entry(
            s,
            model=model,
            file=file,
            printer_id=printer.id,
            state="ready",
            created_by=admin_id,
        )
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)

    r = redis_lib.Redis.from_url(redis_url)
    r.set(alert_key(pid), json.dumps({"reason": "badge only", "entities": ["x"], "hold": False}))

    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, r, settings)
    r.close()

    assert counts["sent"] == 1
    with base.sync_session() as s:
        assert s.get(PrintQueueEntry, entry_id).state == "dispatched"


def test_alert_ttl_fails_open(redis_url: str, fake_adapter):
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="ha-hold-ttl")
        printer = _seed_printer(s, name="HoldTtlP")
        admin_id = _seed_admin(s)
        entry = _seed_entry(
            s,
            model=model,
            file=file,
            printer_id=printer.id,
            state="ready",
            created_by=admin_id,
        )
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)

    r = redis_lib.Redis.from_url(redis_url)
    # The alert key simply isn't present -- same observable state as an
    # expired key -- a dead poller's hold fails open.

    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, r, settings)
    r.close()

    assert counts["sent"] == 1
    with base.sync_session() as s:
        assert s.get(PrintQueueEntry, entry_id).state == "dispatched"


# ---------------------------------------------------------------------------
# pause-on-alert edge
# ---------------------------------------------------------------------------


@respx.mock
def test_pause_on_alert_edge_only(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    route = _state_route("binary_sensor.door", "on")
    with base.sync_session() as s:
        printer = _seed_printer_raw(s, name="PauseP")
        s.add(
            HaLink(
                printer_id=printer.id,
                entity_id="binary_sensor.door",
                role=HaRole.DOOR,
                alert_condition={"op": "eq", "value": "on"},
                pause_print=True,
            )
        )
        s.commit()
        pid = printer.id

    r.set(state_key(pid), json.dumps(RUNNING_STATE))
    pubsub = r.pubsub()
    pubsub.subscribe(command_channel(pid))
    pubsub.get_message(timeout=1)  # subscribe confirmation

    with base.sync_session() as s:
        poll_once(s, r, _cfg())
        poll_once(s, r, _cfg())  # second tick while still firing: no 2nd pause

    msgs = []
    while True:
        m = pubsub.get_message(timeout=0.2)
        if m is None:
            break
        if m["type"] == "message":
            msgs.append(json.loads(m["data"]))
    assert msgs == [{"command": "pause"}]

    # clear then fire again -> a second pause.
    route.respond(200, json={"state": "off", "attributes": {}})
    with base.sync_session() as s:
        poll_once(s, r, _cfg())
    assert r.get(fired_key(1)) is None

    route.respond(200, json={"state": "on", "attributes": {}})
    with base.sync_session() as s:
        poll_once(s, r, _cfg())

    msgs = []
    while True:
        m = pubsub.get_message(timeout=0.2)
        if m is None:
            break
        if m["type"] == "message":
            msgs.append(json.loads(m["data"]))
    assert msgs == [{"command": "pause"}]
    pubsub.close()
    r.close()


@respx.mock
def test_pause_print_false_gives_no_pause(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    _state_route("binary_sensor.door", "on")
    with base.sync_session() as s:
        printer = _seed_printer_raw(s, name="NoPauseP")
        s.add(
            HaLink(
                printer_id=printer.id,
                entity_id="binary_sensor.door",
                role=HaRole.DOOR,
                alert_condition={"op": "eq", "value": "on"},
                pause_print=False,
            )
        )
        s.commit()
        pid = printer.id

    r.set(state_key(pid), json.dumps(RUNNING_STATE))
    pubsub = r.pubsub()
    pubsub.subscribe(command_channel(pid))
    pubsub.get_message(timeout=1)

    with base.sync_session() as s:
        poll_once(s, r, _cfg())

    assert pubsub.get_message(timeout=0.3) is None
    pubsub.close()
    r.close()


@respx.mock
def test_pause_skipped_when_printer_idle(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    _state_route("binary_sensor.door", "on")
    with base.sync_session() as s:
        printer = _seed_printer_raw(s, name="IdlePauseP")
        s.add(
            HaLink(
                printer_id=printer.id,
                entity_id="binary_sensor.door",
                role=HaRole.DOOR,
                alert_condition={"op": "eq", "value": "on"},
                pause_print=True,
            )
        )
        s.commit()
        pid = printer.id

    r.set(state_key(pid), json.dumps(IDLE_STATE))  # IDLE, not printing
    pubsub = r.pubsub()
    pubsub.subscribe(command_channel(pid))
    pubsub.get_message(timeout=1)

    with base.sync_session() as s:
        poll_once(s, r, _cfg())

    assert pubsub.get_message(timeout=0.3) is None
    pubsub.close()
    r.close()
