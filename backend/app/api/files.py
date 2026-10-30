"""File deletion + download, restricted to/scoped by a model's current
revision where relevant (SPEC "API surface", Task 5 brief + Task 6 interface
decisions).
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import iterate_in_threadpool

from app.api.deps import (
    SESSION_COOKIE_NAME,
    AuthContext,
    require_owner_or_admin,
    require_perm,
    require_session,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Blob, File
from app.models.enums import BlobFormat
from app.pipeline import slicedmeta
from app.services import library, signed_urls
from app.services.bed_fit import fit_for_selection
from app.services.http_names import content_disposition_attachment, content_disposition_inline
from app.services.server_config import ServerConfig, get_server_config, public_origin
from app.services.storage_backends import resolve_backend_for_file
from app.storage.base import StorageBackend
from app.storage.errors import StorageKeyNotFound

router = APIRouter(prefix="/files", tags=["files"])

# Unauthenticated at the router level (mounted directly on ``api_router``,
# like ``ext.router`` -- see ``app.api``'s module docstring): the download
# route below does its OWN auth, branching on whether a signed ``?token=``
# is present, so it can't sit under ``protected_router``'s blanket
# ``require_session`` dependency, which would run unconditionally before the
# endpoint ever gets a look at the query string.
public_router = APIRouter(prefix="/files", tags=["files"])


class FileFormatOut(BaseModel):
    id: int
    rel_path: str
    format: BlobFormat


@router.get("/{file_id}", response_model=FileFormatOut)
async def get_file_format(file_id: int, db: AsyncSession = Depends(get_db)) -> FileFormatOut:
    """Minimal file lookup (id, path, format) -- the print dialog needs a
    file's format to know whether it is already sliced."""
    file = await library.get_live_file(db, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    blob = await db.get(Blob, file.blob_hash)
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    return FileFormatOut(id=file.id, rel_path=file.rel_path, format=blob.format)


@router.get("/{file_id}/bed-fit")
async def file_bed_fit(
    file_id: int,
    printer_id: int | None = None,
    recipe_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Slug-free twin of ``GET /models/{slug}/files/{id}/bed-fit`` (the print
    wizard only knows a file id)."""
    file = await library.get_live_file(db, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    result = await fit_for_selection(db, file, printer_id, recipe_id, settings)
    await db.commit()  # persist a freshly computed plate_count
    return result.model_dump()


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("library:write"))],
)
async def delete_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> None:
    file = await db.get(File, file_id)
    if file is not None:  # unknown id falls through to the service's 404
        require_owner_or_admin(ctx, file.created_by_user_id)
    await library.delete_file(db, settings, file_id)


# Extension -> media type (review finding 4): a desktop slicer opening a
# deep-linked download decides whether to accept the file partly off
# Content-Type -- a blanket application/octet-stream got silently refused.
_MEDIA_TYPES_BY_SUFFIX: dict[str, str] = {
    ".stl": "model/stl",
    ".3mf": "model/3mf",
    ".step": "model/step",
    ".stp": "model/step",
    ".obj": "model/obj",
    ".gcode": "text/x.gcode",
    # R13c: doc kinds.
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# R13c: doc formats a browser can render inline (``?inline=1``) rather than
# download -- ``.docx`` has no reliable in-browser renderer, so it's
# excluded and always downloads.
_INLINE_PREVIEWABLE_SUFFIXES = {".pdf", ".txt", ".md"}


def _media_type_for_filename(filename: str) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    return _MEDIA_TYPES_BY_SUFFIX.get(suffix, "application/octet-stream")


def _content_disposition_for_filename(filename: str, *, inline: bool) -> str:
    """``?inline=1`` (R13c doc preview) renders pdf/txt/md in the browser
    instead of downloading; every other request/format keeps the existing
    ``attachment`` behavior unchanged."""
    suffix = PurePosixPath(filename).suffix.lower()
    if inline and suffix in _INLINE_PREVIEWABLE_SUFFIXES:
        return content_disposition_inline(filename)
    return content_disposition_attachment(filename)


# Chunk size for streaming a zip member out (review finding 1) -- matches
# gcode_meta's own bounded-read chunk size, no particular reason they must
# match beyond "both are a reasonable, small, fixed unit of I/O".
_GCODE_STREAM_CHUNK_BYTES = 256 * 1024


def _spool_to_temp_file(backend: StorageBackend, storage_path: str) -> Path:
    """Copy ``storage_path``'s bytes to a private temp file and return its
    path (review finding 1). ``StorageBackend.read`` only ever yields a
    forward ``Iterator[bytes]`` -- never a seekable handle -- but
    ``zipfile.ZipFile`` needs random access to read a ``.gcode.3mf``'s
    central directory and open one member without buffering the rest.
    Caller owns cleanup (``Path.unlink``) once done with the file.
    """
    fd, tmp_name = tempfile.mkstemp(prefix="tdmm-gcode-member-", suffix=".3mf")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            for chunk in backend.read(storage_path):
                tmp_file.write(chunk)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def _resolve_gcode_member(tmp_path: Path, plate: int | None) -> tuple[str, int]:
    """Resolve which zip member (name + uncompressed ``ZipInfo.file_size``,
    for ``Content-Length``) holds the requested plate's embedded
    ``.gcode``, via the same ``model_settings.config`` plate->gcode mapping
    the pipeline's own metadata extraction uses. Defaults to the
    lowest-numbered plate when ``plate`` is omitted. On ANY exception
    (including the 404s raised here), removes ``tmp_path`` -- the caller
    only reaches ownership of cleanup on success, via the streaming
    generator below.
    """
    try:
        with zipfile.ZipFile(tmp_path) as zf:
            model_settings = slicedmeta.read_zip_member(zf, slicedmeta.MODEL_SETTINGS_PATH)
            plate_files = slicedmeta.parse_model_settings(model_settings)
            if not plate_files:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "no embedded gcode in this file")
            chosen = plate if plate is not None else min(plate_files)
            gcode_member = (plate_files.get(chosen) or {}).get("gcode_file")
            if gcode_member is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"no gcode for plate {chosen}")
            try:
                info = zf.getinfo(gcode_member)
            except KeyError as exc:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, "embedded gcode member missing"
                ) from exc
            return gcode_member, info.file_size
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _stream_gcode_member(tmp_path: Path, member: str) -> Iterator[bytes]:
    """Stream one zip member's bytes in fixed-size chunks (review
    finding 1) -- never ``.read()``s the whole (decompressed) member into
    memory -- and removes the spooled temp copy of the whole zip once the
    stream is consumed or abandoned.
    """
    try:
        with zipfile.ZipFile(tmp_path) as zf, zf.open(member) as stream:
            while True:
                chunk = stream.read(_GCODE_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                yield chunk
    finally:
        tmp_path.unlink(missing_ok=True)


async def _download_file(
    file_id: int,
    *,
    url_filename: str | None,
    member: str | None,
    plate: int | None,
    token: str | None,
    inline: bool = False,
    tdmm_session: str | None,
    db: AsyncSession,
    settings: Settings,
) -> StreamingResponse:
    """Shared implementation for both download routes below. Resolves
    THIS file's own primary backend (Workstream C task C2), not a shared
    default -- a file relocated onto a non-default backend is still
    downloadable from wherever its bytes actually are. 409 if the file
    hasn't finished being stored yet (NULL ``verified_at`` and the backend
    object genuinely doesn't exist -- a NULL ``verified_at`` with an
    already-present object, e.g. a race with a scanner, is not treated as
    "processing").

    ``?member=gcode`` (R10-B): for a ``gcode_3mf`` blob, extracts and streams
    the embedded per-plate ``.gcode`` instead of the raw zip -- the
    frontend's ``GcodePreview`` needs a bare gcode stream, and re-deriving it
    at extraction time (rather than a new derivative/table) keeps the
    already-stored zip as the single source of truth.

    ``?token=`` (R10-C): a short-lived signed token minted by
    ``POST /files/{id}/slicer-link`` lets a desktop slicer fetch this URL
    itself with no session cookie. When present it must verify against
    THIS ``file_id`` or the request is rejected; the existing cookie-auth
    path (below, ``require_session`` called directly since this route is
    mounted unauthenticated) is otherwise unchanged.

    ``url_filename`` (review finding 4, filename-bearing route): when given,
    must equal the file's own stored name (else 404) -- a slicer opening a
    signed deep link sees a real extension in the URL itself, not just the
    Content-Type header.
    """
    if token is not None:
        if signed_urls.verify(settings, token) != file_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    else:
        await require_session(tdmm_session=tdmm_session, db=db)

    file = await library.get_live_file(db, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"file {file_id} not found")

    filename = PurePosixPath(file.rel_path).name
    if url_filename is not None and url_filename != filename:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "filename does not match this file")

    backend = await resolve_backend_for_file(db, settings, file)
    if file.verified_at is None:
        exists = await anyio.to_thread.run_sync(backend.exists, file.storage_path)
        if not exists:
            raise HTTPException(status.HTTP_409_CONFLICT, "file is still processing")

    blob = await db.get(Blob, file.blob_hash)

    if member == "gcode":
        if blob is None or blob.format is not BlobFormat.GCODE_3MF:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "member=gcode needs a gcode_3mf file")
        try:
            tmp_path = await anyio.to_thread.run_sync(
                _spool_to_temp_file, backend, file.storage_path
            )
        except StorageKeyNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "file missing from storage") from exc
        gcode_member, file_size = await anyio.to_thread.run_sync(
            _resolve_gcode_member, tmp_path, plate
        )
        return StreamingResponse(
            iterate_in_threadpool(_stream_gcode_member(tmp_path, gcode_member)),
            media_type=_media_type_for_filename("plate.gcode"),
            headers={"Content-Length": str(file_size)},
        )

    try:
        iterator = await anyio.to_thread.run_sync(backend.read, file.storage_path)
    except StorageKeyNotFound as exc:
        # `verified_at` says the backend write succeeded, but the object is
        # gone now -- removed out-of-band (e.g. manual disk edit, a scanner
        # cleanup). That's a 404, not the 409 "still processing" above.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file missing from storage") from exc

    return StreamingResponse(
        iterate_in_threadpool(iterator),
        media_type=_media_type_for_filename(filename),
        headers={
            "Content-Disposition": _content_disposition_for_filename(filename, inline=inline),
            "Content-Length": str(blob.size),
        },
    )


@public_router.get("/{file_id}/download")
async def download_file(
    file_id: int,
    member: str | None = Query(None, description="'gcode' extracts a .gcode.3mf's embedded gcode"),
    plate: int | None = Query(None, description="Plate index for member=gcode; default lowest"),
    token: str | None = Query(
        None,
        description="Signed slicer-deep-link token (POST .../slicer-link); bypasses the cookie",
    ),
    inline: bool = Query(
        False, description="R13c: render pdf/txt/md inline instead of downloading"
    ),
    tdmm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream a file's bytes from the storage backend (Task 6 interface
    decision). See ``_download_file`` for the shared behavior; kept
    filename-less for existing (cookie-auth, member=gcode) callers -- new
    signed slicer-link URLs use ``/{file_id}/download/{filename}`` below.
    """
    return await _download_file(
        file_id,
        url_filename=None,
        member=member,
        plate=plate,
        token=token,
        inline=inline,
        tdmm_session=tdmm_session,
        db=db,
        settings=settings,
    )


@public_router.get("/{file_id}/download/{filename}")
async def download_file_with_filename(
    file_id: int,
    filename: str,
    member: str | None = Query(None, description="'gcode' extracts a .gcode.3mf's embedded gcode"),
    plate: int | None = Query(None, description="Plate index for member=gcode; default lowest"),
    token: str | None = Query(
        None,
        description="Signed slicer-deep-link token (POST .../slicer-link); bypasses the cookie",
    ),
    inline: bool = Query(
        False, description="R13c: render pdf/txt/md inline instead of downloading"
    ),
    tdmm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Filename-bearing variant (review finding 4) of ``download_file``
    above -- this is what ``POST /{file_id}/slicer-link`` now mints, so a
    desktop slicer opening the deep link sees the file's real extension in
    the URL path itself. ``{filename}`` must equal the file's own stored
    name or this 404s (see ``_download_file``'s ``url_filename`` check).
    """
    return await _download_file(
        file_id,
        url_filename=filename,
        member=member,
        plate=plate,
        token=token,
        inline=inline,
        tdmm_session=tdmm_session,
        db=db,
        settings=settings,
    )


class SlicerLinkResponse(BaseModel):
    url: str
    expires_at: datetime


def _absolute_origin(request: Request, cfg: ServerConfig) -> str:
    """Build ``scheme://host`` for the signed URL handed to a desktop
    slicer (review finding 3: the old version trusted client-supplied
    ``X-Forwarded-Proto``/``X-Forwarded-Host``/``Host`` headers with no
    allowlist, letting anyone mint a token embedded in an attacker-chosen
    absolute URL).

    The server settings' ``public_url``, when configured, is
    always authoritative -- it's the operator's own declared externally-
    reachable origin. Otherwise this falls back to ``request.base_url``
    (this request's own scheme/host as uvicorn resolved it) and
    deliberately does NOT read any ``X-Forwarded-*`` header directly: a
    reverse proxy in front of this API should instead be pointed at
    uvicorn's own ``--proxy-headers``/``--forwarded-allow-ips``, which
    parses/validates those headers from only the configured trusted proxy
    IP(s) before this code (or anything else in the app) ever sees the
    request -- that's the one place trusting a forwarded header is safe.
    """
    return public_origin(cfg, request)


@router.post("/{file_id}/slicer-link", response_model=SlicerLinkResponse)
async def create_slicer_link(
    file_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SlicerLinkResponse:
    """Mint a short-lived signed download URL (R10-C, plan item 11) so a
    desktop slicer (OrcaSlicer, Bambu Studio, PrusaSlicer, Elegoo Slicer)
    opened via a ``<scheme>://open?file=<url>`` deep link can fetch the file
    itself with no session cookie. This route stays on the protected router
    (``require_session``) -- only an already-authenticated browser tab can
    mint a link; the link itself is what carries the delegated, unauthenticated
    access to ``GET .../download``.
    """
    file = await library.get_live_file(db, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"file {file_id} not found")

    token = signed_urls.sign_file_download(settings, file_id)
    expires_at = datetime.now(UTC) + timedelta(seconds=signed_urls.DEFAULT_TTL_S)
    origin = _absolute_origin(request, await get_server_config(db))
    filename = quote(PurePosixPath(file.rel_path).name, safe="")
    url = f"{origin}/api/files/{file_id}/download/{filename}?token={quote(token, safe='')}"
    return SlicerLinkResponse(url=url, expires_at=expires_at)
