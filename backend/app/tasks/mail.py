"""Celery task behind ``app.services.mailer.send``. SMTP settings are read
fresh in the worker, so credentials never travel through the broker."""

from __future__ import annotations

import logging

from app.services import mailer
from app.services.server_config import current_sync
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_S = (10, 60, 300)


@celery_app.task(name="app.tasks.mail.send", bind=True, max_retries=MAX_RETRIES)
def send_mail(self, to: str, subject: str, text: str, html: str | None = None) -> bool:
    cfg = current_sync().smtp
    if not cfg.configured:
        log.warning("mail.send skipped: SMTP not configured")
        return False
    try:
        mailer.deliver(cfg, to, subject, text, html)
    except mailer.MailError as exc:
        retries = self.request.retries
        if retries >= MAX_RETRIES:
            log.error("mail.send failed permanently: %s", exc)
            return False
        raise self.retry(exc=exc, countdown=BACKOFF_S[retries]) from exc
    return True
