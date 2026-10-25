"""G-code extraction for the toolpath viewer (T8b-be).

``GET /archives/{id}/gcode`` and ``/gcode/plates`` read ``Metadata/plate_N.gcode``
out of the archived ``.gcode.3mf``; ``GET /printers/{id}/files/gcode`` does the
same for a file on the printer's SD card (downloaded to a temp file first).
The zip member is streamed in chunks (never fully loaded) and gzip-compressed
when the client accepts it.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.api.archives import _get_visible
from app.api.deps import (
    AuthContext,
    require_printer_access,
    require_printer_enabled,
    require_session,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Printer
from app.printers.connection import connection_from_printer
from app.printers.registry import build_adapter
from app.services.archive import archive_path

router = APIRouter(tags=["archives"])

_PLATE_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")
_CHUNK = 256 * 1024
_MEDIA = "text/plain; charset=utf-8"


class GcodePlateOut(BaseModel):
    plate: int
    bytes: int


def _plates(path: Path) -> dict[int, int]:
    """``{plate: uncompressed bytes}``; empty for a missing/corrupt archive."""
    try:
        with zipfile.ZipFile(path) as zf:
            return {
                int(m.group(1)): i.file_size
                for i in zf.infolist()
                if (m := _PLATE_RE.match(i.filename))
            }
    except (zipfile.BadZipFile, OSError):
        return {}


def _chunks(
    path: Path, member: str | None, gzip: bool, on_close: Callable[[], None] | None = None
) -> Iterator[bytes]:
    comp = zlib.compressobj(6, zlib.DEFLATED, 31) if gzip else None
    zf = zipfile.ZipFile(path) if member else None
    fh = zf.open(member) if zf and member else path.open("rb")
    try:
        while chunk := fh.read(_CHUNK):
            out = comp.compress(chunk) if comp else chunk
            if out:
                yield out
        if comp:
            yield comp.flush()
    finally:
        fh.close()
        if zf:
            zf.close()
        if on_close:
            on_close()


def _accepts_gzip(header: str) -> bool:
    for part in header.lower().split(","):
        coding, _, params = part.partition(";")
        if coding.strip() not in ("gzip", "*"):
            continue
        q = 1.0
        for param in params.split(";"):
            k, _, v = param.partition("=")
            if k.strip() == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 0.0
        if q > 0:
            return True
    return False


def _stream(
    request: Request,
    path: Path,
    member: str | None,
    cleanup: Callable[[], None] | None = None,
) -> StreamingResponse:
    gzip = _accepts_gzip(request.headers.get("accept-encoding", ""))
    headers = {"Vary": "Accept-Encoding", "Cache-Control": "private, max-age=300"}
    if gzip:
        headers["Content-Encoding"] = "gzip"
    return StreamingResponse(
        _chunks(path, member, gzip, cleanup),
        media_type=_MEDIA,
        headers=headers,
        background=BackgroundTask(cleanup) if cleanup else None,
    )


async def _archive_3mf(
    db: AsyncSession, ctx: AuthContext, archive_id: int, settings: Settings
) -> tuple[Path, int | None]:
    row = await _get_visible(db, ctx, archive_id)
    blob = row[0].archive_blob
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not available")
    path = archive_path(settings, blob, "gcode.3mf")
    if not await anyio.to_thread.run_sync(path.exists):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not available")
    return path, row[0].plate or row[1].plate


@router.get("/archives/{archive_id}/gcode/plates", response_model=list[GcodePlateOut])
async def archive_gcode_plates(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> list[GcodePlateOut]:
    path, _ = await _archive_3mf(db, ctx, archive_id, settings)
    plates = await anyio.to_thread.run_sync(_plates, path)
    return [GcodePlateOut(plate=p, bytes=b) for p, b in sorted(plates.items())]


@router.get("/archives/{archive_id}/gcode")
async def archive_gcode(
    request: Request,
    archive_id: int,
    plate: int | None = Query(None, ge=1),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    path, default_plate = await _archive_3mf(db, ctx, archive_id, settings)
    n = plate or default_plate or 1
    if n not in await anyio.to_thread.run_sync(_plates, path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no gcode for plate {n}")
    return _stream(request, path, f"Metadata/plate_{n}.gcode")


def _clean_remote_path(raw: str) -> str:
    if "\x00" in raw or not raw.startswith("/"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "path must be absolute")
    parts = PurePosixPath(raw).parts[1:]
    if not parts or ".." in parts or "\\" in raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid path")
    low = raw.lower()
    if not (low.endswith(".3mf") or low.endswith(".gcode")):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "only .3mf / .gcode files")
    return "/" + "/".join(parts)


@router.get(
    "/printers/{printer_id}/files/gcode",
    dependencies=[Depends(require_printer_enabled), Depends(require_printer_access("control"))],
)
async def printer_file_gcode(
    request: Request,
    printer_id: int,
    path: str = Query(...),
    plate: int | None = Query(None, ge=1),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    remote = _clean_remote_path(path)
    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    adapter = build_adapter(printer.kind, connection_from_printer(settings, printer))
    tmp = Path(tempfile.mkdtemp(prefix="tdmm-gcode-"))
    local = tmp / "file"

    def cleanup() -> None:
        shutil.rmtree(tmp, ignore_errors=True)

    try:
        try:
            await anyio.to_thread.run_sync(adapter.download_file, remote, local)
        except FileNotFoundError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found on printer") from e
        except NotImplementedError as e:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "unsupported") from e
        except OSError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "printer download failed") from e
        if remote.lower().endswith(".gcode"):
            return _stream(request, local, None, cleanup)
        plates = await anyio.to_thread.run_sync(_plates, local)
        n = plate or (min(plates) if plates else 1)
        if n not in plates:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no gcode for plate {n}")
        return _stream(request, local, f"Metadata/plate_{n}.gcode", cleanup)
    except BaseException:
        cleanup()
        raise
