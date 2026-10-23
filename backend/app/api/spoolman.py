"""Spoolman connector API (Q9). Every route needs ``inventory:write``."""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.db import get_db
from app.models import Spool, SpoolmanSyncState
from app.services import spoolman
from app.tasks import base

router = APIRouter(
    prefix="/spoolman",
    tags=["spoolman"],
    dependencies=[Depends(require_perm("inventory:write"))],
)


class SpoolmanSettings(BaseModel):
    url: str | None = None
    mode: str = "off"

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in spoolman.MODES:
            raise ValueError(f"mode must be one of {', '.join(spoolman.MODES)}")
        return v


class TestIn(BaseModel):
    url: str | None = None


class LinkIn(BaseModel):
    spool_id: int
    spoolman_id: int


class UnlinkIn(BaseModel):
    spool_id: int


def _out(cfg: spoolman.SpoolmanConfig) -> SpoolmanSettings:
    return SpoolmanSettings(url=cfg.url, mode=cfg.mode)


@router.get("/settings", response_model=SpoolmanSettings)
async def get_settings(db: AsyncSession = Depends(get_db)) -> SpoolmanSettings:
    return _out(await spoolman.get_config(db))


@router.put("/settings", response_model=SpoolmanSettings)
async def put_settings(
    payload: SpoolmanSettings, db: AsyncSession = Depends(get_db)
) -> SpoolmanSettings:
    if payload.mode != "off" and not (payload.url or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "url required unless mode=off")
    return _out(await spoolman.set_config(db, url=payload.url, mode=payload.mode))


@router.post("/test")
async def test_connection(payload: TestIn, db: AsyncSession = Depends(get_db)) -> dict:
    url = payload.url or (await spoolman.get_config(db)).url
    if not url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no Spoolman URL configured")
    try:
        info = await spoolman.test_connection(url)
    except spoolman.SpoolmanError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "version": info.get("version")}


@router.get("/spools")
async def remote_spools(db: AsyncSession = Depends(get_db)) -> list[dict]:
    cfg = await spoolman.get_config(db)
    if not cfg.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Spoolman is not enabled")
    try:
        remote = await spoolman.list_remote_spools(cfg.url)
    except spoolman.SpoolmanError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Spoolman unreachable: {e}") from e
    return [spoolman.remote_summary(r) for r in remote]


@router.post("/link", status_code=status.HTTP_204_NO_CONTENT)
async def link(
    payload: LinkIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    spool = await db.get(Spool, payload.spool_id)
    if spool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "spool not found")
    spool.spoolman_id = payload.spoolman_id
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Spoolman spool already linked") from e
    cfg = await spoolman.get_config(db)
    if cfg.active:  # seed sync state (only touched when enabled)
        db.add(SpoolmanSyncState(spool_id=spool.id, created_by_user_id=ctx.user.id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()


@router.post("/unlink", status_code=status.HTTP_204_NO_CONTENT)
async def unlink(payload: UnlinkIn, db: AsyncSession = Depends(get_db)) -> None:
    spool = await db.get(Spool, payload.spool_id)
    if spool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "spool not found")
    spool.spoolman_id = None
    st = await db.get(SpoolmanSyncState, spool.id)
    if st is not None:
        await db.delete(st)
    await db.commit()


@router.post("/sync")
async def sync_now(db: AsyncSession = Depends(get_db)) -> dict:
    if not (await spoolman.get_config(db)).active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Spoolman is not enabled")

    def _go() -> dict:
        with base.sync_session() as s:
            return spoolman.reconcile(s)

    return await anyio.to_thread.run_sync(_go)


@router.get("/status")
async def sync_status(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (
        (await db.execute(select(SpoolmanSyncState).order_by(SpoolmanSyncState.spool_id)))
        .scalars()
        .all()
    )
    return [
        {
            "spool_id": r.spool_id,
            "last_pushed_at": r.last_pushed_at,
            "last_pulled_at": r.last_pulled_at,
            "last_pulled_weight": r.last_pulled_weight,
            "last_error": r.last_error,
        }
        for r in rows
    ]
