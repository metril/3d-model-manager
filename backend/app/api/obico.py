"""Q10 Obico failure detection API: global settings (``settings:write``),
per-printer toggle (control) + events (print access), a one-shot test, and the
public single-use snapshot URL the ML sidecar fetches."""

from __future__ import annotations

import json
from datetime import datetime

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
from app.models import FailureDetection, Printer
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


class ObicoConfigIn(ObicoConfig):
    @model_validator(mode="after")
    def _order(self) -> ObicoConfigIn:
        if self.pause < self.warn:
            raise ValueError("pause threshold must be >= warn threshold")
        return self


class TestIn(BaseModel):
    url: str | None = None


class TestOut(BaseModel):
    ok: bool
    detail: str = ""


class DetectionOut(BaseModel):
    id: int
    print_job_id: int | None
    score: float
    smoothed: float
    action: str
    has_frame: bool
    created_at: datetime


class DetectionStatusOut(BaseModel):
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


async def _status(settings: Settings, printer_id: int) -> DetectionStatusOut:
    r = await _redis(settings)
    try:
        raw = await r.get(obico.status_key(printer_id))
    finally:
        await r.aclose()
    return _parse_status(raw)


def _parse_status(raw: str | bytes | None) -> DetectionStatusOut:
    if not raw:
        return DetectionStatusOut()
    try:
        body = json.loads(raw)
    except ValueError:
        return DetectionStatusOut()
    return DetectionStatusOut(
        active=not body.get("paused"),
        state=body.get("state"),
        score=body.get("score"),
        smoothed=body.get("smoothed"),
        detection_paused=bool(body.get("paused")),
    )


# -- global ---------------------------------------------------------------


@settings_router.get("", response_model=ObicoConfig)
async def get_obico_settings(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ObicoConfig:
    return await obico.get_config(db, settings)


@settings_router.put("", response_model=ObicoConfig)
async def put_obico_settings(
    payload: ObicoConfigIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ObicoConfig:
    cfg = ObicoConfig(**payload.model_dump())
    cfg.url = cfg.url.strip().rstrip("/")
    cfg.callback_url = (cfg.callback_url.strip() or settings.obico_callback).rstrip("/")
    await obico.put_config(db, cfg)
    return cfg


@settings_router.post("/test", response_model=TestOut)
async def test_obico_settings(
    payload: TestIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TestOut:
    url = (payload.url or (await obico.get_config(db, settings)).url).rstrip("/")
    if not url:
        return TestOut(ok=False, detail="not_configured")
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S) as client:
            resp = await client.get(f"{url}/hc/")
        return TestOut(ok=resp.status_code == 200, detail=f"HTTP {resp.status_code}")
    except Exception as exc:
        return TestOut(ok=False, detail=type(exc).__name__)


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
        r = await _redis(settings)
        try:
            raws = await r.mget([obico.status_key(i) for i in ids])
        finally:
            await r.aclose()
        for i, raw in zip(ids, raws, strict=True):
            out[i] = _parse_status(raw)
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
        status=await _status(settings, printer_id),
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
