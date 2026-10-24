"""T7 archive retention purge. Deletes ``print_archives`` rows (and
unreferenced blobs) only -- never ``print_jobs``, ledger or queue rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PrintArchive
from app.models.enums import ArchiveResult
from app.models.library import Blob
from app.services import archive_settings
from app.services.archive_blobs import blob_hashes, delete_archives


def candidates(session: Session, cfg: archive_settings.PurgeConfig) -> list[PrintArchive]:
    cutoff = datetime.now(UTC) - timedelta(days=cfg.older_than_days)
    cond = [
        PrintArchive.created_at < cutoff,
        # never purge a pending confirmation
        ~and_(PrintArchive.confirm_requested.is_(True), PrintArchive.user_verdict.is_(None)),
    ]
    if cfg.keep_failed:
        eff_failed = func.coalesce(PrintArchive.result_override, PrintArchive.result) == (
            ArchiveResult.FAILED
        )
        cond.append(~eff_failed)
    if cfg.keep_with_verdict:
        cond.append(PrintArchive.user_verdict.is_(None))
    return list(session.scalars(select(PrintArchive).where(*cond)).all())


def _bytes(session: Session, rows: list[PrintArchive]) -> int:
    seen: set[str] = set()
    for a in rows:
        seen |= blob_hashes(a)
    total = 0
    for h in seen:
        b = session.get(Blob, h)
        if b is not None:
            total += b.size
    return total


def preview(session: Session, cfg: archive_settings.PurgeConfig) -> tuple[int, int]:
    rows = candidates(session, cfg)
    return len(rows), _bytes(session, rows)


def run_purge(
    session: Session, settings: Settings, cfg: archive_settings.PurgeConfig
) -> tuple[int, int]:
    rows = candidates(session, cfg)
    n, size = len(rows), _bytes(session, rows)
    delete_archives(session, settings, [a.id for a in rows])
    archive_settings.set_purge_stats(
        session,
        archive_settings.PurgeStats(
            last_run_at=datetime.now(UTC).isoformat(), last_deleted=n, last_bytes=size
        ),
    )
    return n, size
