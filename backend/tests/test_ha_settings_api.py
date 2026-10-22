"""Home Assistant settings API (P8 Task 7): connection config (secrets
write-only), MQTT/REST connection tests, links CRUD, and the ``ha_alert``/
``printer_hold_reason`` surfacing on the printers overview and queue.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import httpx
import pytest
import redis.asyncio as aioredis
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Model, Revision
from app.models.enums import BlobFormat, BlobKind, PrinterKind
from app.models.library import File
from app.services.ha_state import alert_key

pytestmark = pytest.mark.usefixtures("library_root")

HA_URL = "http://homeassistant.local:8123"


@pytest.fixture(autouse=True)
def _clean_ha_redis_keys(redis_url: str):
    """``redis_url`` is session-scoped while Postgres ids restart per test
    (``RESTART IDENTITY``) -- a printer id gets reused across tests, so any
    ``ha:*`` key a prior test set without a TTL must not leak into a later
    test's printer with the same id (mirrors ``test_ha_hold.py``'s own
    fixture of the same name)."""
    import redis as redis_lib

    yield
    r = redis_lib.Redis.from_url(redis_url)
    try:
        for key in r.scan_iter("ha:*"):
            r.delete(key)
    finally:
        r.close()


async def _printer(db_session: AsyncSession, name: str = "P1", enabled: bool = True) -> int:
    from app.models import Printer

    p = Printer(
        name=name,
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.1",
        serial=name,
        access_code_enc="x",
        enabled=enabled,
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p.id


async def _model_and_revision(db_session: AsyncSession, slug: str) -> tuple[Model, Revision]:
    model = Model(slug=slug, name=slug)
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db_session.add(revision)
    await db_session.commit()
    await db_session.refresh(revision)
    return model, revision


async def _sliced_file(
    db_session: AsyncSession, seed_file: Callable[..., Awaitable[File]], slug: str
) -> File:
    model, revision = await _model_and_revision(db_session, slug)
    return await seed_file(
        model,
        revision,
        f"{slug}.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )


# ---------------------------------------------------------------------------
# connection config: secrets write-only + sentinel
# ---------------------------------------------------------------------------


async def test_get_secret_never_returned(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.put(
        "/api/settings/home-assistant",
        json={
            "ha_mqtt_url": "mqtt://broker.local",
            "ha_mqtt_password": "mqttsecret",
            "ha_url": HA_URL,
            "ha_token": "longlivedtoken",
        },
    )
    assert r.status_code == 200, r.text
    assert "mqttsecret" not in r.text
    assert "longlivedtoken" not in r.text
    assert r.json()["has_mqtt_password"] is True
    assert r.json()["has_token"] is True

    r = await authenticated_client.get("/api/settings/home-assistant")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "mqttsecret" not in r.text
    assert "longlivedtoken" not in r.text
    assert body["has_mqtt_password"] is True
    assert body["has_token"] is True
    assert body["ha_mqtt_url"] == "mqtt://broker.local"
    assert body["ha_url"] == HA_URL


async def test_put_sentinel_keeps(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.put(
        "/api/settings/home-assistant",
        json={"ha_mqtt_password": "first", "ha_token": "firsttoken"},
    )
    assert r.status_code == 200, r.text

    r = await authenticated_client.put(
        "/api/settings/home-assistant",
        json={"ha_mqtt_password": "***", "ha_token": "***", "ha_discovery_prefix": "custom"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_mqtt_password"] is True
    assert body["has_token"] is True
    assert body["ha_discovery_prefix"] == "custom"


async def test_put_bad_mqtt_url_is_422(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.put(
        "/api/settings/home-assistant", json={"ha_mqtt_url": "http://not-mqtt.local"}
    )
    assert r.status_code == 422


async def test_put_bad_ha_url_is_422(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.put("/api/settings/home-assistant", json={"ha_url": "not-a-url"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# connection tests
# ---------------------------------------------------------------------------


async def test_test_mqtt_ok(authenticated_client: httpx.AsyncClient, mqtt_url: str) -> None:
    r = await authenticated_client.post(
        "/api/settings/home-assistant/test-mqtt", json={"ha_mqtt_url": mqtt_url}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True


async def test_test_mqtt_unreachable(
    authenticated_client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    r = await authenticated_client.post(
        "/api/settings/home-assistant/test-mqtt",
        json={
            "ha_mqtt_url": "mqtt://127.0.0.1:1",
            "ha_mqtt_user": "u",
            "ha_mqtt_password": "supersecretpw",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "supersecretpw" not in r.text
    assert "supersecretpw" not in caplog.text


@respx.mock
async def test_test_rest_ok(authenticated_client: httpx.AsyncClient) -> None:
    await authenticated_client.put(
        "/api/settings/home-assistant", json={"ha_url": HA_URL, "ha_token": "tok"}
    )
    respx.get(f"{HA_URL}/api/").mock(
        return_value=httpx.Response(200, json={"message": "API running."})
    )
    r = await authenticated_client.post("/api/settings/home-assistant/test-rest")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "detail": "API running."}


@respx.mock
async def test_test_rest_401_is_token_rejected(authenticated_client: httpx.AsyncClient) -> None:
    await authenticated_client.put(
        "/api/settings/home-assistant", json={"ha_url": HA_URL, "ha_token": "badtok"}
    )
    respx.get(f"{HA_URL}/api/").mock(return_value=httpx.Response(401))
    r = await authenticated_client.post("/api/settings/home-assistant/test-rest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["detail"] == "token rejected"
    assert "badtok" not in r.text


# ---------------------------------------------------------------------------
# links CRUD + validation
# ---------------------------------------------------------------------------


async def test_create_link_bad_entity_id_is_422(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.post(
        "/api/settings/home-assistant/links",
        json={"entity_id": "not valid", "role": "door"},
    )
    assert r.status_code == 422


async def test_energy_kwh_requires_printer_id(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.post(
        "/api/settings/home-assistant/links",
        json={"entity_id": "sensor.plug_energy", "role": "energy_kwh"},
    )
    assert r.status_code == 422


async def test_hold_queue_requires_alert_condition(
    authenticated_client: httpx.AsyncClient,
) -> None:
    r = await authenticated_client.post(
        "/api/settings/home-assistant/links",
        json={"entity_id": "binary_sensor.door", "role": "door", "hold_queue": True},
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "condition",
    [
        {"op": "gt", "value": "not-a-number"},
        {"op": "in", "value": "not-a-list"},
        {"op": "bogus", "value": 1},
    ],
)
async def test_bad_alert_condition_shapes_are_422(
    authenticated_client: httpx.AsyncClient, condition: dict
) -> None:
    r = await authenticated_client.post(
        "/api/settings/home-assistant/links",
        json={"entity_id": "binary_sensor.door", "role": "door", "alert_condition": condition},
    )
    assert r.status_code == 422


async def test_links_crud_round_trip(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    pid = await _printer(db_session, "LinkPrinter")

    r = await authenticated_client.post(
        "/api/settings/home-assistant/links",
        json={
            "printer_id": pid,
            "entity_id": "binary_sensor.door",
            "role": "door",
            "alert_condition": {"op": "eq", "value": "on"},
            "hold_queue": True,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["printer_name"] == "LinkPrinter"
    link_id = body["id"]

    r = await authenticated_client.get("/api/settings/home-assistant/links")
    assert r.status_code == 200, r.text
    assert any(link["id"] == link_id for link in r.json())

    r = await authenticated_client.patch(
        f"/api/settings/home-assistant/links/{link_id}", json={"entity_id": "binary_sensor.door2"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["entity_id"] == "binary_sensor.door2"

    # Flipping to energy_kwh while keeping the printer_id stays valid...
    r = await authenticated_client.patch(
        f"/api/settings/home-assistant/links/{link_id}",
        json={"role": "energy_kwh", "hold_queue": False, "pause_print": False},
    )
    assert r.status_code == 200, r.text

    r = await authenticated_client.delete(f"/api/settings/home-assistant/links/{link_id}")
    assert r.status_code == 204

    r = await authenticated_client.get("/api/settings/home-assistant/links")
    assert all(link["id"] != link_id for link in r.json())


@pytest.mark.parametrize("field", ["entity_id", "role", "hold_queue", "pause_print"])
async def test_patch_explicit_null_on_non_nullable_field_is_422(
    authenticated_client: httpx.AsyncClient, field: str
) -> None:
    """Final review M1: an explicit `null` for a non-nullable HaLink field
    must 422, not fall through to a raw DB error on commit."""
    r = await authenticated_client.post(
        "/api/settings/home-assistant/links",
        json={"entity_id": "binary_sensor.door", "role": "door"},
    )
    assert r.status_code == 201, r.text
    link_id = r.json()["id"]

    r = await authenticated_client.patch(
        f"/api/settings/home-assistant/links/{link_id}", json={field: None}
    )
    assert r.status_code == 422, r.text


async def test_patch_explicit_null_printer_id_is_allowed(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    pid = await _printer(db_session, "NullablePrinter")
    r = await authenticated_client.post(
        "/api/settings/home-assistant/links",
        json={"printer_id": pid, "entity_id": "binary_sensor.door", "role": "door"},
    )
    assert r.status_code == 201, r.text
    link_id = r.json()["id"]

    r = await authenticated_client.patch(
        f"/api/settings/home-assistant/links/{link_id}", json={"printer_id": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["printer_id"] is None


async def test_patch_to_energy_kwh_without_printer_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    r = await authenticated_client.post(
        "/api/settings/home-assistant/links",
        json={"entity_id": "sensor.power", "role": "power_w"},
    )
    assert r.status_code == 201, r.text
    link_id = r.json()["id"]

    r = await authenticated_client.patch(
        f"/api/settings/home-assistant/links/{link_id}", json={"role": "energy_kwh"}
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# viewer 403 (the sweep covers mutating routes; GETs are gated here)
# ---------------------------------------------------------------------------


async def test_viewer_gets_403_on_get_settings(viewer_client: httpx.AsyncClient) -> None:
    r = await viewer_client.get("/api/settings/home-assistant")
    assert r.status_code == 403
    r = await viewer_client.get("/api/settings/home-assistant/links")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# ha_alert on the printers overview
# ---------------------------------------------------------------------------


async def test_overview_ha_alert(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
    printer_enabled: None,
) -> None:
    pid = await _printer(db_session, "AlertPrinter")
    await redis_client.set(
        alert_key(pid),
        json.dumps({"reason": "Door open", "entities": ["binary_sensor.door"], "hold": True}),
    )

    r = await authenticated_client.get("/api/printers/overview")
    assert r.status_code == 200, r.text
    item = next(p for p in r.json()["printers"] if p["printer"]["id"] == pid)
    assert item["ha_alert"] == "Door open"
    assert item["ha_held"] is True


async def test_overview_no_alert_is_null(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    printer_enabled: None,
) -> None:
    pid = await _printer(db_session, "QuietPrinter")
    r = await authenticated_client.get("/api/printers/overview")
    assert r.status_code == 200, r.text
    item = next(p for p in r.json()["printers"] if p["printer"]["id"] == pid)
    assert item["ha_alert"] is None
    assert item["ha_held"] is False


# ---------------------------------------------------------------------------
# printer_hold_reason on queue entries
# ---------------------------------------------------------------------------


async def test_queue_entry_hold_reason_targeted(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    pid = await _printer(db_session, "TargetedHoldPrinter")
    f = await _sliced_file(db_session, seed_file, "targeted-hold")

    r = await authenticated_client.post("/api/queue", json={"file_id": f.id, "printer_id": pid})
    assert r.status_code == 201, r.text

    await redis_client.set(
        alert_key(pid), json.dumps({"reason": "Smoke detected", "entities": [], "hold": True})
    )

    r = await authenticated_client.get("/api/queue")
    assert r.status_code == 200, r.text
    entry = next(e for e in r.json() if e["printer_id"] == pid)
    assert entry["printer_hold_reason"] == "Smoke detected"


async def test_queue_entry_hold_reason_untargeted_all_held(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    pid = await _printer(db_session, "OnlyEnabledPrinter")
    f = await _sliced_file(db_session, seed_file, "untargeted-hold")

    r = await authenticated_client.post("/api/queue", json={"file_id": f.id})
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]

    await redis_client.set(
        alert_key(pid), json.dumps({"reason": "Door open", "entities": [], "hold": True})
    )

    r = await authenticated_client.get("/api/queue")
    assert r.status_code == 200, r.text
    entry = next(e for e in r.json() if e["id"] == entry_id)
    assert entry["printer_hold_reason"] == "All printers held"


async def test_queue_entry_hold_reason_untargeted_not_all_held(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    held_pid = await _printer(db_session, "HeldPrinter")
    await _printer(db_session, "FreePrinter")
    f = await _sliced_file(db_session, seed_file, "untargeted-partial-hold")

    r = await authenticated_client.post("/api/queue", json={"file_id": f.id})
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]

    await redis_client.set(
        alert_key(held_pid), json.dumps({"reason": "Door open", "entities": [], "hold": True})
    )

    r = await authenticated_client.get("/api/queue")
    assert r.status_code == 200, r.text
    entry = next(e for e in r.json() if e["id"] == entry_id)
    assert entry["printer_hold_reason"] is None
