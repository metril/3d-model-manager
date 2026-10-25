"""Library trash / recycle bin: soft-delete models, restore, purge, and the
retention auto-purge. A trashed ``Model`` keeps its row, revisions and files
(blobs stay in storage) until purged; every library query excludes rows with
``deleted_at`` set. Purging reuses ``library.bulk_hard_delete_models`` (async,
API side) / ``purge_model_sync`` (worker side), which physically remove the
files from every storage backend.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from app.api.deps import AuthContext, require_owner_or_admin
from app.config import Settings
from app.models import AuditLog, User
from app.models.library import Blob, File, Model, Revision
from app.models.storage import FileLocation
from app.schemas.trash import TrashItemOut, TrashListOut
from app.services import layout, library
from app.services.app_config import get_app_config, get_app_config_sync
from app.services.storage_backends import (
    backend_for_id_sync,
    resolve_backend_for_file_sync,
    resolve_default_backend_sync,
)
from app.storage.base import StorageBackend
from app.storage.errors import StorageKeyNotFound

log = logging.getLogger(__name__)


def _audit(db, ctx: AuthContext | None, action: str, payload: dict) -> None:
    uid = ctx.user.id if ctx else None
    db.add(AuditLog(user_id=uid, created_by_user_id=uid, action=action, payload=payload))


async def _load(db: AsyncSession, ids: list[int], *, trashed: bool) -> list[Model]:
    unique = list(dict.fromkeys(ids))
    rows = {
        m.id: m for m in (await db.execute(select(Model).where(Model.id.in_(unique)))).scalars()
    }
    bad = [i for i in unique if i not in rows or (rows[i].deleted_at is not None) != trashed]
    if bad:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"model(s) not found: {sorted(bad)}")
    return [rows[i] for i in unique]


async def move_to_trash(db: AsyncSession, ctx: AuthContext, ids: list[int]) -> int:
    models = await _load(db, ids, trashed=False)
    for m in models:
        require_owner_or_admin(ctx, m.created_by_user_id)
    now = datetime.now(UTC)
    for m in models:
        m.deleted_at = now
        m.deleted_by_user_id = ctx.user.id
    if models:
        _audit(
            db,
            ctx,
            "model.trash",
            {"model_ids": [m.id for m in models], "slugs": [m.slug for m in models]},
        )
    await db.commit()
    return len(models)


async def restore(db: AsyncSession, ctx: AuthContext, ids: list[int]) -> int:
    models = await _load(db, ids, trashed=True)
    for m in models:
        require_owner_or_admin(ctx, m.created_by_user_id)
    for m in models:
        m.deleted_at = None
        m.deleted_by_user_id = None
    if models:
        _audit(db, ctx, "model.restore", {"model_ids": [m.id for m in models]})
    await db.commit()
    return len(models)


async def purge(
    db: AsyncSession,
    backend: StorageBackend,
    settings: Settings,
    ctx: AuthContext,
    ids: list[int],
) -> int:
    models = await _load(db, ids, trashed=True)
    for m in models:
        require_owner_or_admin(ctx, m.created_by_user_id)
    info = {"model_ids": [m.id for m in models], "slugs": [m.slug for m in models]}
    n = await library.bulk_hard_delete_models(db, backend, settings, ids=[m.id for m in models])
    if n:
        _audit(db, ctx, "model.purge", info)
        await db.commit()
    return n


async def purge_all(
    db: AsyncSession, backend: StorageBackend, settings: Settings, ctx: AuthContext
) -> int:
    """Empty the trash. Admins purge everything; others only their own."""
    stmt = select(Model.id).where(Model.deleted_at.is_not(None))
    if not ctx.is_admin:
        stmt = stmt.where(Model.created_by_user_id == ctx.user.id)
    ids = list((await db.execute(stmt)).scalars())
    return await purge(db, backend, settings, ctx, ids) if ids else 0


async def list_trash(db: AsyncSession, settings: Settings) -> TrashListOut:
    cfg = await get_app_config(db, settings)
    days = cfg.trash_retention_days
    size_sq = (
        select(
            Revision.model_id.label("mid"),
            func.count(File.id).label("n"),
            func.coalesce(func.sum(Blob.size), 0).label("sz"),
        )
        .select_from(Revision)
        .join(File, File.revision_id == Revision.id)
        .outerjoin(Blob, Blob.hash == File.blob_hash)
        .group_by(Revision.model_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Model, User.username, size_sq.c.n, size_sq.c.sz)
            .outerjoin(User, User.id == Model.deleted_by_user_id)
            .outerjoin(size_sq, size_sq.c.mid == Model.id)
            .where(Model.deleted_at.is_not(None))
            .order_by(Model.deleted_at.desc(), Model.id.desc())
        )
    ).all()
    items = [
        TrashItemOut(
            id=m.id,
            slug=m.slug,
            name=m.name,
            created_by_user_id=m.created_by_user_id,
            deleted_at=m.deleted_at,
            deleted_by=uname,
            purge_at=(m.deleted_at + timedelta(days=days)) if days > 0 else None,
            file_count=n or 0,
            size_bytes=int(sz or 0),
        )
        for m, uname, n, sz in rows
    ]
    return TrashListOut(items=items, retention_days=days)


# -- worker side -------------------------------------------------------------


def purge_model_sync(session: SyncSession, settings: Settings, model: Model) -> None:
    """Physically delete one trashed model: every file's object on its
    primary backend and each replica, the sidecar, then the row."""
    files = list(
        session.execute(
            select(File)
            .join(Revision, File.revision_id == Revision.id)
            .where(Revision.model_id == model.id)
        ).scalars()
    )
    paths = {f.id: f.storage_path for f in files}
    primary = {f.id: resolve_backend_for_file_sync(session, settings, f) for f in files}
    locs = (
        list(
            session.execute(
                select(FileLocation).where(FileLocation.file_id.in_(list(paths)))
            ).scalars()
        )
        if paths
        else []
    )
    loc_backends: dict[int, StorageBackend] = {}
    for loc in locs:
        if loc.backend_id not in loc_backends:
            loc_backends[loc.backend_id] = backend_for_id_sync(session, settings, loc.backend_id)
    for f in files:
        with contextlib.suppress(StorageKeyNotFound):
            primary[f.id].delete(paths[f.id])
    for loc in locs:
        with contextlib.suppress(StorageKeyNotFound):
            loc_backends[loc.backend_id].delete(paths[loc.file_id])
    default_backend, _ = resolve_default_backend_sync(session, settings)
    with contextlib.suppress(StorageKeyNotFound):
        default_backend.delete(layout.sidecar_key(model.slug))
    session.execute(sa_delete(Model).where(Model.id == model.id))
    session.commit()


def auto_purge_sync(session: SyncSession, settings: Settings) -> int:
    """Purge trashed models older than the retention window (0 = keep forever)."""
    days = get_app_config_sync(session, settings).trash_retention_days
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    models = list(
        session.execute(
            select(Model).where(Model.deleted_at.is_not(None), Model.deleted_at < cutoff)
        ).scalars()
    )
    purged: list[int] = []
    for m in models:
        mid, slug = m.id, m.slug
        try:
            purge_model_sync(session, settings, m)
            purged.append(mid)
        except Exception:
            session.rollback()
            log.exception("trash auto-purge failed for model %s (%s)", mid, slug)
    if purged:
        session.add(AuditLog(action="model.auto_purge", payload={"model_ids": purged}))
        session.commit()
    return len(purged)
