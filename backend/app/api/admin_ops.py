"""Q7 admin ops: backups, support bundle (admin only); ops settings and the
update badge data (``settings:write`` / any session)."""

from __future__ import annotations

import io
import json
import platform
import re
import sys
import zipfile
from datetime import UTC, datetime
from importlib import metadata

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm
from app.config import Settings, get_settings
from app.db import get_db
from app.logging_config import RING
from app.models import BackupRun, Setting
from app.schemas.ops import (
    BackupRunOut,
    OpsSettingsIn,
    OpsSettingsOut,
    RestoreSettingsOut,
    UpdateOut,
)
from app.services import backup as backup_svc
from app.services.ops_settings import get_ops_config, get_update_cache, set_ops_config
from app.tasks import base
from app.tasks.ops import backup_run

router = APIRouter(
    prefix="/admin", tags=["admin-ops"], dependencies=[Depends(require_perm("admin"))]
)
settings_router = APIRouter(
    prefix="/settings/ops",
    tags=["ops-settings"],
    dependencies=[Depends(require_perm("settings:write"))],
)
_admin = Depends(require_perm("admin"))
system_router = APIRouter(prefix="/system", tags=["system"])

_SECRET_RE = re.compile(
    r"(token|password|secret|_enc$|api_?key|access_?key|private|credential)", re.I
)


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_RE.search(k) and v else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# --- backups -----------------------------------------------------------


@router.post("/backups/run", response_model=BackupRunOut, status_code=status.HTTP_202_ACCEPTED)
async def run_backup(ctx: AuthContext = _admin, db: AsyncSession = Depends(get_db)) -> BackupRun:
    running = await db.scalar(select(BackupRun.id).where(BackupRun.status == "running").limit(1))
    if running is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a backup is already running")
    run = BackupRun(kind="manual", status="running", created_by_user_id=ctx.user.id)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    run_id = run.id
    backup_run.delay(run_id)
    db.expire_all()
    return await db.get(BackupRun, run_id)


@router.get("/backups", response_model=list[BackupRunOut])
async def list_backups(db: AsyncSession = Depends(get_db)) -> list[BackupRun]:
    return list(
        (await db.scalars(select(BackupRun).order_by(BackupRun.id.desc()).limit(100))).all()
    )


def _restore(run_id: int, settings: Settings) -> int:
    with base.sync_session() as s:
        run = s.get(BackupRun, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found")
        try:
            return backup_svc.restore_settings(s, settings, run)
        except backup_svc.BackupError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.post("/backups/{run_id}/restore-settings", response_model=RestoreSettingsOut)
async def restore_settings(
    run_id: int, settings: Settings = Depends(get_settings)
) -> RestoreSettingsOut:
    return RestoreSettingsOut(restored=await run_in_threadpool(_restore, run_id, settings))


# --- support bundle ----------------------------------------------------


def _versions(settings: Settings, alembic_rev: str | None) -> dict:
    pkgs = {}
    for name in ("fastapi", "sqlalchemy", "celery", "redis", "httpx", "paho-mqtt", "alembic"):
        try:
            pkgs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pkgs[name] = None
    return {
        "app_version": settings.app_version,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "alembic_revision": alembic_rev,
        "packages": pkgs,
    }


@router.get("/support-bundle")
async def support_bundle(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> Response:
    rows = (await db.scalars(select(Setting).order_by(Setting.key))).all()
    redacted = {r.key: redact(r.value) for r in rows}
    try:
        rev = await db.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception:  # noqa: BLE001
        rev = None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("settings.json", json.dumps(redacted, indent=2, sort_keys=True))
        z.writestr("versions.json", json.dumps(_versions(settings, rev), indent=2))
        z.writestr("logs.txt", "\n".join(list(RING.lines)[-1000:]) + "\n")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="tdmm-support-{stamp}.zip"'},
    )


# --- ops settings ------------------------------------------------------


async def _settings_out(db: AsyncSession, settings: Settings) -> OpsSettingsOut:
    c = await get_ops_config(db, settings)
    return OpsSettingsOut(
        backup_enabled=c.backup_enabled,
        backup_interval_hours=c.backup_interval_hours,
        backup_retention=c.backup_retention,
        git_provider=c.git_provider,
        git_base_url=c.git_base_url,
        git_repo=c.git_repo,
        git_branch=c.git_branch,
        git_token_set=bool(c.git_token),
        update_check_enabled=c.update_check_enabled,
    )


@settings_router.get("", response_model=OpsSettingsOut)
async def get_ops_settings(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> OpsSettingsOut:
    return await _settings_out(db, settings)


@settings_router.put("", response_model=OpsSettingsOut)
async def put_ops_settings(
    payload: OpsSettingsIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OpsSettingsOut:
    patch = payload.model_dump(exclude_unset=True)
    for k in ("backup_enabled", "backup_interval_hours", "backup_retention", "git_branch"):
        if patch.get(k) is None:
            patch.pop(k, None)
    await set_ops_config(db, settings, patch)
    return await _settings_out(db, settings)


# --- update badge ------------------------------------------------------


@system_router.get("/update", response_model=UpdateOut)
async def get_update(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> UpdateOut:
    cache = await get_update_cache(db) or {}
    return UpdateOut(
        latest=cache.get("latest"),
        current=settings.app_version,
        url=cache.get("url"),
        notes=cache.get("notes"),
        update_available=bool(cache.get("update_available")),
        checked_at=cache.get("checked_at"),
        error=cache.get("error"),
    )
