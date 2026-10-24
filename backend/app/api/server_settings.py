"""Admin "Server" settings API (``/settings/server``): the DB-backed infra
knobs in ``app.services.server_config`` plus live probes for the sidecars.
Admin only (``require_perm("admin")``), like ``admin_ops``."""

from __future__ import annotations

import contextlib
import json
import time

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm
from app.config import Settings, get_settings
from app.db import get_db
from app.logging_config import apply_level
from app.models import AuditLog
from app.schemas.settings import (
    ProbeOut,
    ServerDerivedOut,
    ServerSettingsIn,
    ServerSettingsOut,
    ServerStatusOut,
    SlicerTestIn,
    SlicerTestOut,
    VpStatusOut,
)
from app.services import obico
from app.services.server_config import (
    ENV_SEED,
    ServerConfig,
    cookie_secure,
    get_server_config,
    ignored_env,
    migrate_legacy_slicer,
    seeded_fields,
    set_server_config,
)

router = APIRouter(
    prefix="/settings/server",
    tags=["server-settings"],
    dependencies=[Depends(require_perm("admin"))],
)
_admin = Depends(require_perm("admin"))

VP_STATUS_KEY = "tdmm:vp:status"
PROBE_KEY = "tdmm:server:probe"
PROBE_TTL_S = 10
SLICER_TEST_TIMEOUT_S = 5.0
SLICER_PROBE_TIMEOUT_S = 3.0
OBICO_PROBE_TIMEOUT_S = 2.0


async def _out(db: AsyncSession, request: Request) -> ServerSettingsOut:
    cfg = await get_server_config(db)
    seeded = await seeded_fields(db)
    return ServerSettingsOut(
        config=cfg,
        seeded_from_env={f: ENV_SEED[f] for f in seeded},
        ignored_env=ignored_env(),
        derived=ServerDerivedOut(
            cookie_secure=cookie_secure(cfg, None) if cfg.public_url else None,
            request_origin=str(request.base_url).rstrip("/"),
        ),
    )


@router.get("", response_model=ServerSettingsOut)
async def get_server_settings(
    request: Request, db: AsyncSession = Depends(get_db)
) -> ServerSettingsOut:
    await migrate_legacy_slicer(db)
    return await _out(db, request)


@router.put("", response_model=ServerSettingsOut)
async def put_server_settings(
    payload: ServerSettingsIn,
    request: Request,
    ctx: AuthContext = _admin,
    db: AsyncSession = Depends(get_db),
) -> ServerSettingsOut:
    await migrate_legacy_slicer(db)
    old = await get_server_config(db)
    new = await set_server_config(
        db, ServerConfig.model_validate(payload.model_dump()), commit=False
    )
    changed = {
        f: [getattr(old, f), getattr(new, f)]
        for f in ServerConfig.model_fields
        if getattr(old, f) != getattr(new, f)
    }
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            action="server_settings.update",
            payload={"changed": changed},
        )
    )
    await db.commit()
    apply_level(new.log_level)
    return await _out(db, request)


async def _probe(url: str, timeout: float) -> tuple[bool, str, int | None]:
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(url)
    except httpx.TimeoutException:
        return False, "timed out", None
    except httpx.HTTPError as exc:
        return False, type(exc).__name__, None
    ms = int((time.monotonic() - start) * 1000)
    if resp.status_code == 200:
        return True, "ok", ms
    return False, f"HTTP {resp.status_code}", ms


@router.post("/slicer/test", response_model=SlicerTestOut)
async def test_slicer(payload: SlicerTestIn, db: AsyncSession = Depends(get_db)) -> SlicerTestOut:
    url = (payload.url or (await get_server_config(db)).slicer_url).strip().rstrip("/")
    if not url:
        return SlicerTestOut(ok=False, latency_ms=None, detail="not_configured")
    ok, detail, ms = await _probe(f"{url}/health", SLICER_TEST_TIMEOUT_S)
    return SlicerTestOut(ok=ok, latency_ms=ms, detail=detail)


async def _probes(r: aioredis.Redis, cfg: ServerConfig, obico_url: str) -> dict:
    ident = f"{cfg.slicer_enabled}|{cfg.slicer_url}|{obico_url}"
    try:
        raw = await r.get(PROBE_KEY)
        if raw:
            cached = json.loads(raw)
            if cached.get("ident") == ident:
                return cached
    except (aioredis.RedisError, ValueError):
        pass
    if not cfg.slicer_enabled:
        slicer = {"ok": False, "detail": "disabled"}
    elif not cfg.slicer_url:
        slicer = {"ok": False, "detail": "not_configured"}
    else:
        ok, detail, _ = await _probe(f"{cfg.slicer_url}/health", SLICER_PROBE_TIMEOUT_S)
        slicer = {"ok": ok, "detail": detail}
    ok, detail, _ = await _probe(f"{obico_url}/hc/", OBICO_PROBE_TIMEOUT_S)
    out = {"ident": ident, "slicer": slicer, "obico": {"ok": ok, "detail": detail}}
    with contextlib.suppress(aioredis.RedisError):
        await r.set(PROBE_KEY, json.dumps(out), ex=PROBE_TTL_S)
    return out


async def _vp_status(r: aioredis.Redis) -> VpStatusOut:
    try:
        raw = await r.get(VP_STATUS_KEY)
        if raw:
            data = json.loads(raw)
            if data.get("state") in ("idle", "running", "error"):
                return VpStatusOut(**data)
    except (aioredis.RedisError, ValueError, TypeError):
        pass
    return VpStatusOut(state="offline")


@router.get("/status", response_model=ServerStatusOut)
async def server_status(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ServerStatusOut:
    cfg = await get_server_config(db)
    obico_url = (await obico.get_config(db, settings)).url
    r = aioredis.Redis.from_url(settings.redis_url)
    try:
        probes = await _probes(r, cfg, obico_url)
        vp = await _vp_status(r)
    finally:
        await r.aclose()
    slicer = ProbeOut(**probes["slicer"])
    live = cfg.slicer_enabled and bool(cfg.slicer_url)
    return ServerStatusOut(
        slicer=slicer,
        slicer_healthy=slicer.ok if live else None,
        obico=ProbeOut(**probes["obico"]),
        virtual_printer=vp,
    )
