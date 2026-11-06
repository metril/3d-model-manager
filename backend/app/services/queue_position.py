"""Shared queue-position allocation guarded by one advisory lock."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.library import PrintQueueEntry

QUEUE_POSITION_LOCK = 0x514D5155  # transaction-scoped; released on commit/rollback


def append_position_sync(session: Session) -> int:
    session.execute(select(func.pg_advisory_xact_lock(QUEUE_POSITION_LOCK)))
    return (session.scalar(select(func.max(PrintQueueEntry.position))) or 0) + 1
