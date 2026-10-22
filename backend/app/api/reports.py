"""Duplicate-files report (Branch 4 Task 1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Model
from app.schemas.reports import DuplicatesReport, DuplicatesResolveIn, DuplicatesResolveOut
from app.services import reports as reports_service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/duplicates", response_model=DuplicatesReport)
async def get_duplicates_report(db: AsyncSession = Depends(get_db)) -> DuplicatesReport:
    return await reports_service.duplicate_files_report(db)


@router.post(
    "/duplicates/resolve",
    response_model=DuplicatesResolveOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def resolve_duplicates_report(
    payload: DuplicatesResolveIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> DuplicatesResolveOut:
    async def authorize(model_ids: set[int]) -> None:
        owners = (
            await db.execute(select(Model.created_by_user_id).where(Model.id.in_(model_ids)))
        ).scalars()
        for owner in owners:
            require_owner_or_admin(ctx, owner)

    return await reports_service.resolve_duplicates(
        db, settings, keep=payload.keep, authorize_models=authorize
    )
