"""Admin MQTT surfaces: the per-printer raw MQTT debug feed (history + SSE) and
the generic external-broker relay settings (``/settings/mqtt-relay``)."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import AsyncIterator

import anyio
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perm
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Printer
from app.models.auth import User
from app.services import mqtt_debug
from app.services.ha_config import actor_status
from app.services.mqtt_relay_config import (
    KEEP_SENTINEL,
    STATUS_KEY,
    MqttRelayConfig,
    get_relay_config,
    set_relay_config,
    valid_prefix,
)
from app.services.permissions import load_grants

router = APIRouter(
    prefix="/admin/printers", tags=["admin-mqtt"], dependencies=[Depends(require_perm("admin"))]
)
relay_router = APIRouter(
    prefix="/settings/mqtt-relay",
    tags=["mqtt-relay"],
    dependencies=[Depends(require_perm("admin"))],
)


# --- raw MQTT debug feed ---------------------------------------------------


async def _require_printer(db: AsyncSession, printer_id: int) -> None:
    if await db.get(Printer, printer_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")


@router.get("/{printer_id}/mqtt")
async def mqtt_history(
    printer_id: int,
    limit: int = Query(50, ge=1, le=mqtt_debug.KEEP),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """Last ``limit`` raw messages, oldest first."""
    await _require_printer(db, printer_id)
    r = aioredis.Redis.from_url(settings.redis_url)
    try:
        await mqtt_debug.mark_viewer(r, printer_id)  # start tapping for the modal
        raw = await r.lrange(mqtt_debug.list_key(printer_id), 0, limit - 1)
    finally:
        await r.aclose()
    out = []
    for item in reversed(raw):
        with contextlib.suppress(ValueError):
            out.append(json.loads(item))
    return out


async def _stream(redis_url: str, printer_id: int, heartbeat: float = 15.0) -> AsyncIterator[bytes]:
    client = aioredis.Redis.from_url(redis_url)
    pubsub = client.pubsub()
    chan = mqtt_debug.channel(printer_id)
    await pubsub.subscribe(chan)
    try:
        await mqtt_debug.mark_viewer(client, printer_id)
        renewed = time.monotonic()
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=heartbeat)
            if time.monotonic() - renewed >= mqtt_debug.VIEWER_TTL_S / 3:
                await mqtt_debug.mark_viewer(client, printer_id)  # renew while open
                renewed = time.monotonic()
            if msg is None:
                yield b": ping\n\n"
                continue
            data = msg["data"]
            if isinstance(data, bytes):
                data = data.decode()
            yield f"data: {data}\n\n".encode()
    finally:
        with anyio.CancelScope(shield=True):
            await pubsub.unsubscribe(chan)
            await pubsub.aclose()
            await client.aclose()


@router.get("/{printer_id}/mqtt/stream")
async def mqtt_stream(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    await _require_printer(db, printer_id)
    # The yield-dependency session would otherwise stay checked out for the
    # whole (unbounded) stream; release the connection now.
    await db.close()
    return StreamingResponse(
        _stream(settings.redis_url, printer_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- generic MQTT relay -----------------------------------------------------


class RelayIn(BaseModel):
    enabled: bool | None = None
    host: str | None = Field(None, max_length=255)
    port: int | None = Field(None, ge=1, le=65535)
    tls: bool | None = None
    tls_verify: bool | None = None
    username: str | None = Field(None, max_length=255)
    password: str | None = Field(None, max_length=512)
    topic_prefix: str | None = Field(None, max_length=120)
    commands_enabled: bool | None = None
    actor_user_id: int | None = None
    low_filament_pct: int | None = Field(None, ge=0, le=100)

    @field_validator("topic_prefix")
    @classmethod
    def _prefix(cls, v: str | None) -> str | None:
        if v is not None and not valid_prefix(v):
            raise ValueError("topic prefix may only use letters, digits, _ . - and '/' separators")
        return v

    @field_validator("host")
    @classmethod
    def _host(cls, v: str | None) -> str | None:
        if v is not None and any(c in v for c in "/@ \t"):
            raise ValueError("host must be a bare hostname or IP")
        return v


class RelayOut(BaseModel):
    enabled: bool
    host: str | None
    port: int
    tls: bool
    tls_verify: bool
    username: str | None
    has_password: bool
    topic_prefix: str
    commands_enabled: bool
    actor_user_id: int | None
    low_filament_pct: int
    actor_username: str | None
    actor_ok: bool
    actor_error: str | None
    status: dict | None


class RelayActorOut(BaseModel):
    id: int
    username: str


class RelayTestOut(BaseModel):
    ok: bool
    detail: str


async def _relay_out(db: AsyncSession, settings: Settings) -> RelayOut:
    c = await get_relay_config(db, settings)
    r = aioredis.Redis.from_url(settings.redis_url)
    try:
        raw = await r.get(STATUS_KEY)
    finally:
        await r.aclose()
    st = None
    if raw:
        with contextlib.suppress(ValueError, TypeError):
            st = json.loads(raw)
    actor = await db.get(User, c.actor_user_id) if c.actor_user_id else None
    actor_ok, actor_error = (True, None)
    if c.commands_enabled:
        actor_ok, actor_error = await actor_status(db, c.actor_user_id)
    return RelayOut(
        enabled=c.enabled,
        host=c.host,
        port=c.port,
        tls=c.tls,
        tls_verify=c.tls_verify,
        username=c.username,
        has_password=bool(c.password),
        topic_prefix=c.topic_prefix,
        commands_enabled=c.commands_enabled,
        actor_user_id=c.actor_user_id,
        low_filament_pct=c.low_filament_pct,
        actor_username=actor.username if actor else None,
        actor_ok=actor_ok,
        actor_error=actor_error,
        status=st,
    )


@relay_router.get("", response_model=RelayOut)
async def get_relay(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> RelayOut:
    return await _relay_out(db, settings)


@relay_router.put("", response_model=RelayOut)
async def put_relay(
    payload: RelayIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RelayOut:
    patch = payload.model_dump(exclude_unset=True)
    for k in (
        "enabled",
        "tls",
        "tls_verify",
        "commands_enabled",
        "port",
        "topic_prefix",
        "low_filament_pct",
    ):
        if patch.get(k) is None:
            patch.pop(k, None)
    for k in ("host", "username"):
        if k in patch and not patch[k]:
            patch[k] = None
    actor_id = patch.get("actor_user_id")
    if actor_id is not None:
        actor = await db.get(User, actor_id)
        if actor is None or not actor.is_active:
            raise HTTPException(422, "actor_user_id must be an active user")
        _, perms, _ = await load_grants(db, actor)
        if "printers:control" not in perms:
            raise HTTPException(422, "service user needs the printers:control permission")
    stored = await get_relay_config(db, settings)
    if patch.get("enabled", stored.enabled) and not patch.get("host", stored.host):
        raise HTTPException(422, "host is required to enable the relay")
    await set_relay_config(db, settings, patch)
    return await _relay_out(db, settings)


@relay_router.get("/actors", response_model=list[RelayActorOut])
async def relay_actors(db: AsyncSession = Depends(get_db)) -> list[RelayActorOut]:
    users = (await db.scalars(select(User).where(User.is_active).order_by(User.username))).all()
    out = []
    for user in users:
        _, perms, _ = await load_grants(db, user)
        if "printers:control" in perms:
            out.append(RelayActorOut(id=user.id, username=user.username))
    return out


def _run_test(c: MqttRelayConfig, timeout: float = 5.0) -> tuple[bool, str]:
    if not c.host:
        return False, "no host configured"
    import paho.mqtt.client as mqtt

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    if c.username:
        client.username_pw_set(c.username, c.password)
    if c.tls:
        client.tls_set()
        if not c.tls_verify:
            client.tls_insecure_set(True)
    result: dict = {}
    done = threading.Event()

    def on_connect(_c, _u, _f, rc, _p=None) -> None:  # noqa: ANN001
        result["rc"] = rc
        done.set()

    client.on_connect = on_connect
    try:
        client.connect(c.host, c.port, keepalive=int(timeout))
    except Exception as exc:  # noqa: BLE001 -- type only, never echo host/credentials
        return False, type(exc).__name__
    client.loop_start()
    try:
        if not done.wait(timeout):
            return False, "timed out waiting for broker"
        rc = result.get("rc")
        return (True, "connected") if rc == 0 else (False, mqtt.connack_string(rc))
    finally:
        client.loop_stop()
        with contextlib.suppress(Exception):
            client.disconnect()


@relay_router.post("/test", response_model=RelayTestOut)
async def test_relay(
    payload: RelayIn | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RelayTestOut:
    """Try the stored config with the (unsaved) form values overlaid."""
    stored = await get_relay_config(db, settings)
    vals = {f: getattr(stored, f) for f in MqttRelayConfig.__dataclass_fields__}
    if payload is not None:
        for k, v in payload.model_dump(exclude_unset=True).items():
            if k == "password":
                if v is not None and v != KEEP_SENTINEL:
                    vals["password"] = v or None
            elif v is not None and k in vals:
                vals[k] = v
    ok, detail = await run_in_threadpool(_run_test, MqttRelayConfig(**vals))
    return RelayTestOut(ok=ok, detail=detail)
