"""Admin in-app log viewer: tail / filter / search / download the logs every
service ships to Redis (``app.services.log_stream``). Admin only; every line
is redacted again on the way out."""

from __future__ import annotations

from typing import Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from app.api.deps import require_perm
from app.config import Settings, get_settings
from app.services import log_stream as ls

router = APIRouter(
    prefix="/admin/logs", tags=["admin-logs"], dependencies=[Depends(require_perm("admin"))]
)

Level = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogEntryOut(BaseModel):
    id: int
    ts: float
    level: str
    logger: str
    service: str
    msg: str


class LogsOut(BaseModel):
    entries: list[LogEntryOut]  # oldest -> newest
    last_id: int
    services: list[str]
    capacity: int = ls.MAX_ENTRIES


async def _load(
    settings: Settings,
    *,
    service: str | None,
    level: str | None,
    q: str | None,
    after: int | None,
) -> list[dict]:
    r = aioredis.Redis.from_url(settings.redis_url)
    try:
        raw = await r.lrange(ls.LOG_LIST_KEY, 0, ls.MAX_ENTRIES - 1)
    finally:
        await r.aclose()
    entries = ls.filter_entries(
        ls.parse_entries(raw), service=service, level=level, q=q, after=after
    )
    entries.reverse()  # newest-first -> chronological
    return entries


@router.get("", response_model=LogsOut)
async def read_logs(
    service: str | None = Query(None, max_length=40),
    level: Level | None = None,
    q: str | None = Query(None, max_length=200),
    after: int | None = Query(None, ge=0),
    limit: int = Query(300, ge=1, le=ls.MAX_ENTRIES),
    settings: Settings = Depends(get_settings),
) -> LogsOut:
    """Last ``limit`` matching lines; pass ``after=<last_id>`` to follow."""
    entries = await _load(settings, service=service, level=level, q=q, after=after)
    all_services = sorted({e["service"] for e in entries if e.get("service")} | set(ls.SERVICES))
    entries = entries[-limit:]
    return LogsOut(
        entries=[LogEntryOut(**{k: e.get(k) for k in LogEntryOut.model_fields}) for e in entries],
        last_id=max((e["id"] for e in entries), default=after or 0),
        services=all_services,
    )


@router.get("/download")
async def download_logs(
    service: str | None = Query(None, max_length=40),
    level: Level | None = None,
    q: str | None = Query(None, max_length=200),
    settings: Settings = Depends(get_settings),
) -> Response:
    entries = await _load(settings, service=service, level=level, q=q, after=None)
    body = "\n".join(ls.format_entry(e) for e in entries) + "\n"
    return Response(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tdmm-logs.txt"'},
    )
