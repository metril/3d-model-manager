"""Q10 Obico: ``record_event`` persists a threshold-crossing the printerd
watcher enqueued (the watcher never touches the DB); ``prune`` is the daily
retention sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import redis
from sqlalchemy import delete, select, update

from app.config import get_settings
from app.models import FailureDetection
from app.models.printing import Printer
from app.tasks import base
from app.tasks.celery_app import celery_app

KEEP_FRAMES = 50
RETENTION_DAYS = 30


@celery_app.task(name="app.tasks.obico.record_event")
def record_event(
    printer_id: int,
    job_id: int | None,
    score: float,
    smoothed: float,
    action: str,
    frame_key: str | None,
) -> int | None:
    r = redis.Redis.from_url(get_settings().redis_url)
    try:
        with base.sync_session() as s:
            if s.get(Printer, printer_id) is None:
                if frame_key:
                    r.delete(frame_key)
                return None
            row = FailureDetection(
                printer_id=printer_id,
                print_job_id=job_id,
                score=score,
                smoothed=smoothed,
                action=action,
                frame_blob_key=frame_key,
            )
            s.add(row)
            s.flush()
            # Cap stored frames: only the newest KEEP_FRAMES keep a frame.
            old = s.execute(
                select(FailureDetection.id, FailureDetection.frame_blob_key)
                .where(
                    FailureDetection.printer_id == printer_id,
                    FailureDetection.frame_blob_key.is_not(None),
                )
                .order_by(FailureDetection.id.desc())
                .offset(KEEP_FRAMES)
            ).all()
            if old:
                for _id, key in old:
                    r.delete(key)
                s.execute(
                    update(FailureDetection)
                    .where(FailureDetection.id.in_([i for i, _ in old]))
                    .values(frame_blob_key=None)
                )
            s.commit()
            return row.id
    finally:
        r.close()


@celery_app.task(name="app.tasks.obico.prune")
def prune() -> int:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    r = redis.Redis.from_url(get_settings().redis_url)
    try:
        with base.sync_session() as s:
            keys = s.execute(
                select(FailureDetection.frame_blob_key).where(
                    FailureDetection.created_at < cutoff,
                    FailureDetection.frame_blob_key.is_not(None),
                )
            ).scalars()
            for key in keys:
                r.delete(key)
            res = s.execute(delete(FailureDetection).where(FailureDetection.created_at < cutoff))
            s.commit()
            return res.rowcount or 0
    finally:
        r.close()
