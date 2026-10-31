"""OrcaSlicer (Studio) sidecar sessions (Redis-backed, one active at a time).

``create`` copies a library file to ``<exchange>/<token>/in/<name>``, records
the session under ``tdmm:studio:session:<token>`` (24 h TTL), asks the
launcher to open the file and then points ``tdmm:studio:active`` at it. Files
Studio saves under ``<exchange>/<token>/`` are ingested by
``app.tasks.studio_exchange``. The api and studio containers must run as the
same PUID/PGID (compose) so each can read/write the other's files.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import httpx
import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.library import File, Model, Revision
from app.services.server_config import ServerConfig
from app.services.slicer_naming import _safe_basename
from app.services.storage_backends import resolve_backend_for_file

SESSION_TTL_S = 24 * 3600
ACTIVE_KEY = "tdmm:studio:active"
ARRANGEABLE_EXTS = (".3mf", ".stl", ".obj", ".step", ".stp")
LAUNCHER_TIMEOUT_S = 10.0
CREATE_LOCK_KEY = "tdmm:studio:create-lock"


def session_key(token: str) -> str:
    return f"tdmm:studio:session:{token}"


@dataclass(frozen=True, slots=True)
class CreatedSession:
    token: str
    replaced: bool
    file_name: str


def _copy_in(backend, storage_path: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for d in (dest.parent.parent, dest.parent):
        d.chmod(0o775)  # mkdir's mode is umask-masked
    with dest.open("wb") as fh:
        for chunk in backend.read(storage_path):
            fh.write(chunk)
    dest.chmod(0o664)


async def _launcher(cfg: ServerConfig, method: str, path: str, **kw) -> httpx.Response:
    headers = {}
    if cfg.studio_launcher_token is not None:
        headers["X-Launcher-Token"] = cfg.studio_launcher_token.get_secret_value()
    try:
        async with httpx.AsyncClient(timeout=LAUNCHER_TIMEOUT_S, follow_redirects=False) as c:
            return await c.request(
                method, f"{cfg.studio_launcher_url.rstrip('/')}{path}", headers=headers, **kw
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail={"code": "launcher_unreachable"}
        ) from exc


async def create(
    db: AsyncSession,
    settings: Settings,
    cfg: ServerConfig,
    r: aioredis.Redis,
    *,
    model: Model,
    revision: Revision,
    file: File,
    user_id: int,
) -> CreatedSession:
    if not await r.set(CREATE_LOCK_KEY, "1", nx=True, ex=30):
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "studio_busy"})
    try:
        return await _create(db, settings, cfg, r, model, revision, file, user_id)
    finally:
        await r.delete(CREATE_LOCK_KEY)


async def _create(db, settings, cfg, r, model, revision, file, user_id) -> CreatedSession:
    token = secrets.token_urlsafe(16)
    name = _safe_basename(file.rel_path)
    root = settings.studio_exchange_dir / token
    backend = await resolve_backend_for_file(db, settings, file)
    record = {
        "model_id": model.id,
        "model_slug": model.slug,
        "revision_id": revision.id,
        "file_id": file.id,
        "file_name": name,
        "source_hash": file.blob_hash,
        "user_id": user_id,
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        await anyio.to_thread.run_sync(_copy_in, backend, file.storage_path, root / "in" / name)
        await r.set(session_key(token), json.dumps(record), ex=SESSION_TTL_S)
        resp = await _launcher(cfg, "POST", "/open", json={"path": f"/exchange/{token}/in/{name}"})
        if resp.status_code == 409:
            raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "launcher_busy"})
        if resp.status_code != 202:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, detail={"code": "launcher_unreachable"}
            )
    except BaseException:
        await r.delete(session_key(token))
        await anyio.to_thread.run_sync(lambda: shutil.rmtree(root, ignore_errors=True))
        raise
    # The previous session's record is left to expire so a late save still lands.
    prev = await r.getset(ACTIVE_KEY, token)
    await r.expire(ACTIVE_KEY, SESSION_TTL_S)
    return CreatedSession(token=token, replaced=prev is not None, file_name=name)


async def get_active(r: aioredis.Redis) -> dict | None:
    token = await r.get(ACTIVE_KEY)
    if token is None:
        return None
    token = token.decode() if isinstance(token, bytes) else token
    raw = await r.get(session_key(token))
    if raw is None:
        await r.delete(ACTIVE_KEY)
        return None
    rec = json.loads(raw)
    created = datetime.fromisoformat(rec["created_at"])
    rec["token"] = token
    rec["expires_at"] = (created + timedelta(seconds=SESSION_TTL_S)).isoformat()
    return rec


async def end(cfg: ServerConfig, r: aioredis.Redis) -> None:
    """Stop Studio and clear the active pointer. The session record stays
    (until its TTL) so a late save still lands; files stay for the sweeper."""
    if cfg.studio_enabled:
        # clearing our state matters more than a dead launcher
        with contextlib.suppress(HTTPException):
            await _launcher(cfg, "POST", "/stop")
    await r.delete(ACTIVE_KEY)
