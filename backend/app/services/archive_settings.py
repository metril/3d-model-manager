"""T7 settings groups: ``archive_purge`` and ``confirm`` (one ``settings`` row
each). The purge row also carries the last-run stats, which a PUT preserves."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Setting

PURGE_KEY = "archive_purge"
CONFIRM_KEY = "confirm"


class PurgeConfig(BaseModel):
    enabled: bool = False
    older_than_days: int = Field(365, ge=1, le=3650)
    keep_failed: bool = False
    keep_with_verdict: bool = True


class ConfirmConfig(BaseModel):
    enabled: bool = False
    external_prints: bool = False
    default_outcome: Literal["good", "bad"] | None = None
    default_after_h: int = Field(24, ge=1, le=720)
    default_good_on_plate_clear: bool = False


class PurgeStats(BaseModel):
    last_run_at: str | None = None
    last_deleted: int = 0
    last_bytes: int = 0


_PURGE_FIELDS = set(PurgeConfig.model_fields)
_STAT_FIELDS = set(PurgeStats.model_fields)


def _purge(value: dict | None) -> PurgeConfig:
    v = value or {}
    return PurgeConfig(**{k: v[k] for k in _PURGE_FIELDS if k in v})


def _confirm(value: dict | None) -> ConfirmConfig:
    v = value or {}
    return ConfirmConfig(**{k: v[k] for k in ConfirmConfig.model_fields if k in v})


async def get_purge_async(db: AsyncSession) -> PurgeConfig:
    row = await db.get(Setting, PURGE_KEY)
    return _purge(row.value if row else None)


def get_purge(session: Session) -> PurgeConfig:
    row = session.get(Setting, PURGE_KEY)
    return _purge(row.value if row else None)


async def set_purge_async(db: AsyncSession, cfg: PurgeConfig) -> PurgeConfig:
    row = await db.get(Setting, PURGE_KEY)
    old = dict(row.value) if row else {}
    value = {**{k: v for k, v in old.items() if k in _STAT_FIELDS}, **cfg.model_dump()}
    if row is None:
        db.add(Setting(key=PURGE_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    return cfg


def get_purge_stats(session: Session) -> PurgeStats:
    row = session.get(Setting, PURGE_KEY)
    v = row.value if row else {}
    return PurgeStats(**{k: v[k] for k in _STAT_FIELDS if k in v})


async def get_purge_stats_async(db: AsyncSession) -> PurgeStats:
    row = await db.get(Setting, PURGE_KEY)
    v = row.value if row else {}
    return PurgeStats(**{k: v[k] for k in _STAT_FIELDS if k in v})


def set_purge_stats(session: Session, stats: PurgeStats) -> None:
    row = session.get(Setting, PURGE_KEY)
    old = dict(row.value) if row else {}
    value = {**old, **stats.model_dump()}
    if row is None:
        session.add(Setting(key=PURGE_KEY, value=value))
    else:
        row.value = value
    session.commit()


async def get_confirm_async(db: AsyncSession) -> ConfirmConfig:
    row = await db.get(Setting, CONFIRM_KEY)
    return _confirm(row.value if row else None)


def get_confirm(session: Session) -> ConfirmConfig:
    row = session.get(Setting, CONFIRM_KEY)
    return _confirm(row.value if row else None)


async def set_confirm_async(db: AsyncSession, cfg: ConfirmConfig) -> ConfirmConfig:
    row = await db.get(Setting, CONFIRM_KEY)
    if row is None:
        db.add(Setting(key=CONFIRM_KEY, value=cfg.model_dump()))
    else:
        row.value = cfg.model_dump()
    await db.commit()
    return cfg
