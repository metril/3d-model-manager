"""Followed-collection CRUD + review-queue service (M8 H).

Async functions back the API; the ``_sync`` twins back the Celery sync task
(``app.tasks.sync_collections``), mirroring the split in
``app.services.storage_backends``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.orm import aliased

from app.models.collections import FollowedCollection, PendingImport, RemoteCollectionItem
from app.models.enums import CollectionSyncMode, ImportSite
from app.models.library import Model
from app.services import tags as tags_svc

# -- followed collections -------------------------------------------------

# R11-C item 17: at most this many preview thumbnails per collection card.
_PREVIEW_COUNT = 4


async def list_followed(
    db: AsyncSession, *, tag_ids: Sequence[int] = (), tag_mode: tags_svc.TagMode = "any"
) -> list[FollowedCollection]:
    stmt = select(FollowedCollection).order_by(FollowedCollection.id)
    if tag_ids:
        stmt = stmt.where(
            tags_svc.tag_filter("collection", FollowedCollection.id, tag_ids, tag_mode)
        )
    return list((await db.execute(stmt)).scalars().all())


async def preview_thumbnails_by_collection(
    db: AsyncSession, collection_ids: Sequence[int]
) -> dict[int, list[str]]:
    """``{collection_id: [thumbnail url, ...]}`` (<= ``_PREVIEW_COUNT`` each)
    for the collage on each followed-collection card.

    One query selects the candidate MODEL ROWS -- a window function ranks
    each collection's models most-recently-imported-first and keeps only the
    top ``_PREVIEW_COUNT`` per collection, so this is a single query
    regardless of how many collections are being rendered (not one per
    collection). Cover-URL derivation then reuses
    ``app.services.library``'s own gallery-cover machinery (import deferred
    to dodge a circular import at module load) rather than duplicating its
    thumb-readiness joins; models whose cover isn't ready yet are simply
    skipped, same as the gallery.
    """
    if not collection_ids:
        return {}

    from app.config import get_settings
    from app.services.library import _gallery_aggregates, _gallery_cover_url

    rank = (
        func.row_number()
        .over(partition_by=Model.source_collection_id, order_by=Model.id.desc())
        .label("rank")
    )
    ranked = (
        select(Model, rank)
        .where(Model.source_collection_id.in_(collection_ids), Model.deleted_at.is_(None))
        .subquery()
    )
    model_alias = aliased(Model, ranked)
    stmt = (
        select(model_alias)
        .where(ranked.c.rank <= _PREVIEW_COUNT)
        .order_by(ranked.c.source_collection_id, ranked.c.rank)
    )
    models = list((await db.execute(stmt)).scalars().all())
    if not models:
        return {}

    aggregates, cover_ok_hashes = await _gallery_aggregates(db, get_settings(), models)

    out: dict[int, list[str]] = {}
    for model in models:
        if model.source_collection_id is None:
            continue
        agg = aggregates.get(model.current_revision_id) if model.current_revision_id else None
        url = _gallery_cover_url(model, agg, cover_ok_hashes)
        if url is not None:
            out.setdefault(model.source_collection_id, []).append(url)
    return out


async def get_followed(db: AsyncSession, collection_id: int) -> FollowedCollection:
    row = await db.get(FollowedCollection, collection_id)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"followed collection {collection_id} not found"
        )
    return row


async def follow(
    db: AsyncSession,
    *,
    site: ImportSite,
    list_id: str,
    kind: str,
    title: str,
    mode: CollectionSyncMode = CollectionSyncMode.REVIEW,
    created_by_user_id: int | None = None,
) -> FollowedCollection:
    """Follow a remote list. Following the same ``(site, list_id)`` twice is a
    409 rather than a silent duplicate -- the UI should offer "unfollow"."""
    existing = (
        (
            await db.execute(
                select(FollowedCollection).where(
                    FollowedCollection.site == site, FollowedCollection.list_id == list_id
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"already following {site.value} list {list_id!r}"
        )
    row = FollowedCollection(
        site=site,
        list_id=list_id,
        kind=kind,
        title=title,
        mode=mode,
        created_by_user_id=created_by_user_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def unfollow(db: AsyncSession, collection_id: int) -> None:
    """Unfollow. Its queued `pending_imports` go with it (FK ON DELETE CASCADE)
    -- they only ever meant "this followed list found something new"."""
    row = await get_followed(db, collection_id)
    await db.delete(row)
    await db.commit()


async def set_mode(
    db: AsyncSession, collection_id: int, mode: CollectionSyncMode
) -> FollowedCollection:
    row = await get_followed(db, collection_id)
    row.mode = mode
    await db.commit()
    await db.refresh(row)
    return row


# -- review queue ---------------------------------------------------------


async def list_pending(db: AsyncSession, collection_id: int | None = None) -> list[PendingImport]:
    stmt = select(PendingImport).order_by(PendingImport.id)
    if collection_id is not None:
        stmt = stmt.where(PendingImport.collection_id == collection_id)
    return list((await db.execute(stmt)).scalars().all())


async def get_pending(db: AsyncSession, pending_id: int) -> PendingImport:
    row = await db.get(PendingImport, pending_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"pending import {pending_id} not found")
    return row


async def delete_pending(db: AsyncSession, pending_id: int) -> None:
    row = await get_pending(db, pending_id)
    await db.delete(row)
    await db.commit()


async def resolve_display_collections(
    db: AsyncSession, pendings: Sequence[PendingImport]
) -> dict[int, tuple[int, str]]:
    """Map each pending row to the followed collection it should be GROUPED/
    DISPLAYED under (R7 T1). ``pending.collection_id`` is only ever the list
    whose sync happened to discover the item first -- historically that was
    almost always the MakerWorld aggregate ("all collected models"), the only
    list the live endpoint could tell apart before the extension started
    pushing real per-collection membership into ``remote_collection_items``
    (see that model's docstring). So: prefer the most SPECIFIC followed list
    that actually contains the item per ``remote_collection_items`` --
    "specific" meaning fewest total members, tie-broken by title then id --
    and only fall back to the stamped ``collection_id`` when no
    ``remote_collection_items`` membership names a better one (a site that
    doesn't push membership at all, or an item genuinely only in the
    aggregate).

    Batched at a fixed number of queries regardless of ``len(pendings)``:
    one to preload every stamped fallback collection, one join mapping
    ``(site, external_id)`` -> candidate followed collections, one grouped
    count of each candidate list's total membership. Never one query per
    pending row.
    """
    if not pendings:
        return {}

    stamped_ids = {p.collection_id for p in pendings}
    stamped_rows = (
        (await db.execute(select(FollowedCollection).where(FollowedCollection.id.in_(stamped_ids))))
        .scalars()
        .all()
    )
    stamped_by_id = {row.id: row for row in stamped_rows}

    sites = {p.site for p in pendings}
    external_ids = {p.external_id for p in pendings}
    candidates_stmt = (
        select(
            RemoteCollectionItem.site,
            RemoteCollectionItem.external_id,
            FollowedCollection.id,
            FollowedCollection.list_id,
            FollowedCollection.title,
        )
        .join(
            FollowedCollection,
            (FollowedCollection.site == RemoteCollectionItem.site)
            & (FollowedCollection.list_id == RemoteCollectionItem.list_id),
        )
        .where(
            RemoteCollectionItem.site.in_(sites),
            RemoteCollectionItem.external_id.in_(external_ids),
        )
    )
    candidate_rows = (await db.execute(candidates_stmt)).all()

    counts_by_list: dict[tuple[ImportSite, str], int] = {}
    candidate_collection_ids = {row.id for row in candidate_rows}
    if candidate_collection_ids:
        counts_stmt = (
            select(RemoteCollectionItem.site, RemoteCollectionItem.list_id, func.count().label("n"))
            .join(
                FollowedCollection,
                (FollowedCollection.site == RemoteCollectionItem.site)
                & (FollowedCollection.list_id == RemoteCollectionItem.list_id),
            )
            .where(FollowedCollection.id.in_(candidate_collection_ids))
            .group_by(RemoteCollectionItem.site, RemoteCollectionItem.list_id)
        )
        counts_by_list = {
            (row.site, row.list_id): row.n for row in (await db.execute(counts_stmt)).all()
        }

    candidates_by_item: dict[tuple[ImportSite, str], list[tuple[int, str, int]]] = {}
    for row in candidate_rows:
        member_count = counts_by_list.get((row.site, row.list_id), 0)
        candidates_by_item.setdefault((row.site, row.external_id), []).append(
            (row.id, row.title, member_count)
        )

    result: dict[int, tuple[int, str]] = {}
    for pending in pendings:
        options = candidates_by_item.get((pending.site, pending.external_id))
        if options:
            best_id, best_title, _n = min(options, key=lambda o: (o[2], o[1], o[0]))
            result[pending.id] = (best_id, best_title)
        else:
            stamped = stamped_by_id.get(pending.collection_id)
            title = stamped.title if stamped is not None else f"Collection #{pending.collection_id}"
            result[pending.id] = (pending.collection_id, title)
    return result


# -- worker-side twins ----------------------------------------------------


def list_followed_sync(session: SyncSession) -> list[FollowedCollection]:
    stmt = select(FollowedCollection).order_by(FollowedCollection.id)
    return list(session.execute(stmt).scalars())


def mark_synced_sync(
    session: SyncSession, collection: FollowedCollection, *, error: str | None = None
) -> None:
    collection.last_synced_at = datetime.now(UTC)
    collection.last_error = error
    session.commit()


def drop_pending_sync(
    session: SyncSession, collection: FollowedCollection, external_id: str
) -> None:
    """Un-queue an item that has since landed in the library (imported from
    search, or approved elsewhere) so a later sync doesn't keep showing it.
    Site-wide (R7 T1): deletes by ``(site, external_id)`` regardless of which
    followed list originally minted the row -- an item that lands in the
    library must leave the review queue no matter which list's sync run
    queued it, not just the list currently being walked. Takes the
    ``FollowedCollection`` (rather than a bare ``site``) so existing
    callsites keep compiling unchanged; only ``collection.site`` is used."""
    session.execute(
        sa_delete(PendingImport).where(
            PendingImport.site == collection.site,
            PendingImport.external_id == external_id,
        )
    )
    session.commit()


def add_pending_sync(
    session: SyncSession,
    collection: FollowedCollection,
    *,
    external_id: str,
    title: str,
    url: str,
    thumbnail_url: str | None = None,
) -> bool:
    """Queue an item for review. Returns True when it was newly queued, False
    when this SITE had already queued it under ``(site, external_id)`` --
    ANY collection, not just this one (R7 T1: item identity for dedup has
    always been ``(site, external_id)``, so a second followed list
    discovering the same item must not queue a duplicate row) -- so a
    repeated sync (or a second list surfacing the same item) is a no-op."""
    already = session.execute(
        select(PendingImport.id).where(
            PendingImport.site == collection.site,
            PendingImport.external_id == external_id,
        )
    ).first()
    if already is not None:
        return False
    session.add(
        PendingImport(
            collection_id=collection.id,
            site=collection.site,
            external_id=external_id,
            title=title,
            url=url,
            thumbnail_url=thumbnail_url,
        )
    )
    session.commit()
    return True
