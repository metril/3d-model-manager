"""Multi-backend storage CRUD + resolution (Workstream C task C1 design
spec: "run MULTIPLE storage backends at once; each file's bytes live on one
PRIMARY backend").

CRUD lives here over the ``storage_backends``/``file_locations`` tables
(``app.models.storage``); this module owns the single-default invariant (a
partial unique index on ``storage_backends.is_default`` backs it at the DB
level -- ``set_default_backend`` flips it atomically in one statement) and
the delete guardrails (refuse to delete the last backend, the default
backend, or one any ``file_locations`` row still points at).

Secrets are Fernet-encrypted at rest via the SAME
``app.services.storage_config.encrypt_config_secret``/``decrypt_config_row``
seam the legacy single-backend ``settings`` row already used -- this module
never invents a second encryption path. Every CRUD getter returns the row
AS STORED (encrypted); callers that need the live config
(``backend_for_id``/``resolve_default_backend``/``resolve_backend_for_file``
below, or an API response) must explicitly decrypt/parse it, same as
``app.services.storage_config`` always required.

Async functions serve the API; sync twins serve Celery worker task bodies
(``app.tasks.base.sync_session``), mirroring the split ``app.tasks.base``
and ``app.services.storage_config`` already establish.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import File, FileLocation, StorageBackendRow
from app.services.storage_config import decrypt_config_row, encrypt_config_secret
from app.storage.base import StorageBackend
from app.storage.config import StorageConfig, parse_storage_config
from app.storage.registry import get_backend

# -- async: API-side ---------------------------------------------------


async def list_backends(db: AsyncSession) -> list[StorageBackendRow]:
    result = await db.execute(select(StorageBackendRow).order_by(StorageBackendRow.id))
    return list(result.scalars().all())


async def get_backend_row(db: AsyncSession, backend_id: int) -> StorageBackendRow:
    row = await db.get(StorageBackendRow, backend_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"storage backend {backend_id} not found")
    return row


async def get_default_backend_row(db: AsyncSession) -> StorageBackendRow | None:
    result = await db.execute(
        select(StorageBackendRow).where(StorageBackendRow.is_default.is_(True))
    )
    return result.scalar_one_or_none()


async def get_default_backend(db: AsyncSession) -> StorageBackendRow:
    row = await get_default_backend_row(db)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no default storage backend configured")
    return row


async def create_backend(
    db: AsyncSession,
    settings: Settings,
    name: str,
    config: StorageConfig,
    *,
    is_default: bool = False,
) -> StorageBackendRow:
    row = StorageBackendRow(
        name=name, scheme=config.backend, config=encrypt_config_secret(settings, config)
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    if is_default:
        row = await set_default_backend(db, row.id)
    return row


async def update_backend(
    db: AsyncSession,
    settings: Settings,
    backend_id: int,
    *,
    name: str | None = None,
    config: StorageConfig | None = None,
) -> StorageBackendRow:
    row = await get_backend_row(db, backend_id)
    if name is not None:
        row.name = name
    if config is not None:
        row.scheme = config.backend
        row.config = encrypt_config_secret(settings, config)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_backend(db: AsyncSession, backend_id: int) -> None:
    """Refuse to delete a backend that's the last one, the default one, or
    one any ``file_locations`` row still references -- deleting it out from
    under a file with bytes still physically there would orphan them with no
    way to read them back.
    """
    row = await get_backend_row(db, backend_id)
    total = (await db.execute(select(func.count()).select_from(StorageBackendRow))).scalar_one()
    if total <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot delete the last storage backend")
    if row.is_default:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "cannot delete the default storage backend; set another one as default first",
        )
    in_use = (
        await db.execute(
            select(func.count())
            .select_from(FileLocation)
            .where(FileLocation.backend_id == backend_id)
        )
    ).scalar_one()
    if in_use:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"storage backend {backend_id} still holds {in_use} file(s); relocate them first",
        )
    await db.delete(row)
    await db.commit()


async def set_default_backend(db: AsyncSession, backend_id: int) -> StorageBackendRow:
    """Flip the single-default invariant with a clear-then-set pair inside one
    transaction: first drop the current default, then set the target.

    NOT a single ``UPDATE ... SET is_default=(id==:id)``: the partial unique
    index ``uq_storage_backends_is_default`` is NON-deferrable, so a multi-row
    UPDATE checks uniqueness per row as it scans. If the target row is set
    ``true`` before the old default is set ``false`` (which happens whenever
    the target's id sorts before the current default's), two ``true`` rows
    momentarily coexist and Postgres raises a duplicate-key error. Two
    statements never hold two ``true`` rows at any instant.
    """
    row = await get_backend_row(db, backend_id)
    await db.execute(
        update(StorageBackendRow)
        .where(StorageBackendRow.is_default.is_(True))
        .values(is_default=False)
    )
    await db.execute(
        update(StorageBackendRow).where(StorageBackendRow.id == backend_id).values(is_default=True)
    )
    await db.commit()
    await db.refresh(row)
    return row


async def backend_for_id(db: AsyncSession, settings: Settings, backend_id: int) -> StorageBackend:
    row = await get_backend_row(db, backend_id)
    data, _ = decrypt_config_row(settings, dict(row.config))
    return get_backend(settings, parse_storage_config(data))


async def _ensure_default_backend_row(db: AsyncSession, settings: Settings) -> StorageBackendRow:
    """Self-heal: ``storage_backends`` should never be empty once the app has
    been migrated (the migration itself seeds exactly one default row) --
    an empty table here means something wiped it out from under a running
    install, or (in tests) the autouse truncation fixture between test
    functions. Every C2 write call site (``ingest``/``importing``/
    ``library``/the scanner) needs a REAL row to stamp ``files.backend_id``/
    ``file_locations.backend_id`` against, so -- unlike
    ``storage_config.get_active_config``'s read-only shim, which can just
    return an ad hoc ``LocalConfig()`` -- this must actually persist one.
    Reuses ``storage_config.get_active_config`` (same legacy-``settings``-row-
    or-``LocalConfig()`` fallback the shim already implements) as the
    source config, so a still-configured legacy install carries its real
    backend across rather than silently reverting to local disk.
    """
    row = await get_default_backend_row(db)
    if row is not None:
        return row
    from app.services.storage_config import get_active_config

    config = await get_active_config(db, settings)
    # One INSERT that is already the default (not create-then-promote): two
    # concurrent self-heals race on the partial unique index, and the loser
    # simply adopts the winner's row instead of leaving a stray non-default one.
    row = StorageBackendRow(
        name="Default",
        scheme=config.backend,
        config=encrypt_config_secret(settings, config),
        is_default=True,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await get_default_backend_row(db)
        if existing is None:
            raise
        return existing
    await db.refresh(row)
    return row


async def resolve_default_backend(
    db: AsyncSession, settings: Settings
) -> tuple[StorageBackend, int]:
    """The write target: the default backend + its id (for stamping
    ``files.backend_id``/inserting a ``file_locations`` row). Self-heals a
    missing default row first (see ``_ensure_default_backend_row``).
    """
    row = await _ensure_default_backend_row(db, settings)
    data, _ = decrypt_config_row(settings, dict(row.config))
    return get_backend(settings, parse_storage_config(data)), row.id


async def resolve_backend_for_file(
    db: AsyncSession, settings: Settings, file: File
) -> StorageBackend:
    """The read source for ``file``: its own primary backend, or the
    default when ``backend_id`` is NULL (pre-migration-seed safety net --
    should not happen post-seed, but a NULL read must never explode).
    """
    if file.backend_id is not None:
        return await backend_for_id(db, settings, file.backend_id)
    backend, _ = await resolve_default_backend(db, settings)
    return backend


# -- sync: worker-side ---------------------------------------------------


def list_backends_sync(session: Session) -> list[StorageBackendRow]:
    result = session.execute(select(StorageBackendRow).order_by(StorageBackendRow.id))
    return list(result.scalars().all())


def get_backend_row_sync(session: Session, backend_id: int) -> StorageBackendRow:
    row = session.get(StorageBackendRow, backend_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"storage backend {backend_id} not found")
    return row


def get_default_backend_row_sync(session: Session) -> StorageBackendRow | None:
    result = session.execute(
        select(StorageBackendRow).where(StorageBackendRow.is_default.is_(True))
    )
    return result.scalar_one_or_none()


def get_default_backend_sync(session: Session) -> StorageBackendRow:
    row = get_default_backend_row_sync(session)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no default storage backend configured")
    return row


def create_backend_sync(
    session: Session,
    settings: Settings,
    name: str,
    config: StorageConfig,
    *,
    is_default: bool = False,
) -> StorageBackendRow:
    row = StorageBackendRow(
        name=name, scheme=config.backend, config=encrypt_config_secret(settings, config)
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    if is_default:
        row = set_default_backend_sync(session, row.id)
    return row


def update_backend_sync(
    session: Session,
    settings: Settings,
    backend_id: int,
    *,
    name: str | None = None,
    config: StorageConfig | None = None,
) -> StorageBackendRow:
    row = get_backend_row_sync(session, backend_id)
    if name is not None:
        row.name = name
    if config is not None:
        row.scheme = config.backend
        row.config = encrypt_config_secret(settings, config)
    session.commit()
    session.refresh(row)
    return row


def delete_backend_sync(session: Session, backend_id: int) -> None:
    row = get_backend_row_sync(session, backend_id)
    total = session.execute(select(func.count()).select_from(StorageBackendRow)).scalar_one()
    if total <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot delete the last storage backend")
    if row.is_default:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "cannot delete the default storage backend; set another one as default first",
        )
    in_use = session.execute(
        select(func.count()).select_from(FileLocation).where(FileLocation.backend_id == backend_id)
    ).scalar_one()
    if in_use:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"storage backend {backend_id} still holds {in_use} file(s); relocate them first",
        )
    session.delete(row)
    session.commit()


def set_default_backend_sync(session: Session, backend_id: int) -> StorageBackendRow:
    # Clear-then-set (see set_default_backend): the partial unique index is
    # non-deferrable, so a single SET is_default=(id==:id) can transiently
    # hold two true rows and 500 on a duplicate key.
    row = get_backend_row_sync(session, backend_id)
    session.execute(
        update(StorageBackendRow)
        .where(StorageBackendRow.is_default.is_(True))
        .values(is_default=False)
    )
    session.execute(
        update(StorageBackendRow).where(StorageBackendRow.id == backend_id).values(is_default=True)
    )
    session.commit()
    session.refresh(row)
    return row


def backend_for_id_sync(session: Session, settings: Settings, backend_id: int) -> StorageBackend:
    row = get_backend_row_sync(session, backend_id)
    data, _ = decrypt_config_row(settings, dict(row.config))
    return get_backend(settings, parse_storage_config(data))


def _ensure_default_backend_row_sync(session: Session, settings: Settings) -> StorageBackendRow:
    """Sync twin of ``_ensure_default_backend_row`` (worker world)."""
    row = get_default_backend_row_sync(session)
    if row is not None:
        return row
    from app.services.storage_config import get_active_config_sync

    config = get_active_config_sync(session, settings)
    return create_backend_sync(session, settings, "Default", config, is_default=True)


def resolve_default_backend_sync(
    session: Session, settings: Settings
) -> tuple[StorageBackend, int]:
    """Sync twin of ``resolve_default_backend`` -- self-heals a missing
    default row first (see ``_ensure_default_backend_row``).
    """
    row = _ensure_default_backend_row_sync(session, settings)
    data, _ = decrypt_config_row(settings, dict(row.config))
    return get_backend(settings, parse_storage_config(data)), row.id


def resolve_backend_for_file_sync(
    session: Session, settings: Settings, file: File
) -> StorageBackend:
    if file.backend_id is not None:
        return backend_for_id_sync(session, settings, file.backend_id)
    backend, _ = resolve_default_backend_sync(session, settings)
    return backend
