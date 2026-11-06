"""T7 settings groups: ``archive_purge`` and ``confirm`` (one ``settings`` row
each). The purge row also carries the last-run stats, which a PUT preserves."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.services import settings_store as store

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
    return _purge(await store.get_value(db, PURGE_KEY))


def get_purge(session: Session) -> PurgeConfig:
    return _purge(store.get_value_sync(session, PURGE_KEY))


async def set_purge_async(db: AsyncSession, cfg: PurgeConfig) -> PurgeConfig:
    old = await store.get_value(db, PURGE_KEY) or {}
    value = {**{k: v for k, v in old.items() if k in _STAT_FIELDS}, **cfg.model_dump()}
    await store.put_value(db, PURGE_KEY, value)
    await db.commit()
    return cfg


async def get_purge_stats_async(db: AsyncSession) -> PurgeStats:
    v = await store.get_value(db, PURGE_KEY) or {}
    return PurgeStats(**{k: v[k] for k in _STAT_FIELDS if k in v})


def set_purge_stats(session: Session, stats: PurgeStats) -> None:
    old = store.get_value_sync(session, PURGE_KEY) or {}
    store.put_value_sync(session, PURGE_KEY, {**old, **stats.model_dump()})
    session.commit()


async def get_confirm_async(db: AsyncSession) -> ConfirmConfig:
    return _confirm(await store.get_value(db, CONFIRM_KEY))


def get_confirm(session: Session) -> ConfirmConfig:
    return _confirm(store.get_value_sync(session, CONFIRM_KEY))


async def set_confirm_async(db: AsyncSession, cfg: ConfirmConfig) -> ConfirmConfig:
    await store.put_value(db, CONFIRM_KEY, cfg.model_dump())
    await db.commit()
    return cfg
