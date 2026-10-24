"""T7 post-print outcome confirmation: token minting, verdict application,
default-outcome sweep and the plate-cleared tie-in."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Print, PrintArchive, PrintJob
from app.models.enums import ArchiveResult, ArchiveVerdict, PrintJobOrigin, VerdictSource
from app.services import archive_settings
from app.services.server_config import get_server_config_sync

log = logging.getLogger(__name__)

TOKEN_TTL = timedelta(days=7)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def effective_result(arc: PrintArchive) -> ArchiveResult:
    return arc.result_override or arc.result


def sync_run_result(arc: PrintArchive, run: Print | None) -> None:
    """Mirror the effective archive result onto the linked history ``Print``."""
    from app.services.archive import RESULT_TO_PRINT_RESULT

    if run is not None:
        run.result = RESULT_TO_PRINT_RESULT[effective_result(arc)]


def sync_run(session: Session, arc: PrintArchive) -> Print | None:
    return session.scalars(select(Print).where(Print.print_job_id == arc.print_job_id)).first()


def token_gone(arc: PrintArchive) -> bool:
    """True once a confirm token was used or has expired."""
    if arc.confirm_token_used_at is not None:
        return True
    return arc.confirm_expires_at is not None and arc.confirm_expires_at <= datetime.now(UTC)


def apply_verdict(
    arc: PrintArchive,
    run: Print | None,
    verdict: ArchiveVerdict,
    source: VerdictSource,
    user_id: int | None,
) -> None:
    """Record the verdict (caller commits). ``bad`` overrides the result to
    failed when it differs; ``good`` clears a failed override only when a
    prior ``bad`` verdict put it there."""
    prior = arc.user_verdict
    if verdict == ArchiveVerdict.BAD:
        arc.result_override = None if arc.result == ArchiveResult.FAILED else ArchiveResult.FAILED
    elif prior == ArchiveVerdict.BAD and arc.result_override == ArchiveResult.FAILED:
        arc.result_override = None
    arc.user_verdict = verdict
    arc.user_verdict_source = source
    arc.verdict_at = datetime.now(UTC)
    arc.verdict_by_user_id = user_id
    if arc.confirm_token is not None and arc.confirm_token_used_at is None:
        arc.confirm_token_used_at = arc.verdict_at
    sync_run_result(arc, run)


def request_confirm(
    session: Session, settings: Settings, arc: PrintArchive, job: PrintJob, printer_name: str
) -> bool:
    """Mint a token and emit ``print_confirm_request`` when the ``confirm``
    group asks for it. Idempotent per archive. Caller commits afterwards."""
    cfg = archive_settings.get_confirm(session)
    if not cfg.enabled or arc.confirm_requested:
        return False
    if job.origin == PrintJobOrigin.EXTERNAL and not cfg.external_prints:
        return False
    if arc.result == ArchiveResult.CANCELED:
        return False
    token = secrets.token_urlsafe(32)
    arc.confirm_requested = True
    arc.confirm_token = hash_token(token)
    arc.confirm_token_used_at = None
    arc.confirm_expires_at = datetime.now(UTC) + TOKEN_TTL
    session.flush()
    base = (get_server_config_sync(session, settings).public_url or "").rstrip("/")
    root = f"{base}/api/archives/confirm/{token}"
    from app.services.notify.events import emit

    emit(
        "print_confirm_request",
        job.printer_id,
        {
            "subtask_name": job.subtask_name,
            "archive_id": arc.id,
            "job_id": job.id,
            "printer_name": printer_name,
            "good_url": f"{root}/good?tap=1",
            "bad_url": f"{root}/bad?tap=1",
        },
    )
    return True


def apply_defaults(session: Session) -> int:
    """Hourly sweep: pending archives older than ``default_after_h`` get the
    configured default outcome (source ``api``)."""
    cfg = archive_settings.get_confirm(session)
    if not cfg.enabled or cfg.default_outcome is None:
        return 0
    cutoff = datetime.now(UTC) - timedelta(hours=cfg.default_after_h)
    rows = session.scalars(
        select(PrintArchive).where(
            PrintArchive.confirm_requested.is_(True),
            PrintArchive.user_verdict.is_(None),
            PrintArchive.created_at <= cutoff,
        )
    ).all()
    for arc in rows:
        apply_verdict(
            arc,
            sync_run(session, arc),
            ArchiveVerdict(cfg.default_outcome),
            VerdictSource.API,
            None,
        )
    session.commit()
    return len(rows)


def on_plate_cleared(printer_id: int, user_id: int | None) -> None:
    """T3 ``plate_cleared`` subscriber: the printer's latest confirm-pending
    archive is recorded good (source ``plate_clear``) when configured."""
    from app.tasks.base import sync_session

    with sync_session() as session:
        cfg = archive_settings.get_confirm(session)
        if not (cfg.enabled and cfg.default_good_on_plate_clear):
            return
        arc = session.scalars(
            select(PrintArchive)
            .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
            .where(
                PrintJob.printer_id == printer_id,
                PrintArchive.confirm_requested.is_(True),
                PrintArchive.user_verdict.is_(None),
            )
            .order_by(PrintArchive.created_at.desc(), PrintArchive.id.desc())
            .limit(1)
        ).first()
        if arc is None:
            return
        apply_verdict(
            arc, sync_run(session, arc), ArchiveVerdict.GOOD, VerdictSource.PLATE_CLEAR, user_id
        )
        session.commit()


def register_hooks() -> bool:
    """Subscribe ``on_plate_cleared`` to T3's hook registry when present."""
    try:
        from app.services import hooks
    except ImportError:
        return False
    hooks.on_plate_cleared(on_plate_cleared)
    return True
