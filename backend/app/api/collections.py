"""Followed remote collections + review queue (M8 H).

Follow a site's collection/likes list, choose per-list whether a sync
auto-imports new items or queues them for approval, run the sync on demand, and
approve/dismiss whatever a ``review`` list queued up.

Literal segments (``/sync``, ``/pending``) are declared BEFORE
``/{collection_id}`` so they aren't parsed as an id.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.importers.makerworld import parse_collection_url
from app.models.enums import ImportSite
from app.schemas.collections import (
    CollectionModeIn,
    FollowCollectionIn,
    FollowedCollectionOut,
    FollowFromUrlIn,
    PendingImportOut,
)
from app.schemas.imports import ImportOut
from app.schemas.jobs import JobOut
from app.services import collections as collections_svc
from app.services import jobs as jobs_service
from app.services import remote_collections as remote_collections_svc
from app.services import zip_export
from app.services.http_names import content_disposition_attachment
from app.services.imports import start_import
from app.tasks.sync_collections import sync_all

router = APIRouter(prefix="/collections", tags=["collections"])


@router.post("/sync", response_model=JobOut, dependencies=[Depends(require_perm("library:write"))])
async def sync_collections_now(db: AsyncSession = Depends(get_db)) -> JobOut:
    """Run the periodic sync immediately (the beat schedule is opt-in via
    ``COLLECTION_SYNC_INTERVAL``; this button always works). Tracked as a
    Job so it shows up on the Jobs page like any other background work."""
    job = await jobs_service.create_job(
        db, id=uuid.uuid4(), type="sync_collections", subject_type=None, subject_id=None
    )
    sync_all.apply_async(args=[str(job.id)], task_id=str(job.id))
    # Eager Celery (tests) already ran it inline through its own sync session.
    await db.refresh(job)
    return JobOut.from_model(job)


@router.get("/pending", response_model=list[PendingImportOut])
async def list_pending_imports(
    collection_id: int | None = None, db: AsyncSession = Depends(get_db)
) -> list[PendingImportOut]:
    rows = await collections_svc.list_pending(db, collection_id)
    groups = await collections_svc.resolve_display_collections(db, rows)
    return [PendingImportOut.from_model(row, groups[row.id]) for row in rows]


@router.post(
    "/pending/{pending_id}/approve",
    status_code=status.HTTP_201_CREATED,
    response_model=ImportOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def approve_pending_import(
    pending_id: int,
    response: Response,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ImportOut:
    """Import a queued item, then un-queue it. Goes through the same
    dedup-guarded ``start_import`` as ``POST /imports``, so approving something
    that arrived some other way answers 200 and creates nothing.

    Stamps the RESOLVED display collection (R7 T1), not the stamped
    ``pending.collection_id`` -- ``pending.collection_id`` is just whichever
    list's sync happened to discover the item first (often the MakerWorld
    aggregate), while the resolved collection is the most specific real list
    the item is actually in, so downstream provenance
    (``source_collection_id``/``title``, stamped by the import worker) reads
    the specific collection a user would recognize."""
    pending = await collections_svc.get_pending(db, pending_id)
    groups = await collections_svc.resolve_display_collections(db, [pending])
    group_collection_id, _group_title = groups[pending.id]
    collection = await collections_svc.get_followed(db, group_collection_id)
    imp, created = await start_import(db, pending.url, collection, created_by_user_id=ctx.user.id)
    await collections_svc.delete_pending(db, pending_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return ImportOut.from_model(imp)


@router.delete(
    "/pending/{pending_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("library:write"))],
)
async def dismiss_pending_import(pending_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await collections_svc.delete_pending(db, pending_id)


@router.get("", response_model=list[FollowedCollectionOut])
async def list_followed_collections(
    db: AsyncSession = Depends(get_db),
) -> list[FollowedCollectionOut]:
    rows = await collections_svc.list_followed(db)
    previews = await collections_svc.preview_thumbnails_by_collection(db, [row.id for row in rows])
    return [FollowedCollectionOut.from_model(row, previews.get(row.id)) for row in rows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=FollowedCollectionOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def follow_collection(
    payload: FollowCollectionIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> FollowedCollectionOut:
    row = await collections_svc.follow(
        db,
        site=payload.site,
        list_id=payload.list_id,
        kind=payload.kind,
        title=payload.title,
        mode=payload.mode,
        created_by_user_id=ctx.user.id,
    )
    return FollowedCollectionOut.from_model(row)


@router.post(
    "/from-url",
    status_code=status.HTTP_201_CREATED,
    response_model=FollowedCollectionOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def follow_collection_from_url(
    payload: FollowFromUrlIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> FollowedCollectionOut:
    """Add-by-URL escape hatch (M10 Workstream A): follow a MakerWorld
    collection by pasting its URL, for when the SSR route that would
    otherwise let a user pick one off a list is Cloudflare-walled (see
    ``app.importers.makerworld``'s module docstring). Only MakerWorld is
    supported -- Thingiverse/Printables collections are already reachable
    through ``list_user_lists``. The title comes from the collection cache
    (extension push, or a past successful SSR read) when we have it, else a
    generic placeholder the user can rename by unfollowing/re-following once
    they know the real name.

    Declared before ``/{collection_id}`` so ``from-url`` isn't parsed as an
    id (see this module's docstring)."""
    list_id = parse_collection_url(payload.url)
    if list_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{payload.url!r} doesn't look like a MakerWorld collection URL "
            "(expected e.g. https://makerworld.com/en/collections/<id>-<slug>)",
        )
    cached = await remote_collections_svc.get_cache_entry(db, ImportSite.MAKERWORLD, list_id)
    title = cached.title if cached is not None else f"MakerWorld collection {list_id}"
    row = await collections_svc.follow(
        db,
        site=ImportSite.MAKERWORLD,
        list_id=list_id,
        kind="collection",
        title=title,
        mode=payload.mode,
        created_by_user_id=ctx.user.id,
    )
    return FollowedCollectionOut.from_model(row)


@router.patch(
    "/{collection_id}",
    response_model=FollowedCollectionOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def set_collection_mode(
    collection_id: int,
    payload: CollectionModeIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> FollowedCollectionOut:
    existing = await collections_svc.get_followed(db, collection_id)
    require_owner_or_admin(ctx, existing.created_by_user_id)
    row = await collections_svc.set_mode(db, collection_id, payload.mode)
    return FollowedCollectionOut.from_model(row)


@router.delete(
    "/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("library:write"))],
)
async def unfollow_collection(
    collection_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    existing = await collections_svc.get_followed(db, collection_id)
    require_owner_or_admin(ctx, existing.created_by_user_id)
    await collections_svc.unfollow(db, collection_id)


@router.get("/{collection_id}/zip")
async def download_collection_zip(
    collection_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream a zip nesting every model in this collection as
    ``<collection-name>/<model-slug>/...`` (plan item 12). Models with zero
    current-revision files are skipped; an empty collection still streams a
    (valid, empty) zip rather than 409ing."""
    collection = await collections_svc.get_followed(db, collection_id)
    _chunk, body = await zip_export.first_chunk(
        zip_export.iter_collection_zip(db, settings, collection)
    )

    return StreamingResponse(
        body,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition_attachment(f"{collection.title}.zip")},
    )
