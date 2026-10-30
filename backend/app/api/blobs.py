"""Blob-derivative endpoints (Task 7 brief; Global Constraints "Derivative
store" / "Two GLB artifacts per blob"): immutable-cached thumbnails, GLBs,
and per-plate preview PNGs, served straight off the derivative store's
on-disk files -- no derivative bytes ever pass through the DB.
"""

from __future__ import annotations

from enum import IntEnum

import anyio
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Blob, Derivative
from app.models.enums import DerivativeKind, DerivativeStatus
from app.services import derivatives

router = APIRouter(prefix="/blobs", tags=["blobs"])

_IMMUTABLE_CACHE_CONTROL = "private, max-age=31536000, immutable"


class ThumbSize(IntEnum):
    """``size=256|1024`` (Task 7 brief). A plain ``Literal[256, 1024]`` query
    param doesn't coerce a query string ("1024") to the matching int member
    on this FastAPI/Pydantic version -- an ``IntEnum`` does, while still
    422ing on any other value.
    """

    SMALL = 256
    LARGE = 1024


def _if_none_match_matches(request: Request, quoted_etag: str) -> bool:
    """``If-None-Match`` may carry a comma-separated list of validators
    (RFC 7232 SS3.2), any of which can be weak (``W/"..."``-prefixed) -- a
    client revalidating several cached representations (e.g. after the
    ``size``/``preview`` query param changed) sends its whole list on one
    request. A strict single-value string compare only matched when the
    right entry happened to be first (M2-Minor 4 fold).
    """
    header = request.headers.get("if-none-match")
    if not header:
        return False
    return any(
        candidate.strip().removeprefix("W/") == quoted_etag for candidate in header.split(",")
    )


async def _conditional_file_response(
    request: Request,
    path,
    etag: str,
    media_type: str,
    *,
    cache_control: str,
    missing_detail: str = DerivativeStatus.PENDING.value,
) -> Response:
    quoted_etag = f'"{etag}"'
    headers = {"Cache-Control": cache_control, "ETag": quoted_etag}
    if _if_none_match_matches(request, quoted_etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    exists = await anyio.to_thread.run_sync(path.exists)
    if not exists:
        # The DB says `ok`, but the file is gone (e.g. removed out of band)
        # -- an inconsistent state, but a clean 404 beats `FileResponse`
        # raising a raw 500 at send time (M2-Minor 4 fold; matches
        # app.api.revisions.get_assembly_thumb's existing handling).
        raise HTTPException(status.HTTP_404_NOT_FOUND, missing_detail)
    return FileResponse(path, media_type=media_type, headers=headers)


async def _get_blob_or_404(db: AsyncSession, blob_hash: str) -> Blob:
    blob = await db.get(Blob, blob_hash)
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "blob not found")
    return blob


async def _get_derivative(
    db: AsyncSession, blob_hash: str, kind: DerivativeKind
) -> Derivative | None:
    return await db.scalar(
        select(Derivative).where(Derivative.blob_hash == blob_hash, Derivative.kind == kind)
    )


def _not_ready_detail(deriv: Derivative | None) -> str:
    """A missing row means the step hasn't run yet -- same client-facing
    story as an explicit ``pending`` status.
    """
    return deriv.status.value if deriv is not None else DerivativeStatus.PENDING.value


@router.get("/{blob_hash}/thumb")
async def get_blob_thumb(
    blob_hash: str,
    request: Request,
    size: ThumbSize = ThumbSize.SMALL,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    await _get_blob_or_404(db, blob_hash)
    kind = DerivativeKind.THUMB_256 if size == ThumbSize.SMALL else DerivativeKind.THUMB_1024
    deriv = await _get_derivative(db, blob_hash, kind)
    if deriv is None or deriv.status != DerivativeStatus.OK:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _not_ready_detail(deriv))

    path = derivatives.derivative_path(settings, blob_hash, kind)
    return await _conditional_file_response(
        request,
        path,
        f"{blob_hash}:{kind.value}",
        "image/png",
        cache_control=_IMMUTABLE_CACHE_CONTROL,
    )


@router.get("/{blob_hash}/plates/{index}/thumb")
async def get_blob_plate_thumb(
    blob_hash: str,
    request: Request,
    index: int = Path(ge=1),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    await _get_blob_or_404(db, blob_hash)
    path = derivatives.plate_thumb_path(settings, blob_hash, index)

    return await _conditional_file_response(
        request,
        path,
        f"{blob_hash}:plate{index}",
        "image/png",
        cache_control=_IMMUTABLE_CACHE_CONTROL,
        missing_detail="plate thumbnail not found",
    )


@router.get("/{blob_hash}/glb")
async def get_blob_glb(
    blob_hash: str,
    request: Request,
    preview: bool = False,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Serves the meshopt-compressed browser GLB when it exists, else the
    raw uncompressed ``glb`` derivative -- see Global Constraints "Two GLB
    artifacts per blob". ``preview=true`` prefers the decimated LOD when
    it's ``ok``, falling through to the same raw-glb-then-web chain
    otherwise (Task 7 interface decision).
    """
    await _get_blob_or_404(db, blob_hash)

    if preview:
        preview_deriv = await _get_derivative(db, blob_hash, DerivativeKind.GLB_PREVIEW)
        if preview_deriv is not None and preview_deriv.status == DerivativeStatus.OK:
            path = derivatives.derivative_path(settings, blob_hash, DerivativeKind.GLB_PREVIEW)
            return await _conditional_file_response(
                request,
                path,
                f"{blob_hash}:glb_preview",
                "model/gltf-binary",
                cache_control=_IMMUTABLE_CACHE_CONTROL,
            )

    raw_deriv = await _get_derivative(db, blob_hash, DerivativeKind.GLB)
    if raw_deriv is None or raw_deriv.status != DerivativeStatus.OK:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _not_ready_detail(raw_deriv))

    web_path = derivatives.glb_web_path(settings, blob_hash)
    web_exists = await anyio.to_thread.run_sync(web_path.exists)
    path = (
        web_path
        if web_exists
        else derivatives.derivative_path(settings, blob_hash, DerivativeKind.GLB)
    )

    return await _conditional_file_response(
        request,
        path,
        f"{blob_hash}:glb",
        "model/gltf-binary",
        cache_control=_IMMUTABLE_CACHE_CONTROL,
    )
