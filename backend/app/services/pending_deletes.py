"""Best-effort storage deletes that outlive their DB rows.

Deleting the DB row first (so a failed commit never strands rows pointing at
missing bytes) means a failed object delete would leave orphan bytes that the
scanner would re-adopt. Failures are recorded here, retried by the reaper, and
the scanner skips these keys.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.storage import PendingStorageDelete
from app.services.storage_backends import backend_for_id_sync, resolve_default_backend_sync
from app.storage.base import StorageBackend
from app.storage.errors import StorageKeyNotFound

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 50


def delete_collecting(
    targets: Iterable[tuple[StorageBackend, int | None, str]],
) -> list[tuple[int | None, str]]:
    """Delete each ``(backend, backend_id, key)``; return the failures."""
    failed: list[tuple[int | None, str]] = []
    for be, backend_id, key in targets:
        try:
            be.delete(key)
        except StorageKeyNotFound:
            pass
        except Exception:
            log.warning("storage delete failed for %s; queued for retry", key, exc_info=True)
            failed.append((backend_id, key))
    return failed


def record_sync(session: Session, failed: list[tuple[int | None, str]]) -> None:
    if not failed:
        return
    session.add_all(PendingStorageDelete(backend_id=b, key=k) for b, k in failed)
    session.commit()


async def record_async(db, failed: list[tuple[int | None, str]]) -> None:
    if not failed:
        return
    db.add_all(PendingStorageDelete(backend_id=b, key=k) for b, k in failed)
    await db.commit()


def pending_keys(session: Session, keys: Iterable[str]) -> set[str]:
    keys = list(keys)
    if not keys:
        return set()
    return set(
        session.execute(
            select(PendingStorageDelete.key).where(PendingStorageDelete.key.in_(keys))
        ).scalars()
    )


def retry_sync(session: Session, settings: Settings) -> int:
    """Retry queued deletes; returns how many were cleared."""
    cleared = 0
    for row in list(session.execute(select(PendingStorageDelete)).scalars()):
        try:
            if row.backend_id is None:
                be, _ = resolve_default_backend_sync(session, settings)
            else:
                be = backend_for_id_sync(session, settings, row.backend_id)
            with contextlib.suppress(StorageKeyNotFound):
                be.delete(row.key)
        except Exception:
            row.attempts += 1
            if row.attempts >= MAX_ATTEMPTS:
                log.error("giving up deleting %s after %d attempts", row.key, row.attempts)
                session.execute(
                    delete(PendingStorageDelete).where(PendingStorageDelete.id == row.id)
                )
            continue
        session.execute(delete(PendingStorageDelete).where(PendingStorageDelete.id == row.id))
        cleared += 1
    session.commit()
    return cleared
