"""Sidebar external links (Q8a). Reads are open to any session; mutations
need ``settings:write``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm
from app.db import get_db
from app.models.external_link import ExternalLink
from app.schemas.external_links import ExternalLinkIn, ExternalLinkOut, ExternalLinkPatch

router = APIRouter(prefix="/external-links", tags=["external-links"])
_write = Depends(require_perm("settings:write"))


async def _get_or_404(db: AsyncSession, link_id: int) -> ExternalLink:
    row = await db.get(ExternalLink, link_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "external link not found")
    return row


@router.get("", response_model=list[ExternalLinkOut])
async def list_external_links(db: AsyncSession = Depends(get_db)) -> list[ExternalLink]:
    result = await db.execute(
        select(ExternalLink).order_by(ExternalLink.sort_order, ExternalLink.id)
    )
    return list(result.scalars())


@router.post("", response_model=ExternalLinkOut, status_code=status.HTTP_201_CREATED)
async def create_external_link(
    payload: ExternalLinkIn,
    ctx: AuthContext = _write,
    db: AsyncSession = Depends(get_db),
) -> ExternalLink:
    row = ExternalLink(**payload.model_dump(), created_by_user_id=ctx.user.id)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.patch("/{link_id}", response_model=ExternalLinkOut)
async def update_external_link(
    link_id: int,
    payload: ExternalLinkPatch,
    _ctx: AuthContext = _write,
    db: AsyncSession = Depends(get_db),
) -> ExternalLink:
    row = await _get_or_404(db, link_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None and key != "icon":
            continue
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_external_link(
    link_id: int,
    _ctx: AuthContext = _write,
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await _get_or_404(db, link_id)
    await db.delete(row)
    await db.commit()
