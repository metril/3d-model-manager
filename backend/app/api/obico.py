"""Q10 Obico failure detection API: global settings (``settings:write``),
per-printer toggle (control) + events (print access), a one-shot test, and the
public single-use snapshot URL the ML sidecar fetches."""

from __future__ import annotations

import io
import json
import secrets
import time
from datetime import datetime
from functools import lru_cache

import anyio
import httpx
import redis as redis_sync
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    require_perm,
    require_printer_access,
    require_printer_enabled,
    require_session,
)
from app.api.printers import _get_or_404
from app.config import Settings, get_settings
from app.db import get_db
from app.models import FailureDetection, Printer, Setting
from app.services import camera_state, obico
from app.services.obico import ObicoConfig, PrinterDetection
from app.services.obico_ml import MlError, score_frame

settings_router = APIRouter(
    prefix="/settings/obico",
    tags=["obico"],
    dependencies=[Depends(require_perm("settings:write"))],
)
router = APIRouter(
    prefix="/printers", tags=["obico"], dependencies=[Depends(require_printer_enabled)]
)
public_router = APIRouter(prefix="/obico", tags=["obico"])

_print = Depends(require_printer_access("print"))
_control = Depends(require_printer_access("control"))
HEALTH_TIMEOUT_S = 5.0
ML_TEST_TIMEOUT_S = 20.0


class ObicoConfigIn(ObicoConfig):
    @model_validator(mode="after")
    def _order(self) -> ObicoConfigIn:
        if self.pause < self.warn:
            raise ValueError("pause threshold must be >= warn threshold")
        return self


class TestIn(BaseModel):
    url: str | None = None
    callback_url: str | None = None


class CheckOut(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class TestOut(BaseModel):
    ok: bool
    detail: str = ""
    checks: list[CheckOut] = []
    ml_version: str | None = None


class ObicoSettingsOut(ObicoConfig):
    seeded_from_env: dict[str, str] = {}
    ml_version: str | None = None
    ml_version_source: str | None = None


class DetectionOut(BaseModel):
    id: int
    print_job_id: int | None
    score: float
    smoothed: float
    action: str
    has_frame: bool
    created_at: datetime


class DetectionStatusOut(BaseModel):
    # idle (no watcher), watching, paused (3 ML errors), error (last ML call failed)
    phase: str = "idle"
    enabled: bool = False  # global + per-printer detection switched on
    last_error: str | None = None
    heartbeat: float | None = None
    active: bool = False
    state: str | None = None
    score: float | None = None
    smoothed: float | None = None
    detection_paused: bool = False


class PrinterDetectionOut(PrinterDetection):
    status: DetectionStatusOut
    effective_action: str
    global_enabled: bool


class DetectionsOut(BaseModel):
    status: DetectionStatusOut
    events: list[DetectionOut]


class TestScoreOut(BaseModel):
    score: float


async def _redis(settings: Settings) -> aioredis.Redis:
    return aioredis.Redis.from_url(settings.redis_url)


async def _status(settings: Settings, printer_id: int, enabled: bool = False) -> DetectionStatusOut:
    r = await _redis(settings)
    try:
        raw = await r.get(obico.status_key(printer_id))
    finally:
        await r.aclose()
    return _parse_status(raw, enabled)


def _parse_status(raw: str | bytes | None, enabled: bool = False) -> DetectionStatusOut:
    if not raw:
        return DetectionStatusOut(enabled=enabled)
    try:
        body = json.loads(raw)
    except ValueError:
        return DetectionStatusOut(enabled=enabled)
    if not isinstance(body, dict):
        return DetectionStatusOut(enabled=enabled)
    paused = bool(body.get("paused"))
    last_error = body.get("last_error") or None
    return DetectionStatusOut(
        phase="paused" if paused else "error" if last_error else "watching",
        enabled=enabled,
        last_error=last_error,
        heartbeat=body.get("heartbeat"),
        active=not body.get("paused"),
        state=body.get("state"),
        score=body.get("score"),
        smoothed=body.get("smoothed"),
        detection_paused=bool(body.get("paused")),
    )


# -- global ---------------------------------------------------------------


async def _settings_out(db: AsyncSession, settings: Settings) -> ObicoSettingsOut:
    cfg = await obico.get_config(db, settings)
    ver = settings.obico_ml_version.strip()
    return ObicoSettingsOut(
        **cfg.model_dump(),
        seeded_from_env=await obico.seeded_from_env(db),
        ml_version=ver or None,
        ml_version_source="configured" if ver else None,
    )


@settings_router.get("", response_model=ObicoSettingsOut)
async def get_obico_settings(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ObicoSettingsOut:
    return await _settings_out(db, settings)


@settings_router.put("", response_model=ObicoSettingsOut)
async def put_obico_settings(
    payload: ObicoConfigIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ObicoSettingsOut:
    cfg = ObicoConfig(**payload.model_dump())
    cfg.url = cfg.url.strip().rstrip("/")
    # A blank callback keeps the currently resolved one (never reads env).
    current = await obico.get_config(db, settings)
    cfg.callback_url = (cfg.callback_url.strip() or current.callback_url).rstrip("/")
    await obico.put_config(db, cfg)
    return await _settings_out(db, settings)


@lru_cache(maxsize=1)
def _test_jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (320, 240), (128, 128, 128)).save(buf, "JPEG", quality=70)
    return buf.getvalue()


async def _run_checks(redis: aioredis.Redis, url: str, callback: str) -> list[CheckOut]:
    """Three independent checks: sidecar /hc/, callback fetch, inference."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S) as client:
            resp = await client.get(f"{url}/hc/")
        sidecar = CheckOut(
            name="sidecar",
            ok=resp.status_code == 200,
            detail=f"HTTP {resp.status_code} in {int((time.monotonic() - t0) * 1000)} ms",
        )
    except Exception as exc:
        sidecar = CheckOut(name="sidecar", ok=False, detail=type(exc).__name__)
    skipped = "skipped: sidecar unreachable"
    if not sidecar.ok:
        return [
            sidecar,
            CheckOut(name="callback", ok=False, detail=skipped),
            CheckOut(name="inference", ok=False, detail=skipped),
        ]
    nonce = secrets.token_urlsafe(32)
    key = obico.nonce_key(nonce)
    await redis.set(key, _test_jpeg(), ex=obico.FRAME_NONCE_TTL_S)
    frame_url = f"{callback}/api/obico/frame/{nonce}"
    t1 = time.monotonic()
    body: object = None
    err = ""
    try:
        async with httpx.AsyncClient(timeout=ML_TEST_TIMEOUT_S) as client:
            resp = await client.get(f"{url}/p/", params={"img": frame_url})
        if resp.status_code != 200:
            err = f"HTTP {resp.status_code}"
        else:
            body = resp.json()
    except Exception as exc:
        err = type(exc).__name__
    ms = int((time.monotonic() - t1) * 1000)
    try:
        fetched = not await redis.exists(key)  # the frame endpoint GETDELs the nonce
    finally:
        await redis.delete(key)
    callback_check = (
        CheckOut(name="callback", ok=True, detail=f"sidecar fetched the frame from {callback}")
        if fetched
        else CheckOut(
            name="callback",
            ok=False,
            detail=f"sidecar did not fetch the frame; it cannot reach {callback}",
        )
    )
    detections = body.get("detections") if isinstance(body, dict) else None
    if isinstance(detections, list):
        inference = CheckOut(
            name="inference", ok=True, detail=f"{len(detections)} detection(s) in {ms} ms"
        )
    elif not fetched:
        inference = CheckOut(name="inference", ok=False, detail=err or "skipped: no frame fetched")
    else:
        inference = CheckOut(name="inference", ok=False, detail=err or "no detections field")
    return [sidecar, callback_check, inference]


@settings_router.post("/test", response_model=TestOut)
async def test_obico_settings(
    payload: TestIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TestOut:
    cfg = await obico.get_config(db, settings)
    url = (payload.url or cfg.url).strip().rstrip("/")
    callback = (payload.callback_url or cfg.callback_url).strip().rstrip("/")
    ver = settings.obico_ml_version.strip() or None
    if not url:
        return TestOut(ok=False, detail="not_configured", ml_version=ver)
    r = await _redis(settings)
    try:
        checks = await _run_checks(r, url, callback)
    finally:
        await r.aclose()
    failed = [c.name for c in checks if not c.ok]
    return TestOut(
        ok=not failed,
        detail="ok" if not failed else "failed: " + ", ".join(failed),
        checks=checks,
        ml_version=ver,
    )


# -- per printer ----------------------------------------------------------


class DetectionSummaryOut(BaseModel):
    printers: dict[int, DetectionStatusOut]


@router.get("/detections/summary", response_model=DetectionSummaryOut)
async def detections_summary(
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DetectionSummaryOut:
    """Live detection status of every printer the caller may print on (one
    query + one MGET), so a dashboard of N cards polls once, not N times."""
    ids = [r for r in (await db.scalars(select(Printer.id))).all()]
    ids = [i for i in ids if _can_print(ctx, i)]
    out: dict[int, DetectionStatusOut] = {}
    if ids:
        cfg = await obico.get_config(db, settings)
        rows = (
            await db.execute(select(Setting).where(Setting.key.like("obico_printer:%")))
        ).scalars()
        on = {
            int(row.key.split(":", 1)[1])
            for row in rows
            if (row.value or {}).get("enabled") is True
        }
        live = cfg.enabled and bool(cfg.url)
        r = await _redis(settings)
        try:
            raws = await r.mget([obico.status_key(i) for i in ids])
        finally:
            await r.aclose()
        for i, raw in zip(ids, raws, strict=True):
            out[i] = _parse_status(raw, live and i in on)
    return DetectionSummaryOut(printers=out)


def _can_print(ctx: AuthContext, printer_id: int) -> bool:
    try:
        ctx.require_printer(printer_id, "print")
    except HTTPException:
        return False
    return True


@router.get("/{printer_id}/detection", response_model=PrinterDetectionOut, dependencies=[_print])
async def get_printer_detection(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PrinterDetectionOut:
    await _get_or_404(db, printer_id)
    cfg = await obico.get_config(db, settings)
    pc = await obico.get_printer(db, printer_id)
    return PrinterDetectionOut(
        **pc.model_dump(),
        status=await _status(settings, printer_id, cfg.enabled and bool(cfg.url) and pc.enabled),
        effective_action=obico.effective_action(cfg, pc),
        global_enabled=cfg.enabled and bool(cfg.url),
    )


@router.put("/{printer_id}/detection", response_model=PrinterDetectionOut, dependencies=[_control])
async def put_printer_detection(
    printer_id: int,
    payload: PrinterDetection,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PrinterDetectionOut:
    await _get_or_404(db, printer_id)
    await obico.put_printer(db, printer_id, payload)
    return await get_printer_detection(printer_id, db, settings)


@router.get("/{printer_id}/detections", response_model=DetectionsOut, dependencies=[_print])
async def list_detections(
    printer_id: int,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DetectionsOut:
    await _get_or_404(db, printer_id)
    rows = (
        await db.execute(
            select(FailureDetection)
            .where(FailureDetection.printer_id == printer_id)
            .order_by(FailureDetection.id.desc())
            .limit(max(1, min(limit, 100)))
        )
    ).scalars()
    return DetectionsOut(
        status=await _status(settings, printer_id),
        events=[
            DetectionOut(
                id=r.id,
                print_job_id=r.print_job_id,
                score=r.score,
                smoothed=r.smoothed,
                action=r.action,
                has_frame=bool(r.frame_blob_key),
                created_at=r.created_at,
            )
            for r in rows
        ],
    )


@router.get("/{printer_id}/detections/{detection_id}/frame", dependencies=[_print])
async def detection_frame(
    printer_id: int,
    detection_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    row = await db.get(FailureDetection, detection_id)
    if row is None or row.printer_id != printer_id or not row.frame_blob_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "frame not found")
    r = await _redis(settings)
    try:
        data = await r.get(row.frame_blob_key)
    finally:
        await r.aclose()
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "frame expired")
    return Response(
        data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"}
    )


def _test_once(settings: Settings, cfg: ObicoConfig, printer_id: int) -> float:
    r = redis_sync.Redis.from_url(settings.redis_url)
    try:
        jpeg = camera_state.wait_for_frame(
            r, printer_id, timeout=12.0, viewer_id=f"obico-test-{printer_id}"
        )
        if jpeg is None:
            raise MlError("no_frame")
        return score_frame(r, cfg, jpeg)
    finally:
        r.close()


@router.post("/{printer_id}/detection/test", response_model=TestScoreOut, dependencies=[_control])
async def test_printer_detection(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TestScoreOut:
    await _get_or_404(db, printer_id)
    cfg = await obico.get_config(db, settings)
    if not cfg.url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Obico URL is not configured")
    try:
        score = await anyio.to_thread.run_sync(_test_once, settings, cfg, printer_id)
    except MlError as exc:
        code = status.HTTP_409_CONFLICT if str(exc) == "no_frame" else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, str(exc)) from None
    return TestScoreOut(score=score)


# -- public one-shot snapshot for the ML sidecar --------------------------


@public_router.get("/frame/{nonce}")
async def cached_frame(nonce: str, settings: Settings = Depends(get_settings)) -> Response:
    """Unauthenticated: the 32-byte nonce is unguessable, single-use (GETDEL)
    and expires in 30 s -- the sidecar has no session."""
    if len(nonce) > 64:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    r = await _redis(settings)
    try:
        data = await r.getdel(obico.nonce_key(nonce))
    finally:
        await r.aclose()
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
