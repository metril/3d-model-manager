"""T7 archive extras: compare, calendar, purge, outcome confirm, timelapse
editor and photos. Registered BEFORE ``archives.router`` so the static paths
(``/compare``, ``/purge/*`` ...) win over ``/{archive_id}``."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import secrets
import subprocess
import tempfile
import time
import uuid
import warnings
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any

import anyio
import redis.asyncio as aioredis
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.archives import _get_visible, archive_out, archive_select, require_writer
from app.api.deps import AuthContext, require_perm, require_session, visible_printer_ids
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuditLog, PrintArchive, PrintJob
from app.models.enums import ArchiveResult, ArchiveVerdict, BlobFormat, BlobKind, VerdictSource
from app.models.library import Blob
from app.schemas.archives import (
    ArchiveOut,
    CalendarDayOut,
    CompareOut,
    CorrelationOut,
    PhotoOut,
    PurgeCountOut,
    PurgeRunOut,
    PurgeStatsOut,
    SettingsDiffOut,
    TimelapseInfoOut,
    TimelapseJobOut,
    TimelapseJobStateOut,
    TimelapseThumbsOut,
    TimeOut,
)
from app.services import archive_confirm, archive_purge, archive_settings, archive_sources
from app.services.archive import _ensure_blob, archive_path
from app.services.archive_blobs import blob_ext, release_blobs, unlink_all
from app.services.derivatives import publish_bytes
from app.services.tz import parse_tz
from app.tasks.base import sync_session

router = APIRouter(prefix="/archives", tags=["archives"])
public_router = APIRouter(prefix="/archives", tags=["archives"])
settings_router = APIRouter(
    prefix="/settings", tags=["archives"], dependencies=[Depends(require_perm("settings:write"))]
)

# Subscribe the plate-cleared tie-in when T3's hook registry exists.
archive_confirm.register_hooks()


# --- settings groups ----------------------------------------------------------


@settings_router.get("/archive_purge", response_model=archive_settings.PurgeConfig)
async def get_purge_settings(db: AsyncSession = Depends(get_db)) -> archive_settings.PurgeConfig:
    return await archive_settings.get_purge_async(db)


@settings_router.put("/archive_purge", response_model=archive_settings.PurgeConfig)
async def put_purge_settings(
    body: archive_settings.PurgeConfig, db: AsyncSession = Depends(get_db)
) -> archive_settings.PurgeConfig:
    return await archive_settings.set_purge_async(db, body)


@settings_router.get("/confirm", response_model=archive_settings.ConfirmConfig)
async def get_confirm_settings(
    db: AsyncSession = Depends(get_db),
) -> archive_settings.ConfirmConfig:
    return await archive_settings.get_confirm_async(db)


@settings_router.put("/confirm", response_model=archive_settings.ConfirmConfig)
async def put_confirm_settings(
    body: archive_settings.ConfirmConfig, db: AsyncSession = Depends(get_db)
) -> archive_settings.ConfirmConfig:
    return await archive_settings.set_confirm_async(db, body)


# --- compare ------------------------------------------------------------------

_NOT_SETTINGS = {"print_time_s", "filament_g", "filament_m"}


def _eff(a: PrintArchive) -> ArchiveResult:
    return a.result_override or a.result


@router.get("/compare", response_model=CompareOut)
async def compare_archives(
    ids: str = Query(...),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> CompareOut:
    try:
        parsed = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "ids must be integers") from None
    uniq = list(dict.fromkeys(parsed))
    if not 2 <= len(uniq) <= 5:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "compare takes 2 to 5 ids")
    stmt = archive_select().where(PrintArchive.id.in_(uniq))
    visible = visible_printer_ids(ctx)
    if visible is not None:
        stmt = stmt.where(PrintJob.printer_id.in_(visible))
    rows = {r[0].id: r for r in (await db.execute(stmt)).all()}
    if len(rows) != len(uniq):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "archive not found")
    ordered = [rows[i] for i in uniq]
    metas = {r[0].id: (r[0].slicer_meta or {}) for r in ordered}
    keys = sorted({k for m in metas.values() for k in m} - _NOT_SETTINGS)
    diff: list[SettingsDiffOut] = []
    for k in keys:
        vals = {str(i): m.get(k) for i, m in metas.items()}
        if len({json.dumps(v, sort_keys=True, default=str) for v in vals.values()}) > 1:
            diff.append(SettingsDiffOut(key=k, values=vals))

    model_ids = {r[0].model_id for r in ordered if r[0].model_id is not None}
    peers: dict[int, PrintArchive] = {r[0].id: r[0] for r in ordered}
    if model_ids:
        pstmt = (
            select(PrintArchive)
            .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
            .where(PrintArchive.model_id.in_(model_ids))
        )
        if visible is not None:
            pstmt = pstmt.where(PrintJob.printer_id.in_(visible))
        for a in (await db.execute(pstmt)).scalars():
            peers[a.id] = a
    correlation: list[CorrelationOut] = []
    for d in diff:
        wanted = {json.dumps(v, sort_keys=True, default=str): v for v in d.values.values()}
        buckets: dict[str, list[bool]] = defaultdict(list)
        for a in peers.values():
            v = json.dumps((a.slicer_meta or {}).get(d.key), sort_keys=True, default=str)
            if v in wanted:
                buckets[v].append(_eff(a) == ArchiveResult.COMPLETED)
        for v, outcomes in buckets.items():
            correlation.append(
                CorrelationOut(
                    key=d.key,
                    value=wanted[v],
                    success_rate=round(sum(outcomes) / len(outcomes), 4),
                    n=len(outcomes),
                )
            )
    return CompareOut(
        archives=[archive_out(r) for r in ordered],
        settings_diff=diff,
        filament={str(r[0].id): r[0].est_filament for r in ordered},
        time={
            str(r[0].id): TimeOut(
                estimated_s=(r[0].slicer_meta or {}).get("print_time_s"), actual_s=r[0].duration_s
            )
            for r in ordered
        },
        correlation=correlation,
    )


# --- calendar -----------------------------------------------------------------


@router.get("/calendar", response_model=list[CalendarDayOut])
async def calendar(
    from_: Annotated[date, Query(alias="from")],
    to: date,
    tz: str | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[CalendarDayOut]:
    if to < from_ or (to - from_).days > 400:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "range must be 0-400 days")
    zone = parse_tz(tz)
    start = datetime(from_.year, from_.month, from_.day, tzinfo=zone)
    end = datetime(to.year, to.month, to.day, tzinfo=zone) + timedelta(days=1)
    stmt = (
        select(PrintArchive)
        .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
        .where(PrintArchive.created_at >= start, PrintArchive.created_at < end)
    )
    visible = visible_printer_ids(ctx)
    if visible is not None:
        stmt = stmt.where(PrintJob.printer_id.in_(visible))
    days: dict[str, dict[str, float]] = {}
    for a in (await db.execute(stmt)).scalars():
        d = days.setdefault(
            a.created_at.astimezone(zone).date().isoformat(),
            {"count": 0, "ok": 0, "failed": 0, "secs": 0},
        )
        d["count"] += 1
        eff = _eff(a)
        d["ok"] += eff == ArchiveResult.COMPLETED
        d["failed"] += eff == ArchiveResult.FAILED
        d["secs"] += a.duration_s or 0
    return [
        CalendarDayOut(
            date=k,
            count=int(v["count"]),
            ok=int(v["ok"]),
            failed=int(v["failed"]),
            hours=round(v["secs"] / 3600, 2),
        )
        for k, v in sorted(days.items())
    ]


# --- purge --------------------------------------------------------------------


def _preview() -> tuple[int, int]:
    with sync_session() as s:
        return archive_purge.preview(s, archive_settings.get_purge(s))


def _run(settings: Settings) -> tuple[int, int]:
    with sync_session() as s:
        return archive_purge.run_purge(s, settings, archive_settings.get_purge(s))


@router.post(
    "/purge/preview", response_model=PurgeCountOut, dependencies=[Depends(require_perm("admin"))]
)
async def purge_preview() -> PurgeCountOut:
    count, size = await run_in_threadpool(_preview)
    return PurgeCountOut(count=count, bytes=size)


@router.post(
    "/purge/run", response_model=PurgeRunOut, dependencies=[Depends(require_perm("admin"))]
)
async def purge_run(
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> PurgeRunOut:
    deleted, size = await run_in_threadpool(_run, settings)
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            action="archive.purge",
            payload={"deleted": deleted, "bytes": size},
        )
    )
    await db.commit()
    return PurgeRunOut(deleted=deleted, bytes=size)


@router.get("/purge/stats", response_model=PurgeStatsOut)
async def purge_stats(
    db: AsyncSession = Depends(get_db), _ctx: AuthContext = Depends(require_session)
) -> PurgeStatsOut:
    cfg = await archive_settings.get_purge_async(db)
    st = await archive_settings.get_purge_stats_async(db)
    return PurgeStatsOut(**st.model_dump(), next_run="03:30 daily" if cfg.enabled else None)


# --- outcome confirm (public) -------------------------------------------------

RATE_LIMIT = 30


async def _rate_limit(request: Request, settings: Settings = Depends(get_settings)) -> None:
    ip = request.client.host if request.client else "unknown"
    tok = archive_confirm.hash_token(request.path_params.get("token", ""))[:16]
    key = f"confirm:rl:{tok}:{ip}:{int(time.time() // 60)}"
    try:
        client = aioredis.Redis.from_url(settings.redis_url)
        try:
            n = await client.incr(key)
            if n == 1:
                await client.expire(key, 120)
        finally:
            await client.aclose()
    except Exception as e:  # noqa: BLE001 - fail closed when the limiter is down
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many requests") from e
    if n > RATE_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many requests")


def _page(title: str, body: str, script: str = "") -> HTMLResponse:
    doc = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:28rem;margin:4rem auto;"
        "padding:0 1rem;text-align:center}button{font-size:1.1rem;padding:.7rem 1.6rem;"
        "border-radius:.5rem;border:0;background:#2563eb;color:#fff;cursor:pointer}</style>"
        f"</head><body>{body}{script}</body></html>"
    )
    return HTMLResponse(
        doc, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    )


async def _by_token(db: AsyncSession, token: str, verdict: str, lock: bool = False) -> PrintArchive:
    if verdict not in ("good", "bad"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    stmt = select(PrintArchive).where(
        PrintArchive.confirm_token == archive_confirm.hash_token(token)
    )
    if lock:
        stmt = stmt.with_for_update()
    arc = (await db.execute(stmt)).scalars().first()
    if arc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if archive_confirm.token_gone(arc):
        raise HTTPException(status.HTTP_410_GONE, "this link was already used or has expired")
    return arc


@public_router.get(
    "/confirm/{token}/{verdict}",
    response_class=HTMLResponse,
    dependencies=[Depends(_rate_limit)],
)
async def confirm_prompt(
    token: str, verdict: str, tap: int = 0, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    await _by_token(db, token, verdict)
    word = "good" if verdict == "good" else "bad"
    body = (
        f"<h1>Mark this print as {word}?</h1>"
        '<form method="post"><button type="submit">Confirm</button></form>'
    )
    script = "<script>document.forms[0].submit()</script>" if tap else ""
    return _page("Confirm print outcome", body, script)


@public_router.post(
    "/confirm/{token}/{verdict}",
    response_class=HTMLResponse,
    dependencies=[Depends(_rate_limit)],
)
async def confirm_record(
    token: str, verdict: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    arc = await _by_token(db, token, verdict, lock=True)
    run = await db.run_sync(lambda s: archive_confirm.sync_run(s, arc))
    archive_confirm.apply_verdict(arc, run, ArchiveVerdict(verdict), VerdictSource.LINK, None)
    await db.commit()
    return _page(
        "Thanks", f"<h1>Recorded: {html.escape(verdict)}</h1><p>You can close this tab.</p>"
    )


# --- timelapse editor ---------------------------------------------------------

_FFPROBE_TIMEOUT = 30


async def _timelapse(db: AsyncSession, ctx: AuthContext, settings: Settings, aid: int) -> tuple:
    row = await _get_visible(db, ctx, aid)
    ctx.require_printer(row[2].id, "print")
    arc = row[0]
    blob = await db.get(Blob, arc.timelapse_blob) if arc.timelapse_blob else None
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no timelapse")
    path = archive_path(settings, blob.hash, blob_ext(blob.format))
    if not await anyio.to_thread.run_sync(path.exists):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no timelapse")
    return arc, blob, path


def _probe(path: Any) -> dict:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate:format=duration,size",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=_FFPROBE_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ffprobe unavailable") from e
    return json.loads(out.stdout)


def _fps(raw: str | None) -> float:
    try:
        n, _, d = (raw or "0/1").partition("/")
        return round(float(n) / float(d or 1), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


@router.get("/{archive_id}/timelapse/info", response_model=TimelapseInfoOut)
async def timelapse_info(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> TimelapseInfoOut:
    arc, blob, path = await _timelapse(db, ctx, settings, archive_id)
    info = await anyio.to_thread.run_sync(_probe, path)
    st = (info.get("streams") or [{}])[0]
    fmt = info.get("format") or {}
    return TimelapseInfoOut(
        duration_s=float(fmt.get("duration") or 0),
        fps=_fps(st.get("r_frame_rate")),
        width=int(st.get("width") or 0),
        height=int(st.get("height") or 0),
        bytes=int(fmt.get("size") or blob.size),
        frames=arc.timelapse_frames,
        layers=arc.timelapse_layers,
    )


def _frames(path: Any, duration: float, n: int) -> list[str]:
    out: list[str] = []
    for i in range(n):
        t = max(duration * (i + 0.5) / n, 0)
        try:
            r = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=160:-2",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "-q:v",
                    "6",
                    "pipe:1",
                ],  # fmt: skip
                check=True,
                capture_output=True,
                timeout=_FFPROBE_TIMEOUT,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ffmpeg unavailable") from e
        if r.stdout:
            out.append("data:image/jpeg;base64," + base64.b64encode(r.stdout).decode())
    return out


@router.get("/{archive_id}/timelapse/thumbnails", response_model=TimelapseThumbsOut)
async def timelapse_thumbnails(
    archive_id: int,
    n: int = Query(12, ge=1, le=24),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> TimelapseThumbsOut:
    _arc, blob, path = await _timelapse(db, ctx, settings, archive_id)
    key = f"tl:thumbs:{blob.hash}:{n}"
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        cached = await client.get(key)
        if cached:
            return TimelapseThumbsOut(frames=json.loads(cached))
        info = await anyio.to_thread.run_sync(_probe, path)
        dur = float((info.get("format") or {}).get("duration") or 0)
        frames = await anyio.to_thread.run_sync(_frames, path, dur, n)
        await client.set(key, json.dumps(frames), ex=3600)
    finally:
        await client.aclose()
    return TimelapseThumbsOut(frames=frames)


def job_key(job_id: str) -> str:
    return f"tl:job:{job_id}"


@router.post(
    "/{archive_id}/timelapse/process",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TimelapseJobOut,
    dependencies=[Depends(require_perm("printers:print"))],
)
async def timelapse_process(
    archive_id: int,
    trim_start: float = Form(0, ge=0),
    trim_end: float | None = Form(None, gt=0),
    speed: float = Form(1.0, ge=0.25, le=4.0),
    save_mode: str = Form("replace", pattern="^(replace|new)$"),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> TimelapseJobOut:
    arc, blob, path = await _timelapse(db, ctx, settings, archive_id)
    if blob.format == BlobFormat.AVI:
        raise HTTPException(status.HTTP_409_CONFLICT, "timelapse is still being transcoded")
    if trim_end is not None and trim_end <= trim_start:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "trim_end must exceed trim_start"
        )
    info = await anyio.to_thread.run_sync(_probe, path)
    dur = float((info.get("format") or {}).get("duration") or 0)
    if dur and (trim_start >= dur or (trim_end is not None and trim_end > dur + 0.05)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "trim exceeds video duration")
    from app.tasks.media import timelapse_edit

    job_id = uuid.uuid4().hex
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        await client.set(
            job_key(job_id),
            json.dumps({"archive_id": arc.id, "state": "pending", "error": None, "filename": None}),
            ex=86400,
        )
    finally:
        await client.aclose()
    timelapse_edit.delay(arc.id, job_id, trim_start, trim_end, speed, save_mode, ctx.user.id)
    return TimelapseJobOut(job_id=job_id)


@router.get("/{archive_id}/timelapse/jobs/{job_id}", response_model=TimelapseJobStateOut)
async def timelapse_job(
    archive_id: int,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> TimelapseJobStateOut:
    row = await _get_visible(db, ctx, archive_id)
    ctx.require_printer(row[2].id, "print")
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        raw = await client.get(job_key(job_id))
    finally:
        await client.aclose()
    data = json.loads(raw) if raw else None
    if data is None or data.get("archive_id") != archive_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return TimelapseJobStateOut(**{k: data.get(k) for k in ("state", "error", "filename")})


# --- photos -------------------------------------------------------------------

MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_PHOTO_PX = 4096
MAX_PHOTO_PIXELS = 40_000_000
_PHOTO_FORMATS = {
    "JPEG": ("jpg", BlobFormat.JPG),
    "PNG": ("png", BlobFormat.PNG),
    "WEBP": ("webp", BlobFormat.WEBP),
}
_MEDIA = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "mp4": "video/mp4"}


def _reencode(data: bytes) -> tuple[bytes, str, BlobFormat]:
    """Validate + re-encode (strips EXIF/metadata), capped at 4096 px."""
    try:
        with warnings.catch_warnings():
            # Pillow only warns between 1x and 2x MAX_IMAGE_PIXELS; make that fatal too.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(BytesIO(data))
            fmt = img.format or ""
            if fmt not in _PHOTO_FORMATS:
                raise ValueError(fmt)
            if img.width * img.height > MAX_PHOTO_PIXELS:
                raise ValueError("too many pixels")
            img.load()
    except (
        UnidentifiedImageError,
        ValueError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as e:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "only jpeg, png or webp images"
        ) from e
    img = ImageOps.exif_transpose(img)
    img.thumbnail((MAX_PHOTO_PX, MAX_PHOTO_PX))
    ext, bfmt = _PHOTO_FORMATS[fmt]
    if fmt == "JPEG":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
    elif img.mode in ("P", "PA", "LA"):
        img = img.convert("RGBA")
    elif img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGB")
    img.info = {}  # no EXIF / text chunks / ICC carried into the output
    buf = BytesIO()
    img.save(buf, format=fmt, **({"quality": 90, "exif": b""} if fmt != "PNG" else {}))
    return buf.getvalue(), ext, bfmt


def _publish_photo(data: bytes, settings: Settings, ext: str) -> str:
    sha = hashlib.sha256(data).hexdigest()
    publish_bytes(data, archive_path(settings, sha, ext))
    return sha


@router.post("/{archive_id}/photos", response_model=PhotoOut, status_code=status.HTTP_201_CREATED)
async def upload_photo(
    archive_id: int,
    file: Annotated[UploadFile, File()],
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> PhotoOut:
    arc, job, printer, _m, _r = await _get_visible(db, ctx, archive_id)
    require_writer(ctx, job, printer)
    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "photo exceeds 15 MB")
    out, ext, bfmt = await anyio.to_thread.run_sync(_reencode, data)
    sha = await anyio.to_thread.run_sync(_publish_photo, out, settings, ext)
    await db.run_sync(lambda s: _ensure_blob(s, sha, len(out), BlobKind.IMAGE, bfmt))
    # Lock the row so concurrent uploads/deletes don't lose each other's writes.
    await db.refresh(arc, with_for_update=True)
    item = {
        "filename": f"{secrets.token_hex(6)}.{ext}",
        "blob": sha,
        "kind": "photo",
        "uploaded_by": ctx.user.id,
        "at": datetime.now(UTC).isoformat(),
    }
    arc.photos = [*(arc.photos or []), item]
    await db.commit()
    return PhotoOut(**{k: item[k] for k in ("filename", "kind", "uploaded_by", "at")})


@router.get("/{archive_id}/photos", response_model=list[PhotoOut])
async def list_photos(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[PhotoOut]:
    return archive_out(await _get_visible(db, ctx, archive_id)).photos


def _find_photo(arc: PrintArchive, filename: str) -> dict:
    for p in arc.photos or []:
        if p.get("filename") == filename:
            return p
    raise HTTPException(status.HTTP_404_NOT_FOUND, "photo not found")


@router.get("/{archive_id}/photos/{filename}")
async def get_photo(
    archive_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    arc = (await _get_visible(db, ctx, archive_id))[0]
    item = _find_photo(arc, filename)
    blob = await db.get(Blob, item["blob"])
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "photo not found")
    ext = blob_ext(blob.format)
    path = archive_path(settings, blob.hash, ext)
    if not await anyio.to_thread.run_sync(path.exists):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "photo not found")
    return FileResponse(path, media_type=_MEDIA.get(ext, "application/octet-stream"))


@router.delete("/{archive_id}/photos/{filename}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_photo(
    archive_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    arc, job, printer, _m, _r = await _get_visible(db, ctx, archive_id)
    require_writer(ctx, job, printer)
    await db.refresh(arc, with_for_update=True)
    item = _find_photo(arc, filename)
    arc.photos = [p for p in arc.photos if p.get("filename") != filename]
    await db.flush()
    _freed, paths = await db.run_sync(lambda s: release_blobs(s, settings, {item["blob"]}))
    await db.commit()
    await anyio.to_thread.run_sync(unlink_all, paths)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- source attachments (source project / Fusion F3D) -------------------------


async def _stage_upload(file: UploadFile, settings: Settings) -> tuple[Path, str, int]:
    """Stream the upload to a staged temp file, hashing and enforcing the size
    cap as it goes (never buffers the body). Returns ``(path, sha256, size)``."""
    limit = archive_sources.MAX_SOURCE_BYTES
    h = hashlib.sha256()
    size = 0
    stage = await anyio.to_thread.run_sync(archive_sources.stage_dir, settings)
    fd, tmp_name = await anyio.to_thread.run_sync(lambda: tempfile.mkstemp(dir=stage, prefix="up-"))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "source exceeds 256 MB")
                h.update(chunk)
                await anyio.to_thread.run_sync(fh.write, chunk)
        if size == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "empty file")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, h.hexdigest(), size


def _slot_mutation(
    archive_id: int,
    slot: str,
    settings: Settings,
    staged: tuple[Path, str, int] | None,
    name: str,
):
    """Attach (staged upload) or clear (None) a slot in a worker-thread session."""
    try:
        with sync_session() as s:
            arc = s.get(PrintArchive, archive_id)
            if arc is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "archive not found")
            if staged is None:
                paths = archive_sources.clear(s, settings, arc, slot)  # type: ignore[arg-type]
            else:
                tmp, sha, size = staged
                paths = archive_sources.store_path(
                    s,
                    settings,
                    arc,
                    slot,  # type: ignore[arg-type]
                    tmp,
                    sha,
                    size,
                    name,
                )
            s.commit()
            for p in paths:
                p.unlink(missing_ok=True)
    finally:
        if staged is not None:
            staged[0].unlink(missing_ok=True)


def _register_source_slot(slot: str) -> None:
    @router.get(f"/{{archive_id}}/{slot}", name=f"download_{slot}")
    async def download(
        archive_id: int,
        db: AsyncSession = Depends(get_db),
        ctx: AuthContext = Depends(require_session),
        settings: Settings = Depends(get_settings),
    ) -> FileResponse:
        arc = (await _get_visible(db, ctx, archive_id))[0]
        sha, name = archive_sources.get(arc, slot)  # type: ignore[arg-type]
        blob = await db.get(Blob, sha) if sha else None
        if blob is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no {slot} attached")
        path = archive_path(settings, blob.hash, blob_ext(blob.format))
        if not await anyio.to_thread.run_sync(path.exists):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{slot} file missing")
        return FileResponse(path, media_type="application/octet-stream", filename=name)

    @router.put(f"/{{archive_id}}/{slot}", response_model=ArchiveOut, name=f"put_{slot}")
    async def put(
        archive_id: int,
        file: Annotated[UploadFile, File()],
        db: AsyncSession = Depends(get_db),
        ctx: AuthContext = Depends(require_session),
        settings: Settings = Depends(get_settings),
    ) -> ArchiveOut:
        _arc, job, printer, _m, _r = await _get_visible(db, ctx, archive_id)
        require_writer(ctx, job, printer)
        staged = await _stage_upload(file, settings)
        await db.rollback()  # release the read tx before the worker-thread write
        await anyio.to_thread.run_sync(
            lambda: _slot_mutation(archive_id, slot, settings, staged, file.filename or slot)
        )
        return archive_out(await _get_visible(db, ctx, archive_id))

    @router.delete(f"/{{archive_id}}/{slot}", response_model=ArchiveOut, name=f"delete_{slot}")
    async def delete(
        archive_id: int,
        db: AsyncSession = Depends(get_db),
        ctx: AuthContext = Depends(require_session),
        settings: Settings = Depends(get_settings),
    ) -> ArchiveOut:
        _arc, job, printer, _m, _r = await _get_visible(db, ctx, archive_id)
        require_writer(ctx, job, printer)
        await db.rollback()
        await anyio.to_thread.run_sync(lambda: _slot_mutation(archive_id, slot, settings, None, ""))
        return archive_out(await _get_visible(db, ctx, archive_id))


for _slot in archive_sources.SLOTS:
    _register_source_slot(_slot)


def _relink(archive_id: int, settings: Settings) -> list[str]:
    with sync_session() as s:
        arc = s.get(PrintArchive, archive_id)
        if arc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "archive not found")
        filled = archive_sources.link_from_model(s, settings, arc)
        s.commit()
        return list(filled)


@router.post("/{archive_id}/sources/link-model", response_model=ArchiveOut)
async def link_sources_from_model(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> ArchiveOut:
    """Fill empty source slots from the archive's library model (never from a
    trashed one). 404 when nothing could be linked."""
    arc, job, printer, _m, _r = await _get_visible(db, ctx, archive_id)
    require_writer(ctx, job, printer)
    await db.rollback()
    filled = await anyio.to_thread.run_sync(lambda: _relink(archive_id, settings))
    if not filled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no source found on the library model")
    return archive_out(await _get_visible(db, ctx, archive_id))
