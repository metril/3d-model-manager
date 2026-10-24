"""T1 ``drying`` settings group: one ``settings`` row under key ``"drying"``."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Setting
from app.services.drying import DEFAULT_PRESETS, DRY_HOURS_RANGE, DRY_TEMP_RANGE

DRYING_KEY = "drying"


class DryingPreset(BaseModel):
    n3f_temp: int = Field(ge=DRY_TEMP_RANGE[0], le=DRY_TEMP_RANGE[1])
    n3s_temp: int = Field(ge=DRY_TEMP_RANGE[0], le=DRY_TEMP_RANGE[1])
    n3f_hours: int = Field(ge=DRY_HOURS_RANGE[0], le=DRY_HOURS_RANGE[1])
    n3s_hours: int = Field(ge=DRY_HOURS_RANGE[0], le=DRY_HOURS_RANGE[1])


def _default_presets() -> dict[str, DryingPreset]:
    return {k: DryingPreset(**v) for k, v in DEFAULT_PRESETS.items()}


class DryingConfig(BaseModel):
    presets: dict[str, DryingPreset] = Field(default_factory=_default_presets)
    ams_humidity_good: int = Field(default=40, ge=0, le=100)
    ams_humidity_fair: int = Field(default=60, ge=0, le=100)
    per_type_thresholds: dict[str, int] = Field(default_factory=dict)
    ambient_drying_enabled: bool = False
    ambient_sustained_min: int = Field(default=30, ge=1, le=1440)
    queue_drying_enabled: bool = False
    queue_drying_block: bool = False
    print_drying_enabled: bool = False

    @model_validator(mode="after")
    def _good_le_fair(self) -> DryingConfig:
        if self.ams_humidity_good > self.ams_humidity_fair:
            raise ValueError("ams_humidity_good must not exceed ams_humidity_fair")
        return self

    def presets_dict(self) -> dict[str, dict[str, int]]:
        return {k: p.model_dump() for k, p in self.presets.items()}

    @field_validator("presets")
    @classmethod
    def _norm_presets(cls, v: dict[str, DryingPreset]) -> dict[str, DryingPreset]:
        return {k.strip().upper(): p for k, p in v.items() if k.strip()}

    @field_validator("per_type_thresholds")
    @classmethod
    def _norm_thresholds(cls, v: dict[str, int]) -> dict[str, int]:
        out: dict[str, int] = {}
        for k, n in v.items():
            if isinstance(n, bool) or not 0 <= n <= 100:
                raise ValueError("per-type thresholds must be 0-100")
            out[k if k == "default" else k.strip().upper()] = n
        return out


def _cfg(value: dict | None) -> DryingConfig:
    try:
        return DryingConfig(**(value or {}))
    except ValueError:  # a corrupt stored row degrades to defaults, never a 500
        return DryingConfig()


async def get_drying_config(db: AsyncSession) -> DryingConfig:
    row = await db.get(Setting, DRYING_KEY)
    return _cfg(row.value if row else None)


def get_drying_config_sync(session: Session) -> DryingConfig:
    row = session.get(Setting, DRYING_KEY)
    return _cfg(row.value if row else None)


async def set_drying_config(db: AsyncSession, patch: dict) -> DryingConfig:
    row = await db.get(Setting, DRYING_KEY)
    value = {**(row.value if row else {}), **patch}
    if row is None:
        db.add(Setting(key=DRYING_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    return _cfg(value)
