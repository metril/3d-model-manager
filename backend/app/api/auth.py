"""Login/logout/me/password (SPEC requirement 1, "API surface" auth rows).

Split into two routers: ``public_router`` (just ``/auth/login``, which must
stay reachable without a session) and ``protected_router`` (``/auth/me``,
``/auth/password``, ``/auth/logout``), mirroring the split enforced at
``app.api`` level.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SESSION_COOKIE_NAME, SESSION_MAX_AGE, AuthContext, require_session
from app.config import get_settings
from app.db import get_db
from app.models import Session as SessionModel
from app.models import User
from app.security import DUMMY_HASH, hash_password, verify_password

public_router = APIRouter(prefix="/auth", tags=["auth"])
protected_router = APIRouter(prefix="/auth", tags=["auth"])

# ``me``/``logout`` below also declare `Depends(require_session)` themselves
# even though the *enforcement* already happens once via the router-level
# dependency composed in ``app.api`` -- that's what's needed to get the
# resolved ``AuthContext`` value into the handler. FastAPI caches a
# dependency's result per request by callable identity, so this doesn't run
# ``require_session`` (and its DB query) twice.


class LoginRequest(BaseModel):
    username: str
    password: str


def _invalid_credentials() -> HTTPException:
    """One message for both "no such user" and "wrong password" so a login
    attempt can never be used to enumerate valid usernames.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid username or password",
    )


@public_router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Verify credentials, open a session, and set the session cookie."""
    user = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()

    if user is None or user.password_hash is None or not user.is_active:
        # Still pay the cost of a real argon2 verification so these branches
        # take roughly as long as the wrong-password branch below (see
        # app.security.DUMMY_HASH).
        verify_password(payload.password, DUMMY_HASH)
        raise _invalid_credentials()

    if not verify_password(payload.password, user.password_hash):
        raise _invalid_credentials()

    await start_session(db, response, user)


async def start_session(db: AsyncSession, response: Response, user: User) -> SessionModel:
    """Create a session row, stamp ``last_login_at``, set the cookie on the
    given ``response`` (any Response, e.g. a RedirectResponse) and commit.
    """
    session = SessionModel(user_id=user.id, expires_at=datetime.now(UTC) + SESSION_MAX_AGE)
    db.add(session)
    user.last_login_at = datetime.now(UTC)
    await db.commit()

    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=str(session.id),
        max_age=int(SESSION_MAX_AGE.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    return session


class MeGroup(BaseModel):
    id: int
    name: str


class PrinterFlags(BaseModel):
    print: bool
    control: bool
    # P7 review/billing plan Task 6: whether this user may review/approve
    # queue items targeting this printer -- the global `queue:review` perm
    # AND their own per-printer `can_print` ACL (mirrors
    # `app.services.review.can_review`'s own check for a non-admin).
    review: bool
    # I2: whether a print THIS user sends to this printer needs review
    # before it dispatches (the printer_access row's own `requires_review`
    # flag) -- mirrors `app.services.review.needs_review`'s per-printer
    # check. Always `false` for an admin (never surfaced here -- admins get
    # `printer_access=None`, see below).
    requires_review: bool


class MeOut(BaseModel):
    id: int
    username: str
    display_name: str | None
    email: str | None
    role: str
    permissions: list[str]
    groups: list[MeGroup]
    has_password: bool
    printer_access: dict[str, PrinterFlags] | None


@protected_router.get("/me", response_model=MeOut)
async def me(ctx: AuthContext = Depends(require_session)) -> MeOut:
    """The authenticated user with resolved grants. ``printer_access`` is
    ``None`` for admins (all printers)."""
    user = ctx.user
    return MeOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role.value,
        permissions=sorted(ctx.perms),
        groups=[MeGroup(id=g.id, name=g.name) for g in ctx.groups],
        has_password=user.password_hash is not None,
        printer_access=None
        if ctx.is_admin
        else {
            str(pid): PrinterFlags(
                print=a.can_print,
                control=a.can_control,
                review=("queue:review" in ctx.perms) and a.can_print,
                requires_review=a.requires_review,
            )
            for pid, a in ctx.printer_acl.items()
        },
    )


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


@protected_router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Change the authenticated user's password.

    ``current_password`` is checked against the stored hash with the same
    ``verify_password`` login uses; a mismatch is a 403 (authenticated, but
    this particular action is denied), not a 401 -- the session itself is
    still valid.

    On success, every OTHER session for this user is deleted so a stolen or
    shared session cookie doesn't survive the password change. The CURRENT
    session (``ctx.session``, backing the cookie this request came in on) is
    deliberately kept so the caller isn't logged out by their own password
    change.
    """
    if ctx.user.password_hash is None or not verify_password(
        payload.current_password, ctx.user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password is incorrect",
        )

    ctx.user.password_hash = hash_password(payload.new_password)
    await db.execute(
        delete(SessionModel).where(
            SessionModel.user_id == ctx.user.id,
            SessionModel.id != ctx.session.id,
        )
    )
    await db.commit()


@protected_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Invalidate the current session and clear the cookie."""
    await db.delete(ctx.session)
    await db.commit()

    settings = get_settings()
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
