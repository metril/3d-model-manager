"""OrcaSlicer (Studio) sidecar sessions: open a library file in Studio, inspect/end
the active session. Returned files are ingested by ``app.tasks.studio_exchange``.
Error bodies follow the house convention ``{"detail": {"code": ...}}``.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.services import events, library, studio_sessions
from app.services.server_config import ServerConfig, get_server_config

router = APIRouter(tags=["studio"])


async def _redis(settings: Settings = Depends(get_settings)) -> AsyncIterator[aioredis.Redis]:
    r = aioredis.Redis.from_url(settings.redis_url)
    try:
        yield r
    finally:
        await r.aclose()


async def _studio_running(cfg: ServerConfig) -> bool:
    if not cfg.studio_enabled:
        return False
    headers = {}
    if cfg.studio_launcher_token is not None:
        headers["X-Launcher-Token"] = cfg.studio_launcher_token.get_secret_value()
    with contextlib.suppress(httpx.HTTPError, ValueError):
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as c:
            resp = await c.get(f"{cfg.studio_launcher_url.rstrip('/')}/health", headers=headers)
        return resp.status_code == 200 and bool(resp.json().get("studio_running"))
    return False


@router.post(
    "/models/{slug}/files/{file_id}/studio",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_perm("admin"))],
)
async def open_in_studio(
    slug: str,
    file_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    r: aioredis.Redis = Depends(_redis),
    ctx: AuthContext = Depends(require_session),
) -> dict:
    model = await library.get_model_by_slug(db, slug)
    file = await library.get_live_file(db, file_id)
    revision = await library.get_revision_or_404(db, file.revision_id) if file else None
    if file is None or revision is None or revision.model_id != model.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    if not file.rel_path.lower().endswith(studio_sessions.ARRANGEABLE_EXTS):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "file type cannot be opened in Studio")
    cfg = await get_server_config(db)
    if not cfg.studio_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "studio_disabled"})
    created = await studio_sessions.create(
        db, settings, cfg, r, model=model, revision=revision, file=file, user_id=ctx.user.id
    )
    await events.publish_event(
        settings.redis_url,
        {
            "type": "studio.session_started",
            "token": created.token,
            "model_slug": model.slug,
            "file_name": created.file_name,
        },
    )
    return {
        "token": created.token,
        "iframe_path": "/slicer/",
        "replaced_session": created.replaced,
        "file": {"id": file.id, "name": created.file_name},
    }


@router.get(
    "/studio/session",
    response_model=None,
    dependencies=[Depends(require_perm("admin"))],
)
async def get_session(
    db: AsyncSession = Depends(get_db), r: aioredis.Redis = Depends(_redis)
) -> Response | dict:
    rec = await studio_sessions.get_active(r)
    if rec is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    cfg = await get_server_config(db)
    return {
        "token": rec["token"],
        "model_slug": rec["model_slug"],
        "model_id": rec["model_id"],
        "revision_id": rec["revision_id"],
        "file": {"id": rec["file_id"], "name": rec["file_name"]},
        "created_at": rec["created_at"],
        "expires_at": rec["expires_at"],
        "studio_running": await _studio_running(cfg),
    }


@router.delete(
    "/studio/session",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("admin"))],
)
async def end_session(
    db: AsyncSession = Depends(get_db), r: aioredis.Redis = Depends(_redis)
) -> Response:
    await studio_sessions.end(await get_server_config(db), r)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
