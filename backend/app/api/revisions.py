"""Revision listing/creation, revision detail, and revision diff (SPEC "API
surface", Task 5 brief).

Model-scoped listing/creation are keyed by the model's numeric id
(``/models/{model_id}/revisions``), distinct from the slug-keyed
``/models/{slug}`` detail routes in ``app.api.models`` -- that split is
exactly what the Task 5 brief specifies.
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_storage_backend, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AssemblyThumb, Revision
from app.models.enums import DerivativeStatus
from app.schemas.library import DiffResponse, RevisionCreate, RevisionDetail, RevisionSummary
from app.services import derivatives, library
from app.storage.base import StorageBackend

router = APIRouter(tags=["revisions"])


@router.get("/models/{model_id}/revisions", response_model=list[RevisionSummary])
async def list_revisions(
    model_id: int, db: AsyncSession = Depends(get_db)
) -> list[RevisionSummary]:
    model = await library.get_model_by_id(db, model_id)
    return await library.list_revisions(db, model)


@router.post(
    "/models/{model_id}/revisions",
    status_code=status.HTTP_201_CREATED,
    response_model=RevisionDetail,
    dependencies=[Depends(require_perm("library:write"))],
)
async def create_revision(
    model_id: int,
    payload: RevisionCreate,
    db: AsyncSession = Depends(get_db),
    backend: StorageBackend = Depends(get_storage_backend),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> RevisionDetail:
    model = await library.get_model_by_id(db, model_id)
    revision = await library.create_revision(
        db,
        backend,
        model,
        settings,
        name=payload.name,
        note=payload.note,
        created_by_user_id=ctx.user.id,
    )
    return await library.build_revision_detail(db, revision, settings)


@router.get("/revisions/{revision_id}", response_model=RevisionDetail)
async def get_revision(
    revision_id: int, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> RevisionDetail:
    revision = await library.get_revision_or_404(db, revision_id)
    return await library.build_revision_detail(db, revision, settings)


@router.get("/revisions/{revision_a_id}/diff/{revision_b_id}", response_model=DiffResponse)
async def diff_revisions(
    revision_a_id: int, revision_b_id: int, db: AsyncSession = Depends(get_db)
) -> DiffResponse:
    return await library.diff_revisions(db, revision_a_id, revision_b_id)


@router.get("/revisions/{revision_id}/assembly-thumb")
async def get_assembly_thumb(
    revision_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Whole-revision assembly PNG (Task 7 brief). Unlike blob derivatives,
    this is ``Cache-Control: no-cache`` with an mtime-keyed ``ETag`` rather
    than an immutable one: the CURRENT revision's assembly can be re-rendered
    (a file added/removed/replaced) without its id changing, so a client must
    always revalidate rather than caching forever.
    """
    revision = await db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"revision {revision_id} not found")

    thumb = await db.get(AssemblyThumb, revision_id)
    if thumb is None or thumb.status != DerivativeStatus.OK:
        detail = thumb.status.value if thumb is not None else DerivativeStatus.PENDING.value
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail)

    path = derivatives.assembly_thumb_path(settings, revision_id)
    try:
        mtime_ns = await anyio.to_thread.run_sync(lambda: path.stat().st_mtime_ns)
    except FileNotFoundError:
        # The DB row says `ok`, but the file is gone (e.g. removed out of
        # band) -- an inconsistent state, but a clean 404 beats a raw 500.
        raise HTTPException(status.HTTP_404_NOT_FOUND, DerivativeStatus.PENDING.value) from None
    etag = f'"{revision_id}:{mtime_ns}"'
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return FileResponse(path, media_type="image/png", headers=headers)
