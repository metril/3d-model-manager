"""``remote_collection_cache``/``remote_collection_items`` services (M10
escape hatch A + Workstream A task 3).

Two independent producers keep the CACHE (collection metadata: id/title/
slug/count) warm: the browser extension pushes a full authoritative snapshot
of a site's collections (``POST /ext/collections``, API/async world), and
``MakerWorldImporter.list_user_lists`` (``app.importers.makerworld``)
upserts into it whenever the SSR route it normally reads happens to succeed
on its own (worker/sync world, via ``app.tasks.base.sync_session`` --
mirrors the async/sync split in ``app.services.storage_backends``). Both
funnel through ``replace_site_cache`` (or its sync twin): the pushed/fetched
set is treated as the site's full current list, so anything cached for that
site but NOT in the new set is stale and gets deleted -- a collection the
user deleted/unfollowed on the remote site should disappear from the cache
too, not linger forever.

``remote_collection_items`` (task 3) is the analogous store for a
collection's CONTENTS: the extension pushes each collection's items (``POST
/ext/collections/{list_id}/items``) because the server-side items endpoint
serves only the uid aggregate (see ``app.models.collections
.RemoteCollectionItem``'s docstring for the live-verified why).
``replace_list_items``/``replace_list_items_sync`` follow the exact same
"authoritative snapshot, full replace" posture as ``replace_site_cache``,
scoped to ``(site, list_id)`` instead of just ``site``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from app.models.collections import RemoteCollectionCache, RemoteCollectionItem
from app.models.enums import ImportSite


@dataclass(frozen=True)
class CacheEntry:
    """One pushed/fetched collection, decoupled from both the ext API's
    pydantic request schema and the ORM row -- the only shape this service
    needs to know about."""

    list_id: str
    title: str
    slug: str | None = None
    count: int | None = None
    is_default: bool = False


def _upsert_values(site: ImportSite, entry: CacheEntry) -> dict:
    return {
        "site": site,
        "list_id": entry.list_id,
        "title": entry.title,
        "slug": entry.slug,
        "count": entry.count,
        "is_default": entry.is_default,
    }


def _upsert_stmt(site: ImportSite, entry: CacheEntry, keep: frozenset[str] = frozenset()):
    """``keep``: column names left untouched on conflict (omitted by the caller)."""
    stmt = pg_insert(RemoteCollectionCache).values(**_upsert_values(site, entry))
    set_ = {
        "title": stmt.excluded.title,
        "slug": stmt.excluded.slug,
        "count": stmt.excluded.count,
        "is_default": stmt.excluded.is_default,
        "updated_at": func.now(),
    }
    for k in keep:
        set_.pop(k, None)
    return stmt.on_conflict_do_update(
        index_elements=[RemoteCollectionCache.site, RemoteCollectionCache.list_id],
        set_=set_,
    )


async def upsert_cache_entry(
    db: AsyncSession,
    site: ImportSite,
    entry: CacheEntry,
    keep: frozenset[str] = frozenset(),
) -> int:
    """ASYNC single-entry upsert by ``(site, list_id)`` (``PUT
    /ext/collections/{list_id}``). Unlike ``replace_site_cache`` this NEVER
    deletes other cached collections. Returns 1."""
    await db.execute(_upsert_stmt(site, entry, keep))
    await db.commit()
    db.expire_all()  # see `replace_site_cache`'s matching comment
    return 1


def upsert_cache_entry_sync(session: SyncSession, site: ImportSite, entry: CacheEntry) -> int:
    """SYNC twin of ``upsert_cache_entry``."""
    session.execute(_upsert_stmt(site, entry))
    session.commit()
    session.expire_all()
    return 1


async def replace_site_cache(db: AsyncSession, site: ImportSite, entries: list[CacheEntry]) -> int:
    """ASYNC twin (API path, ``POST /ext/collections``). Upsert every entry
    by ``(site, list_id)``, then delete whatever else was cached for ``site``
    -- see module docstring for why a partial push is a full replace, not a
    merge. Returns the number of entries just upserted."""
    list_ids = [entry.list_id for entry in entries]
    for entry in entries:
        stmt = pg_insert(RemoteCollectionCache).values(**_upsert_values(site, entry))
        stmt = stmt.on_conflict_do_update(
            index_elements=[RemoteCollectionCache.site, RemoteCollectionCache.list_id],
            set_={
                "title": stmt.excluded.title,
                "slug": stmt.excluded.slug,
                "count": stmt.excluded.count,
                "is_default": stmt.excluded.is_default,
                "updated_at": func.now(),
            },
        )
        await db.execute(stmt)
    delete_stmt = sa_delete(RemoteCollectionCache).where(RemoteCollectionCache.site == site)
    if list_ids:
        delete_stmt = delete_stmt.where(RemoteCollectionCache.list_id.notin_(list_ids))
    await db.execute(delete_stmt)
    await db.commit()
    # The upsert/delete above are raw Core statements, so the unit-of-work
    # never syncs any `RemoteCollectionCache` instance already in this
    # session's identity map (e.g. from an earlier `get_cache_entry` call in
    # the same request) -- expire it so the next attribute access re-reads
    # the row this call just wrote, instead of silently returning stale data.
    db.expire_all()
    return len(entries)


def replace_site_cache_sync(
    session: SyncSession, site: ImportSite, entries: list[CacheEntry]
) -> int:
    """SYNC twin (worker path, ``list_user_lists``'s self-heal). Same
    semantics as ``replace_site_cache`` -- see its docstring."""
    list_ids = [entry.list_id for entry in entries]
    for entry in entries:
        stmt = pg_insert(RemoteCollectionCache).values(**_upsert_values(site, entry))
        stmt = stmt.on_conflict_do_update(
            index_elements=[RemoteCollectionCache.site, RemoteCollectionCache.list_id],
            set_={
                "title": stmt.excluded.title,
                "slug": stmt.excluded.slug,
                "count": stmt.excluded.count,
                "is_default": stmt.excluded.is_default,
                "updated_at": func.now(),
            },
        )
        session.execute(stmt)
    delete_stmt = sa_delete(RemoteCollectionCache).where(RemoteCollectionCache.site == site)
    if list_ids:
        delete_stmt = delete_stmt.where(RemoteCollectionCache.list_id.notin_(list_ids))
    session.execute(delete_stmt)
    session.commit()
    session.expire_all()  # see `replace_site_cache`'s matching comment
    return len(entries)


def get_site_cache(session: SyncSession, site: ImportSite) -> list[RemoteCollectionCache]:
    """Every cached collection for ``site``, ordered ``is_default`` first
    then title -- the order ``list_user_lists`` merges named collections in."""
    stmt = (
        select(RemoteCollectionCache)
        .where(RemoteCollectionCache.site == site)
        .order_by(RemoteCollectionCache.is_default.desc(), RemoteCollectionCache.title.asc())
    )
    return list(session.execute(stmt).scalars())


async def get_cache_entry(
    db: AsyncSession, site: ImportSite, list_id: str
) -> RemoteCollectionCache | None:
    """ASYNC single-row lookup (API path, ``POST /collections/from-url``'s
    title fill-in) -- None when this (site, list_id) has never been cached."""
    stmt = select(RemoteCollectionCache).where(
        RemoteCollectionCache.site == site, RemoteCollectionCache.list_id == list_id
    )
    return (await db.execute(stmt)).scalars().first()


@dataclass(frozen=True)
class ItemEntry:
    """One pushed collection item, decoupled from the ext API's pydantic
    request schema and the ORM row -- deliberately NOT
    ``app.importers.base.SearchResult`` either, even though the two shapes
    match field-for-field, so this service has no import-time reason to know
    about the importer layer (``list_list_items`` does that mapping itself,
    the same way it already maps a live hit into a ``SearchResult``)."""

    external_id: str
    title: str
    url: str
    author: str | None = None
    thumbnail_url: str | None = None


def _item_upsert_values(site: ImportSite, list_id: str, position: int, entry: ItemEntry) -> dict:
    return {
        "site": site,
        "list_id": list_id,
        "external_id": entry.external_id,
        "title": entry.title,
        "url": entry.url,
        "author": entry.author,
        "thumbnail_url": entry.thumbnail_url,
        "position": position,
    }


async def replace_list_items(
    db: AsyncSession, site: ImportSite, list_id: str, items: list[ItemEntry]
) -> int:
    """ASYNC twin (API path, ``POST /ext/collections/{list_id}/items``).
    Upsert every entry by ``(site, list_id, external_id)``, stamping each
    with its position in ``items``, then delete whatever else was stored for
    this ``(site, list_id)`` -- full replace-set, not a merge, same posture
    as ``replace_site_cache``. Returns the number of entries just upserted."""
    external_ids = [entry.external_id for entry in items]
    for position, entry in enumerate(items):
        stmt = pg_insert(RemoteCollectionItem).values(
            **_item_upsert_values(site, list_id, position, entry)
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                RemoteCollectionItem.site,
                RemoteCollectionItem.list_id,
                RemoteCollectionItem.external_id,
            ],
            set_={
                "title": stmt.excluded.title,
                "url": stmt.excluded.url,
                "author": stmt.excluded.author,
                "thumbnail_url": stmt.excluded.thumbnail_url,
                "position": stmt.excluded.position,
                "updated_at": func.now(),
            },
        )
        await db.execute(stmt)
    delete_stmt = sa_delete(RemoteCollectionItem).where(
        RemoteCollectionItem.site == site, RemoteCollectionItem.list_id == list_id
    )
    if external_ids:
        delete_stmt = delete_stmt.where(RemoteCollectionItem.external_id.notin_(external_ids))
    await db.execute(delete_stmt)
    await db.commit()
    # See `replace_site_cache`'s matching comment -- the raw Core upsert/
    # delete above never syncs an already-identity-mapped
    # `RemoteCollectionItem` in this session.
    db.expire_all()
    return len(items)


def replace_list_items_sync(
    session: SyncSession, site: ImportSite, list_id: str, items: list[ItemEntry]
) -> int:
    """SYNC twin -- same semantics as ``replace_list_items``. No current
    worker-side caller (the extension push is the only producer, and it's
    API/async-world), but kept alongside ``replace_site_cache_sync`` for the
    same reason: mirrors the module's established async/sync split so a
    future sync producer (a self-heal, say) doesn't need to invent one."""
    external_ids = [entry.external_id for entry in items]
    for position, entry in enumerate(items):
        stmt = pg_insert(RemoteCollectionItem).values(
            **_item_upsert_values(site, list_id, position, entry)
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                RemoteCollectionItem.site,
                RemoteCollectionItem.list_id,
                RemoteCollectionItem.external_id,
            ],
            set_={
                "title": stmt.excluded.title,
                "url": stmt.excluded.url,
                "author": stmt.excluded.author,
                "thumbnail_url": stmt.excluded.thumbnail_url,
                "position": stmt.excluded.position,
                "updated_at": func.now(),
            },
        )
        session.execute(stmt)
    delete_stmt = sa_delete(RemoteCollectionItem).where(
        RemoteCollectionItem.site == site, RemoteCollectionItem.list_id == list_id
    )
    if external_ids:
        delete_stmt = delete_stmt.where(RemoteCollectionItem.external_id.notin_(external_ids))
    session.execute(delete_stmt)
    session.commit()
    session.expire_all()
    return len(items)


def get_list_items(
    session: SyncSession, site: ImportSite, list_id: str
) -> list[RemoteCollectionItem]:
    """Every pushed item for ``(site, list_id)``, ordered by ``position`` --
    the order the extension observed them on the page. Sync-only: the one
    reader (``MakerWorldImporter.list_list_items``'s cache fallback) runs in
    the worker/thread-offload sync world, same as ``get_site_cache``."""
    stmt = (
        select(RemoteCollectionItem)
        .where(RemoteCollectionItem.site == site, RemoteCollectionItem.list_id == list_id)
        .order_by(RemoteCollectionItem.position.asc())
    )
    return list(session.execute(stmt).scalars())
