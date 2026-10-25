"""Obico failure-detection config + Redis keys (Q10). Global config lives in
the ``settings`` row ``obico``; per-printer in ``obico_printer:{id}`` (no
printers-table change). Sync twins for printerd, async for the API."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Setting

Action = Literal["none", "notify", "pause"]
GLOBAL_KEY = "obico"
SEEDED_META = "_seeded_from_env"
ENV_FIELDS = {"url": "TDMM_OBICO_URL", "callback_url": "TDMM_OBICO_CALLBACK"}
STATUS_TTL_S = 60
FRAME_NONCE_TTL_S = 30
EVENT_FRAME_TTL_S = 7 * 86400
DEFAULT_URL = "http://obico-ml:3333"
DEFAULT_CALLBACK = "http://api:8080"


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


def _global(raw: dict | None, settings: Settings | None = None) -> ObicoConfig:
    """Resolve the global row. Empty/missing url + callback_url take the
    default; the environment is read ONLY by ``seed_obico_config``."""
    data = dict(raw or {})
    if not str(data.get("url") or "").strip():
        data["url"] = DEFAULT_URL
    if not str(data.get("callback_url") or "").strip():
        data["callback_url"] = DEFAULT_CALLBACK
    try:
        cfg = ObicoConfig(**data)
    except ValueError:
        cfg = ObicoConfig(url=data["url"], callback_url=data["callback_url"])
    cfg.url = cfg.url.rstrip("/")
    cfg.callback_url = cfg.callback_url.rstrip("/")
    return cfg


def _seed_values() -> tuple[str, str]:
    """(url, callback) from env (``TDMM_OBICO_URL``/``TDMM_OBICO_CALLBACK``,
    seed-only) else the defaults."""
    url = os.environ.get("TDMM_OBICO_URL", "").strip().rstrip("/")
    cb = os.environ.get("TDMM_OBICO_CALLBACK", "").strip().rstrip("/")
    return url or DEFAULT_URL, cb or DEFAULT_CALLBACK


def _env_set(field: str) -> bool:
    return bool(os.environ.get(ENV_FIELDS[field], "").strip())


def _seeded(raw: dict | None) -> dict | None:
    """The row value after seeding, or ``None`` when nothing needs writing:
    row absent -> full ObicoConfig; row present -> only a missing/empty
    ``url``/``callback_url`` is filled (``enabled`` etc. never touched). Fields
    taken from the environment are recorded under ``_seeded_from_env``."""
    url, cb = _seed_values()
    if raw is None:
        out = ObicoConfig(url=url, callback_url=cb).model_dump()
        meta = {f: e for f, e in ENV_FIELDS.items() if _env_set(f)}
        if meta:
            out[SEEDED_META] = meta
        return out
    out = dict(raw)
    changed = False
    meta = dict(out.get(SEEDED_META) or {})
    if not str(out.get("url") or "").strip():
        out["url"], changed = url, True
        if _env_set("url"):
            meta["url"] = ENV_FIELDS["url"]
    if not str(out.get("callback_url") or "").strip():
        out["callback_url"], changed = cb, True
        if _env_set("callback_url"):
            meta["callback_url"] = ENV_FIELDS["callback_url"]
    if meta:
        out[SEEDED_META] = meta
    return out if changed else None


async def seed_obico_config(db: AsyncSession, settings: Settings | None = None) -> bool:
    row = await db.get(Setting, GLOBAL_KEY)
    value = _seeded(row.value if row else None)
    if value is None:
        return False
    if row is None:
        await db.execute(
            pg_insert(Setting)
            .values(key=GLOBAL_KEY, value=value)
            .on_conflict_do_nothing(index_elements=["key"])
        )
    else:
        row.value = value
    await db.commit()
    return True


def seed_obico_config_sync(session: Session, settings: Settings | None = None) -> bool:
    row = session.get(Setting, GLOBAL_KEY)
    value = _seeded(row.value if row else None)
    if value is None:
        return False
    if row is None:
        session.execute(
            pg_insert(Setting)
            .values(key=GLOBAL_KEY, value=value)
            .on_conflict_do_nothing(index_elements=["key"])
        )
    else:
        row.value = value
    session.commit()
    return True


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


def _meta(raw: dict | None) -> dict[str, str]:
    m = (raw or {}).get(SEEDED_META)
    return {k: v for k, v in m.items() if k in ENV_FIELDS} if isinstance(m, dict) else {}


async def seeded_from_env(db: AsyncSession) -> dict[str, str]:
    """{field: ENV_NAME} for fields still holding the env-seeded value."""
    row = await db.get(Setting, GLOBAL_KEY)
    return _meta(row.value if row else None)


async def put_config(db: AsyncSession, cfg: ObicoConfig) -> None:
    row = await db.get(Setting, GLOBAL_KEY)
    old = row.value if row else {}
    keep = {f: e for f, e in _meta(old).items() if getattr(cfg, f) == old.get(f)}
    value = cfg.model_dump()
    if keep:
        value[SEEDED_META] = keep
    await _put(db, GLOBAL_KEY, value)


async def put_printer(db: AsyncSession, printer_id: int, pc: PrinterDetection) -> None:
    await _put(db, printer_key(printer_id), pc.model_dump())
