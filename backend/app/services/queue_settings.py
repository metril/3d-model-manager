"""T3 ``queue`` settings group: one ``settings`` row under key ``"queue"``."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Setting
from app.printers.pause_eta import heads_up_minutes

QUEUE_KEY = "queue"


class QueueConfig(BaseModel):
    require_plate_clear: bool = False
    # Minutes before a slicer-inserted pause to send the heads-up (1-120).
    pause_heads_up_min: int = Field(default=5, ge=1, le=120)


def _cfg(value: dict | None) -> QueueConfig:
    v = value or {}
    return QueueConfig(
        require_plate_clear=bool(v.get("require_plate_clear", False)),
        pause_heads_up_min=heads_up_minutes(v.get("pause_heads_up_min")),
    )


async def get_queue_config(db: AsyncSession) -> QueueConfig:
    row = await db.get(Setting, QUEUE_KEY)
    return _cfg(row.value if row else None)


def get_queue_config_sync(session: Session) -> QueueConfig:
    row = session.get(Setting, QUEUE_KEY)
    return _cfg(row.value if row else None)


async def set_queue_config(db: AsyncSession, patch: dict) -> QueueConfig:
    row = await db.get(Setting, QUEUE_KEY)
    value = {**(row.value if row else {}), **patch}
    if row is None:
        db.add(Setting(key=QUEUE_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    return _cfg(value)
