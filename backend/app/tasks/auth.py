"""Hourly sweep of expired 2FA ephemeral tokens and email OTP codes."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete

from app.models import AuthEphemeralToken, UserOtpCode
from app.tasks import base
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.auth.sweep_ephemeral")
def sweep_ephemeral() -> int:
    now = datetime.now(UTC)
    with base.sync_session() as session:
        n = session.execute(
            delete(AuthEphemeralToken).where(AuthEphemeralToken.expires_at < now)
        ).rowcount
        n += session.execute(delete(UserOtpCode).where(UserOtpCode.expires_at < now)).rowcount
        session.commit()
    return n
