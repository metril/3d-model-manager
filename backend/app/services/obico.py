"""Obico failure-detection config + Redis keys (Q10). Global config lives in
the ``settings`` row ``obico``; per-printer in ``obico_printer:{id}`` (no
printers-table change). Sync twins for printerd, async for the API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Setting

Action = Literal["none", "notify", "pause"]
GLOBAL_KEY = "obico"
STATUS_TTL_S = 60
FRAME_NONCE_TTL_S = 30
EVENT_FRAME_TTL_S = 7 * 86400


class ObicoConfig(BaseModel):
    url: str = ""
    enabled: bool = False
    interval_s: int = Field(default=10, ge=2, le=300)
    warn: float = Field(default=0.38, ge=0.0, le=5.0)
    pause: float = Field(default=0.78, ge=0.0, le=5.0)
    action: Action = "notify"
    callback_url: str = ""


class PrinterDetection(BaseModel):
    enabled: bool = False
    action: Action | None = None  # None = follow the global default


def printer_key(printer_id: int) -> str:
    return f"obico_printer:{printer_id}"


def status_key(printer_id: int) -> str:
    return f"printer:{printer_id}:obico"


def nonce_key(nonce: str) -> str:
    return f"obico:frame:{nonce}"


def event_frame_key(detection_id: int) -> str:
    return f"obico:evt:{detection_id}"


def _global(raw: dict | None, settings: Settings) -> ObicoConfig:
    data = dict(raw or {})
    data.setdefault("url", settings.obico_url or "")
    data.setdefault("callback_url", settings.obico_callback)
    try:
        cfg = ObicoConfig(**data)
    except ValueError:
        cfg = ObicoConfig(url=data["url"], callback_url=data["callback_url"])
    cfg.url = cfg.url.rstrip("/")
    cfg.callback_url = (cfg.callback_url or settings.obico_callback).rstrip("/")
    return cfg


def _printer(raw: dict | None) -> PrinterDetection:
    try:
        return PrinterDetection(**(raw or {}))
    except ValueError:
        return PrinterDetection()


def effective_action(cfg: ObicoConfig, pc: PrinterDetection) -> str:
    return pc.action or cfg.action


def get_config_sync(session: Session, settings: Settings) -> ObicoConfig:
    row = session.get(Setting, GLOBAL_KEY)
    return _global(row.value if row else None, settings)


def get_printer_sync(session: Session, printer_id: int) -> PrinterDetection:
    row = session.get(Setting, printer_key(printer_id))
    return _printer(row.value if row else None)


def printer_configs_sync(session: Session) -> dict[int, PrinterDetection]:
    out: dict[int, PrinterDetection] = {}
    rows = session.execute(select(Setting).where(Setting.key.like("obico_printer:%"))).scalars()
    for row in rows:
        try:
            out[int(row.key.split(":", 1)[1])] = _printer(row.value)
        except ValueError:
            continue
    return out


async def get_config(db: AsyncSession, settings: Settings) -> ObicoConfig:
    row = await db.get(Setting, GLOBAL_KEY)
    return _global(row.value if row else None, settings)


async def get_printer(db: AsyncSession, printer_id: int) -> PrinterDetection:
    row = await db.get(Setting, printer_key(printer_id))
    return _printer(row.value if row else None)


async def _put(db: AsyncSession, key: str, value: dict) -> None:
    row = await db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value  # fresh dict: JSONB mutation tracking
    await db.commit()


async def put_config(db: AsyncSession, cfg: ObicoConfig) -> None:
    await _put(db, GLOBAL_KEY, cfg.model_dump())


async def put_printer(db: AsyncSession, printer_id: int, pc: PrinterDetection) -> None:
    await _put(db, printer_key(printer_id), pc.model_dump())
