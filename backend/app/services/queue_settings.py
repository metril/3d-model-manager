"""T3 ``queue`` settings group: one ``settings`` row under key ``"queue"``."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Setting

QUEUE_KEY = "queue"


class QueueConfig(BaseModel):
    require_plate_clear: bool = False


def _cfg(value: dict | None) -> QueueConfig:
    v = value or {}
    return QueueConfig(require_plate_clear=bool(v.get("require_plate_clear", False)))


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
