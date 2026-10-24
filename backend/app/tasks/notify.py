"""Q6 notification delivery. ``deliver_pending`` (beat, every 10 s) drains the
Redis list ``notify:queue`` and fans each event out to subscribed channels as
``send_one`` tasks (3 retries with backoff, final outcome logged);
``digest`` (beat, every minute) flushes digest channels on their cron."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import redis
from sqlalchemy import select

from app.config import get_settings
from app.models.notify import NotificationChannel, NotificationLog, NotificationTemplate
from app.models.printing import Printer
from app.services.notify import Message, ProviderError
from app.services.notify.delivery import (
    cron_due,
    fetch_photo,
    in_quiet_hours,
    send_message,
)
from app.services.notify.events import QUEUE_KEY
from app.services.notify.templates import base_context, render
from app.tasks import base
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)

DRAIN_INTERVAL_S = 10
DRAIN_BATCH = 200
MAX_RETRIES = 3
BACKOFF_S = (5, 30, 120)
DIGEST_LAST_KEY = "notify:digest:last:{}"


def build_message(session, event: str, printer_id: int | None, payload: dict) -> tuple[str, str]:
    printer = session.get(Printer, printer_id) if printer_id else None
    tpl = session.scalar(select(NotificationTemplate).where(NotificationTemplate.event == event))
    ctx = base_context(event, printer_id, printer.name if printer else None, payload)
    return render(event, tpl.title_tpl if tpl else None, tpl.body_tpl if tpl else None, ctx)


def _log(session, channel_id, event, printer_id, status, error=None, payload=None) -> None:
    session.add(
        NotificationLog(
            channel_id=channel_id,
            event=event,
            printer_id=printer_id,
            status=status,
            error=error,
            payload=payload,
        )
    )
    session.commit()


PROCESSING_KEY = "notify:processing"


@celery_app.task(name="app.tasks.notify.deliver_pending")
def deliver_pending() -> int:
    """Items move queue -> processing (LMOVE) and are only removed once their
    fan-out was handed to Celery, so a crash/DB error never loses them: the
    next run first moves any leftover processing items back to the queue."""
    r = redis.Redis.from_url(get_settings().redis_url)
    try:
        while r.lmove(PROCESSING_KEY, QUEUE_KEY, "RIGHT", "LEFT") is not None:
            pass  # recover leftovers, order preserved
        claimed: list[tuple[bytes, dict]] = []
        for _ in range(DRAIN_BATCH):
            raw = r.lmove(QUEUE_KEY, PROCESSING_KEY, "LEFT", "RIGHT")
            if raw is None:
                break
            try:
                claimed.append((raw, json.loads(raw)))
            except ValueError:
                r.lrem(PROCESSING_KEY, 1, raw)  # unparseable: drop
        if not claimed:
            return 0
        with base.sync_session() as s:
            channels = list(
                s.scalars(select(NotificationChannel).where(NotificationChannel.enabled))
            )
        dispatched = 0
        for raw, it in claimed:
            for c in channels:
                if it.get("event") in (c.events or []):
                    send_one.apply_async(
                        args=[c.id, it["event"], it.get("printer_id"), it.get("payload") or {}]
                    )
                    dispatched += 1
            r.lrem(PROCESSING_KEY, 1, raw)
        return dispatched
    finally:
        r.close()


@celery_app.task(name="app.tasks.notify.send_one", bind=True, max_retries=MAX_RETRIES)
def send_one(self, channel_id: int, event: str, printer_id: int | None, payload: dict) -> str:
    """Returns the logged status (sent|failed|queued|quiet|skipped)."""
    with base.sync_session() as s:
        channel = s.get(NotificationChannel, channel_id)
        if channel is None or not channel.enabled:
            return "skipped"
        if in_quiet_hours(channel.quiet_hours):
            _log(s, channel.id, event, printer_id, "quiet", payload=payload)
            return "quiet"
        title, body = build_message(s, event, printer_id, payload)
        if channel.digest_cron:
            _log(
                s,
                channel.id,
                event,
                printer_id,
                "queued",
                payload={**payload, "_title": title, "_body": body},
            )
            return "queued"
        error: str | None = None
        try:
            photo = fetch_photo(printer_id, payload) if channel.attach_photo else None
            send_message(channel, Message(title, body, event, photo))
        except ProviderError as exc:
            error = str(exc)
            failure: Exception = exc
        except Exception as exc:  # unexpected: type only, text could echo secrets
            error = type(exc).__name__
            failure = exc
        if error is not None:
            retries = self.request.retries
            if retries < MAX_RETRIES:
                raise self.retry(exc=failure, countdown=BACKOFF_S[retries]) from failure
            _log(s, channel.id, event, printer_id, "failed", error=error, payload=payload)
            return "failed"
        _log(s, channel.id, event, printer_id, "sent", payload=payload)
        return "sent"


@celery_app.task(name="app.tasks.notify.digest")
def digest(now: datetime | None = None) -> int:
    """Send one digest per due channel; returns how many were sent."""
    now = now or datetime.now(UTC)
    sent = 0
    r = redis.Redis.from_url(get_settings().redis_url)
    try:
        with base.sync_session() as s:
            channels = list(
                s.scalars(
                    select(NotificationChannel).where(
                        NotificationChannel.enabled, NotificationChannel.digest_cron.is_not(None)
                    )
                )
            )
            for ch in channels:
                key = DIGEST_LAST_KEY.format(ch.id)
                raw = r.get(key)
                last = datetime.fromisoformat(raw.decode()) if raw else ch.created_at
                try:
                    if not cron_due(ch.digest_cron or "", last, now):
                        continue
                except Exception:
                    continue
                r.set(key, now.isoformat())
                rows = list(
                    s.scalars(
                        select(NotificationLog)
                        .where(
                            NotificationLog.channel_id == ch.id, NotificationLog.status == "queued"
                        )
                        .order_by(NotificationLog.id)
                    )
                )
                if not rows:
                    continue
                lines = [
                    f"- {row.sent_at:%H:%M} {(row.payload or {}).get('_title', row.event)}"
                    for row in rows
                ]
                msg = Message(f"Digest: {len(rows)} notifications", "\n".join(lines), "digest")
                try:
                    send_message(ch, msg)
                except ProviderError as exc:
                    _log(s, ch.id, "digest", None, "failed", error=str(exc))
                    continue
                for row in rows:
                    row.status = "digested"
                s.commit()
                _log(s, ch.id, "digest", None, "sent", payload={"count": len(rows)})
                sent += 1
    finally:
        r.close()
    return sent
