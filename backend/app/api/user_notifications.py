"""Per-user email notification preferences (T8a). Session-only: every user
manages their own row; emails go to the starter of a print job."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_session
from app.db import get_db
from app.models.notify import UserEmailPreference
from app.services import mailer

router = APIRouter(prefix="/user-notifications", tags=["notifications"])

PREF_FIELDS = (
    "notify_print_start",
    "notify_print_complete",
    "notify_print_failed",
    "notify_print_stopped",
)


class EmailPrefs(BaseModel):
    notify_print_start: bool = False
    notify_print_complete: bool = False
    notify_print_failed: bool = False
    notify_print_stopped: bool = False


class EmailPrefsOut(EmailPrefs):
    smtp_configured: bool
    has_email: bool


async def _out(
    db: AsyncSession, ctx: AuthContext, row: UserEmailPreference | None
) -> EmailPrefsOut:
    prefs = {f: bool(getattr(row, f)) if row else False for f in PREF_FIELDS}
    return EmailPrefsOut(
        **prefs,
        smtp_configured=await mailer.is_configured(db),
        has_email=bool(ctx.user.email),
    )


async def _row(db: AsyncSession, user_id: int) -> UserEmailPreference | None:
    return await db.scalar(
        select(UserEmailPreference).where(UserEmailPreference.user_id == user_id)
    )


@router.get("/preferences", response_model=EmailPrefsOut)
async def get_preferences(
    ctx: AuthContext = Depends(require_session), db: AsyncSession = Depends(get_db)
) -> EmailPrefsOut:
    return await _out(db, ctx, await _row(db, ctx.user.id))


@router.put("/preferences", response_model=EmailPrefsOut)
async def put_preferences(
    body: EmailPrefs,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> EmailPrefsOut:
    values = {f: getattr(body, f) for f in PREF_FIELDS}
    await db.execute(
        insert(UserEmailPreference)
        .values(user_id=ctx.user.id, created_by_user_id=ctx.user.id, **values)
        .on_conflict_do_update(index_elements=["user_id"], set_=values)
    )
    await db.commit()
    row = await _row(db, ctx.user.id)
    return await _out(db, ctx, row)
