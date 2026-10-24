"""``GET/PUT /settings/queue`` (T3): queue behaviour toggles."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perm
from app.db import get_db
from app.services.queue_settings import QueueConfig, get_queue_config, set_queue_config

router = APIRouter(prefix="/settings/queue", tags=["queue-settings"])


class QueueSettingsIn(QueueConfig):
    pass


@router.get("", response_model=QueueConfig)
async def get_queue_settings(db: AsyncSession = Depends(get_db)) -> QueueConfig:
    return await get_queue_config(db)


@router.put("", response_model=QueueConfig, dependencies=[Depends(require_perm("settings:write"))])
async def put_queue_settings(
    payload: QueueSettingsIn, db: AsyncSession = Depends(get_db)
) -> QueueConfig:
    return await set_queue_config(db, payload.model_dump())
