"""Home Assistant settings API (P8 Task 7): connection config (read/write,
secrets write-only), MQTT/REST connection tests, and ``ha_links`` CRUD.

Every route -- GETs included -- requires ``settings:write`` (ruling: HA
config/links are an admin-only surface, same as the printers CRUD router
and ``/scan``), unlike the plain storage-settings router which only gates
writes.
"""

from __future__ import annotations

import contextlib
import json
import threading

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models.home_assistant import HaLink
from app.models.printing import Printer
from app.schemas.home_assistant import (
    HaLinkIn,
    HaLinkOut,
    HaLinkPatchIn,
    HaSettingsIn,
    HaSettingsOut,
    HaTestOut,
    check_link_invariants,
    validate_alert_condition,
)
from app.services import ha_state
from app.services.ha_client import HaClient, HaUnreachable
from app.services.ha_config import HaConfig, get_ha_config, parse_mqtt_url, set_ha_config

router = APIRouter(
    prefix="/settings/home-assistant",
    tags=["home-assistant"],
    dependencies=[Depends(require_perm("settings:write"))],
)

_KEEP_SENTINEL = "***"
_MQTT_TEST_TIMEOUT_S = 5.0


def _redis(settings: Settings) -> aioredis.Redis:
    return aioredis.Redis.from_url(settings.redis_url)


def _apply_overrides(config: HaConfig, patch: dict) -> HaConfig:
    """Pure in-memory overlay of an unsaved ``HaSettingsIn`` patch onto the
    stored config -- same sentinel rules as ``set_ha_config`` (omitted/
    ``None``/``"***"`` keeps, ``""`` clears a secret, anything else
    replaces), but never persisted. Used by the two connection-test
    endpoints to try an edit before saving it."""
    values = {
        "ha_mqtt_url": config.ha_mqtt_url,
        "ha_mqtt_user": config.ha_mqtt_user,
        "ha_mqtt_password": config.ha_mqtt_password,
        "ha_url": config.ha_url,
        "ha_token": config.ha_token,
        "ha_discovery_prefix": config.ha_discovery_prefix,
    }
    for field in ("ha_mqtt_url", "ha_mqtt_user", "ha_discovery_prefix", "ha_url"):
        if field in patch and patch[field] is not None:
            values[field] = patch[field]
    for field in ("ha_mqtt_password", "ha_token"):
        if field not in patch:
            continue
        new_value = patch[field]
        if new_value is None or new_value == _KEEP_SENTINEL:
            continue
        values[field] = new_value or None
    return HaConfig(**values)


# ---------------------------------------------------------------------------
# Connection config
# ---------------------------------------------------------------------------


@router.get("", response_model=HaSettingsOut)
async def get_ha_settings(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> HaSettingsOut:
    config = await get_ha_config(db, settings)
    r = _redis(settings)
    try:
        pull_raw = await r.get(ha_state.STATUS_KEY)
        bridge_raw = await r.get("ha:bridge:status")
    finally:
        await r.aclose()
    pull_status = None
    if pull_raw:
        with contextlib.suppress(ValueError, TypeError):
            pull_status = json.loads(pull_raw)
    bridge_status = bridge_raw.decode() if isinstance(bridge_raw, bytes) else bridge_raw

    return HaSettingsOut(
        ha_mqtt_url=config.ha_mqtt_url,
        ha_mqtt_user=config.ha_mqtt_user,
        has_mqtt_password=bool(config.ha_mqtt_password),
        ha_discovery_prefix=config.ha_discovery_prefix,
        ha_url=config.ha_url,
        has_token=bool(config.ha_token),
        pull_status=pull_status,
        bridge_status=bridge_status,
    )


@router.put("", response_model=HaSettingsOut)
async def put_ha_settings(
    payload: HaSettingsIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HaSettingsOut:
    patch = payload.model_dump(exclude_unset=True)
    await set_ha_config(db, settings, patch)
    return await get_ha_settings(db=db, settings=settings)


def _run_mqtt_test(config: HaConfig) -> tuple[bool, str]:
    """``paho`` connect + wait for CONNACK, at most
    ``_MQTT_TEST_TIMEOUT_S`` -- run on a worker thread by the caller
    (``run_in_threadpool``). ``detail`` is always the CONNACK reason string
    or an exception TYPE NAME, never the raw exception message (which
    could echo the broker URL/credentials) -- same "never echo secrets"
    rule as ``HaClient``.
    """
    if not config.ha_mqtt_url:
        return False, "no mqtt broker configured"
    try:
        host, port, tls = parse_mqtt_url(config.ha_mqtt_url)
    except ValueError:
        return False, "invalid mqtt url"

    import paho.mqtt.client as mqtt

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    if config.ha_mqtt_user:
        client.username_pw_set(config.ha_mqtt_user, config.ha_mqtt_password)
    if tls:
        client.tls_set()

    result: dict = {}
    connected = threading.Event()

    def _on_connect(c, userdata, flags, rc, properties=None) -> None:  # noqa: ANN001
        result["rc"] = rc
        connected.set()

    client.on_connect = _on_connect
    try:
        client.connect(host, port, keepalive=int(_MQTT_TEST_TIMEOUT_S))
    except Exception as exc:  # noqa: BLE001 -- never echo the raw message
        return False, type(exc).__name__

    client.loop_start()
    try:
        got = connected.wait(_MQTT_TEST_TIMEOUT_S)
        if not got:
            return False, "timed out waiting for broker"
        rc = result.get("rc")
        if rc == 0:
            return True, "connected"
        return False, mqtt.connack_string(rc)
    finally:
        client.loop_stop()
        with contextlib.suppress(Exception):
            client.disconnect()


@router.post("/test-mqtt", response_model=HaTestOut)
async def test_mqtt(
    payload: HaSettingsIn | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HaTestOut:
    stored = await get_ha_config(db, settings)
    config = stored if payload is None else _apply_overrides(stored, payload.model_dump())
    ok, detail = await run_in_threadpool(_run_mqtt_test, config)
    return HaTestOut(ok=ok, detail=detail)


@router.post("/test-rest", response_model=HaTestOut)
async def test_rest(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> HaTestOut:
    config = await get_ha_config(db, settings)
    if not config.ha_url or not config.ha_token:
        return HaTestOut(ok=False, detail="home assistant url/token not configured")

    def _ping() -> tuple[bool, str]:
        client = HaClient(config.ha_url, config.ha_token)
        try:
            message = client.ping()
            return True, message or "ok"
        except HaUnreachable as exc:
            detail = "token rejected" if "401" in str(exc) else "could not reach home assistant"
            return False, detail
        finally:
            client.close()

    ok, detail = await run_in_threadpool(_ping)
    return HaTestOut(ok=ok, detail=detail)


# ---------------------------------------------------------------------------
# Links CRUD
# ---------------------------------------------------------------------------


async def _printer_names(db: AsyncSession, printer_ids: set[int]) -> dict[int, str]:
    if not printer_ids:
        return {}
    rows = (
        await db.execute(select(Printer.id, Printer.name).where(Printer.id.in_(printer_ids)))
    ).all()
    return dict(rows)


def _link_out(link: HaLink, names: dict[int, str]) -> HaLinkOut:
    return HaLinkOut(
        id=link.id,
        printer_id=link.printer_id,
        printer_name=names.get(link.printer_id) if link.printer_id is not None else None,
        entity_id=link.entity_id,
        role=link.role,
        alert_condition=link.alert_condition,
        hold_queue=link.hold_queue,
        pause_print=link.pause_print,
        created_by_user_id=link.created_by_user_id,
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


async def _get_link_or_404(db: AsyncSession, link_id: int) -> HaLink:
    link = await db.get(HaLink, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"ha link {link_id} not found")
    return link


async def _require_printer(db: AsyncSession, printer_id: int | None) -> None:
    if printer_id is None:
        return
    if await db.get(Printer, printer_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"printer {printer_id} not found")


@router.get("/links", response_model=list[HaLinkOut])
async def list_links(db: AsyncSession = Depends(get_db)) -> list[HaLinkOut]:
    links = list((await db.execute(select(HaLink).order_by(HaLink.id))).scalars().all())
    names = await _printer_names(db, {link.printer_id for link in links if link.printer_id})
    return [_link_out(link, names) for link in links]


@router.post("/links", status_code=status.HTTP_201_CREATED, response_model=HaLinkOut)
async def create_link(
    payload: HaLinkIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> HaLinkOut:
    await _require_printer(db, payload.printer_id)
    link = HaLink(
        printer_id=payload.printer_id,
        entity_id=payload.entity_id,
        role=payload.role,
        alert_condition=payload.alert_condition,
        hold_queue=payload.hold_queue,
        pause_print=payload.pause_print,
        created_by_user_id=ctx.user.id,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    names = await _printer_names(db, {link.printer_id} if link.printer_id else set())
    return _link_out(link, names)


@router.patch("/links/{link_id}", response_model=HaLinkOut)
async def patch_link(
    link_id: int,
    payload: HaLinkPatchIn,
    db: AsyncSession = Depends(get_db),
) -> HaLinkOut:
    link = await _get_link_or_404(db, link_id)
    patch = payload.model_dump(exclude_unset=True)

    if "alert_condition" in patch:
        validate_alert_condition(patch["alert_condition"])
    if "printer_id" in patch:
        await _require_printer(db, patch["printer_id"])

    merged = {
        "role": patch.get("role", link.role),
        "printer_id": patch.get("printer_id", link.printer_id),
        "alert_condition": patch.get("alert_condition", link.alert_condition),
        "hold_queue": patch.get("hold_queue", link.hold_queue),
        "pause_print": patch.get("pause_print", link.pause_print),
    }
    try:
        check_link_invariants(**merged)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    fields = ("printer_id", "entity_id", "role", "alert_condition", "hold_queue", "pause_print")
    for field in fields:
        if field in patch:
            setattr(link, field, patch[field])

    await db.commit()
    await db.refresh(link)
    names = await _printer_names(db, {link.printer_id} if link.printer_id else set())
    return _link_out(link, names)


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(link_id: int, db: AsyncSession = Depends(get_db)) -> None:
    link = await _get_link_or_404(db, link_id)
    await db.delete(link)
    await db.commit()
