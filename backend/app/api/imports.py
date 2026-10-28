"""Gallery import endpoints (SPEC "API surface": imports (create/poll)).
POST detects the site from the URL, creates a ``pending`` Import row, and
dispatches ``import_from_url``; GET/{id} + list poll the row (the primary
read path -- richer than the generic jobs row). ``GET /search`` searches one
site or, with no ``site``, ALL registered sites concurrently (federated), so
the UI can browse-then-import instead of needing a URL up front. A deferred
site's URL (currently none -- see ``registry._DEFERRED_HOSTS``) would yield
a friendly 422, never a crash; an unsupported one always does."""

from __future__ import annotations

from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.db import get_db
from app.importers.base import SEARCH_PAGE_SIZE, RemoteList, SearchResult
from app.importers.registry import (
    get_importer,
    registered_sites,
)
from app.models.enums import ImportSite, ImportState
from app.models.system import Import
from app.schemas.imports import (
    ImportCreate,
    ImportOut,
    RemoteListOut,
    SearchResponse,
    SearchResultOut,
    SiteSearchStatus,
)
from app.services.imports import retry_failed_import, start_import

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("/search", response_model=SearchResponse)
async def search_imports(
    q: str,
    site: Annotated[list[ImportSite] | None, Query()] = None,
    page: int = 1,
) -> SearchResponse:
    """Search a chosen subset of sites (repeat ``?site=`` per site) or, when no
    ``site`` is given, ALL registered sites at once (federated). Each site is
    queried on its own worker thread so one slow or failing upstream can't stall
    or sink the others; results are merged and a per-site status row reports
    counts, likely-more, and errors."""
    q = q.strip()
    if not q:
        return SearchResponse(results=[], per_site=[])

    if site:
        # An explicit site with no importer stays a hard 422 (asking for a
        # specific unavailable site is an error); the federated (no-site) path
        # below instead just omits unregistered sites. Dedupe while preserving
        # the caller's order.
        sites = list(dict.fromkeys(site))
        for target in sites:
            if get_importer(target) is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, f"{target.value} isn't available"
                )
    else:
        sites = registered_sites()

    # Every importer's `search()` is fully BLOCKING (a sync httpx.Client with a
    # 30s timeout; Thingiverse/MakerWorld also open a blocking sync DB session
    # for their token, and MakerWorld may do a blocking Bambu token refresh).
    # Fan out one worker thread per site under a task group so a slow upstream
    # only delays itself, and catch per-site so one failure doesn't 500 the lot.
    hits_by_site: dict[ImportSite, list[SearchResult]] = {}
    errors_by_site: dict[ImportSite, str] = {}

    async def run_one(target: ImportSite) -> None:
        importer = get_importer(target)
        if importer is None:
            return
        try:
            hits_by_site[target] = await anyio.to_thread.run_sync(importer.search, q, page)
        except Exception as exc:  # noqa: BLE001 -- isolate one upstream's failure
            errors_by_site[target] = str(exc) or exc.__class__.__name__

    async with anyio.create_task_group() as tg:
        for target in sites:
            tg.start_soon(run_one, target)

    results: list[SearchResultOut] = []
    per_site: list[SiteSearchStatus] = []
    for target in sites:  # stable, site-grouped order
        hits = hits_by_site.get(target, [])
        results.extend(SearchResultOut.from_dataclass(r) for r in hits)
        if target in errors_by_site:
            per_site.append(
                SiteSearchStatus(
                    site=target.value,
                    count=0,
                    has_more=False,
                    status="error",
                    detail=errors_by_site[target],
                )
            )
        else:
            per_site.append(
                SiteSearchStatus(
                    site=target.value,
                    count=len(hits),
                    has_more=len(hits) >= SEARCH_PAGE_SIZE,
                )
            )
    return SearchResponse(results=results, per_site=per_site)


@router.get("/lists", response_model=list[RemoteListOut])
async def list_remote_lists(
    site: Annotated[list[ImportSite] | None, Query()] = None,
) -> list[RemoteListOut]:
    """The signed-in user's collections + likes across sites (M8 H). Fans out
    concurrently like ``/search``; a site whose authenticated session isn't
    wired up yet simply contributes nothing, and one failing upstream can't sink
    the others. Declared BEFORE ``/{import_id}`` so "lists" isn't parsed as an id.
    """
    sites = list(dict.fromkeys(site)) if site else registered_sites()

    lists_by_site: dict[ImportSite, list[RemoteList]] = {}

    async def run_one(target: ImportSite) -> None:
        importer = get_importer(target)
        if importer is None:
            return
        try:
            lists_by_site[target] = await anyio.to_thread.run_sync(importer.list_user_lists)
        except Exception:  # noqa: BLE001 -- isolate one upstream's failure
            lists_by_site[target] = []

    async with anyio.create_task_group() as tg:
        for target in sites:
            tg.start_soon(run_one, target)

    return [
        RemoteListOut.from_dataclass(remote)
        for target in sites
        for remote in lists_by_site.get(target, [])
    ]


@router.get("/lists/{site}/{list_id}/items", response_model=list[SearchResultOut])
async def list_remote_list_items(
    site: ImportSite, list_id: str, page: int = 1
) -> list[SearchResultOut]:
    """The models inside one remote list. Same blocking-importer offload as
    ``/search`` (see its comment)."""
    importer = get_importer(site)
    if importer is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{site.value} isn't available")
    items = await anyio.to_thread.run_sync(importer.list_list_items, list_id, page)
    return [SearchResultOut.from_dataclass(item) for item in items]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ImportOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def create_import(
    payload: ImportCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ImportOut:
    # Already in the library? `start_import` hands back the EXISTING import
    # rather than minting a duplicate Model (M8 H) -- answer 200, because
    # nothing was created. This is what makes a repeated collection sync (and a
    # double-click on "Add to library") idempotent across imports, not just
    # across Celery redeliveries.
    imp, created = await start_import(db, payload.url, created_by_user_id=ctx.user.id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return ImportOut.from_model(imp)


@router.get("", response_model=list[ImportOut])
async def list_imports(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    state: ImportState | None = None,
    include_dismissed: bool = True,
    db: AsyncSession = Depends(get_db),
) -> list[ImportOut]:
    """Newest first. ``state`` filters to one state; ``include_dismissed=false``
    hides dismissed rows; ``offset`` pages."""
    stmt = select(Import)
    if state is not None:
        stmt = stmt.where(Import.state == state)
    if not include_dismissed:
        stmt = stmt.where(Import.dismissed_at.is_(None))
    stmt = stmt.order_by(Import.created_at.desc(), Import.id.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [ImportOut.from_model(r) for r in rows]


@router.get("/failed-count")
async def failed_import_count(db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    """Failed, not-dismissed imports -- what the nav badge shows. Declared
    BEFORE ``/{import_id}``."""
    n = await db.scalar(
        select(func.count())
        .select_from(Import)
        .where(Import.state == ImportState.FAILED, Import.dismissed_at.is_(None))
    )
    return {"count": n or 0}


@router.post(
    "/dismiss-failed",
    dependencies=[Depends(require_perm("library:write"))],
)
async def dismiss_failed_imports(db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    """Acknowledge every failed, not-yet-dismissed import; returns how many."""
    result = await db.execute(
        update(Import)
        .where(Import.state == ImportState.FAILED, Import.dismissed_at.is_(None))
        .values(dismissed_at=func.now())
    )
    await db.commit()
    return {"dismissed": result.rowcount}


@router.get("/{import_id}", response_model=ImportOut)
async def get_import(import_id: int, db: AsyncSession = Depends(get_db)) -> ImportOut:
    imp = await db.get(Import, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"import {import_id} not found")
    return ImportOut.from_model(imp)


@router.post(
    "/{import_id}/retry",
    response_model=ImportOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def retry_import(import_id: int, db: AsyncSession = Depends(get_db)) -> ImportOut:
    """Re-enqueue a ``failed`` import (import-health branch T2) -- the
    recovery path once whatever failed it (e.g. a dead Bambu session, T1) has
    been fixed, without having to re-paste the URL. 404 unknown id, 409
    unless the row is currently ``failed`` (``retry_failed_import``)."""
    imp = await retry_failed_import(db, import_id)
    return ImportOut.from_model(imp)


@router.post(
    "/{import_id}/dismiss",
    response_model=ImportOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def dismiss_import(import_id: int, db: AsyncSession = Depends(get_db)) -> ImportOut:
    """Acknowledge a ``failed`` import so it stops counting toward the nav
    badge (idempotent). 404 unknown id, 409 unless currently ``failed``."""
    imp = await db.get(Import, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"import {import_id} not found")
    if imp.state != ImportState.FAILED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"import {import_id} is not in a failed state"
        )
    if imp.dismissed_at is None:
        imp.dismissed_at = func.now()
        await db.commit()
        await db.refresh(imp)
    return ImportOut.from_model(imp)
