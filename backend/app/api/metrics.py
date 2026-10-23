"""Prometheus text metrics (Q7). Authenticated by an API-token bearer (the
``/ext`` token plane) whose owner must be an admin -- NOT the session cookie,
so a scraper needs no login flow."""

from __future__ import annotations

import json

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_token
from app.config import Settings, get_settings
from app.db import get_db
from app.models import ApiToken, Printer, PrintJob, SmartPlug, User
from app.models.library import PrintQueueEntry
from app.models.printing import PrintArchive
from app.printers.base import state_key
from app.services.permissions import load_grants

router = APIRouter(tags=["metrics"])
_ACTIVE = ("queued", "uploading", "starting", "printing", "paused")


def _esc(v: object) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _fmt(name: str, help_: str, kind: str, samples: list[tuple[dict, float]]) -> list[str]:
    out = [f"# HELP {name} {help_}", f"# TYPE {name} {kind}"]
    for labels, value in samples:
        lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
        out.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")
    return out


@router.get("/metrics")
async def metrics(
    token: ApiToken = Depends(require_api_token),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    owner = await db.get(User, token.user_id)
    _, perms, _ = await load_grants(db, owner)
    if "admin" not in perms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "metrics token must belong to an admin")

    printers = (await db.scalars(select(Printer))).all()
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        by_state: dict[str, int] = {}
        raws = await client.mget([state_key(p.id) for p in printers]) if printers else []
        for raw in raws:
            try:
                st = json.loads(raw) if raw else None
            except ValueError:
                st = None
            key = (st or {}).get("gcode_state") or "OFFLINE"
            by_state[key] = by_state.get(key, 0) + 1
    finally:
        await client.aclose()

    jobs = await db.scalar(
        select(func.count()).select_from(PrintJob).where(PrintJob.state.in_(_ACTIVE))
    )
    queue = (
        await db.execute(
            select(PrintQueueEntry.state, func.count()).group_by(PrintQueueEntry.state)
        )
    ).all()
    archives = (
        await db.execute(select(PrintArchive.result, func.count()).group_by(PrintArchive.result))
    ).all()
    plugs = (
        await db.execute(
            select(SmartPlug.printer_id, SmartPlug.last_power_w).where(
                SmartPlug.last_power_w.is_not(None)
            )
        )
    ).all()

    lines: list[str] = []
    lines += _fmt("tdmm_info", "Build info", "gauge", [({"version": settings.app_version}, 1)])
    lines += _fmt(
        "tdmm_printers",
        "Printers by gcode state",
        "gauge",
        [({"state": s}, n) for s, n in sorted(by_state.items())],
    )
    lines += _fmt("tdmm_active_jobs", "Non-terminal print jobs", "gauge", [({}, jobs or 0)])
    lines += _fmt(
        "tdmm_queue_entries",
        "Print queue entries by state",
        "gauge",
        [({"state": str(s.value if hasattr(s, "value") else s)}, n) for s, n in queue],
    )
    lines += _fmt(
        "tdmm_archives",
        "Archived print runs by result",
        "gauge",
        [({"result": str(r.value if hasattr(r, "value") else r)}, n) for r, n in archives],
    )
    lines += _fmt(
        "tdmm_plug_power_watts",
        "Last polled smart plug power",
        "gauge",
        [({"printer_id": pid}, w) for pid, w in plugs],
    )
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
