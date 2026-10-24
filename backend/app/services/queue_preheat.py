"""T4 ``queue_preheat`` settings group: one ``settings`` row under key
``"queue_preheat"`` (preheat/soak before a queued print + bed keep-warm)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Setting

PREHEAT_KEY = "queue_preheat"


class QueuePreheatConfig(BaseModel):
    enabled: bool = False
    # filament type (upper-case, e.g. "ABS") -> target in deg C.
    chamber_targets: dict[str, int] = Field(default_factory=dict)
    bed_targets: dict[str, int] = Field(default_factory=dict)
    max_wait_s: int = Field(900, ge=60, le=3600)
    soak_s: int = Field(0, ge=0, le=1800)
    keep_warm_enabled: bool = False
    keep_warm_bed_c: int = Field(45, ge=40, le=110)
    keep_warm_max_min: int = Field(30, ge=5, le=480)

    @field_validator("chamber_targets", "bed_targets")
    @classmethod
    def _norm(cls, v: dict[str, int]) -> dict[str, int]:
        out: dict[str, int] = {}
        for k, t in v.items():
            if not 0 <= t <= 120:
                raise ValueError(f"target for {k} out of range")
            out[k.strip().upper()] = t
        return out


def _cfg(value: dict | None) -> QueuePreheatConfig:
    try:
        return QueuePreheatConfig.model_validate(value or {})
    except ValueError:
        return QueuePreheatConfig()


async def get_preheat_config(db: AsyncSession) -> QueuePreheatConfig:
    row = await db.get(Setting, PREHEAT_KEY)
    return _cfg(row.value if row else None)


def get_preheat_config_sync(session: Session) -> QueuePreheatConfig:
    row = session.get(Setting, PREHEAT_KEY)
    return _cfg(row.value if row else None)


async def set_preheat_config(db: AsyncSession, cfg: QueuePreheatConfig) -> QueuePreheatConfig:
    value = cfg.model_dump()
    row = await db.get(Setting, PREHEAT_KEY)
    if row is None:
        db.add(Setting(key=PREHEAT_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    return cfg
