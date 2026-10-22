"""FastAPI dependencies enforcing the single-session auth model (SPEC
requirement 1). ``require_session`` is composed at *router* level in
``app.api`` -- see that module's docstring -- rather than added to every
protected endpoint individually.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.models import ApiToken, Group, Session, User
from app.services import api_tokens
from app.services.app_config import get_app_config
from app.services.permissions import PERMS, Perm, PrinterAcl, load_grants
from app.storage.base import StorageBackend

SESSION_COOKIE_NAME = "tdmm_session"
SESSION_MAX_AGE = timedelta(days=30)

# How stale ``last_seen_at`` must be before a request bothers writing a
# fresh value -- avoids a DB write on every single authenticated request.
LAST_SEEN_THROTTLE = timedelta(seconds=60)


@dataclass(slots=True)
class AuthContext:
    """The authenticated user, their session and resolved grants."""

    user: User
    session: Session | None
    perms: frozenset[str]
    printer_acl: dict[int, PrinterAcl]
    groups: list[Group]

    @property
    def is_admin(self) -> bool:
        return "admin" in self.perms

    def can(self, perm: str) -> bool:
        return perm in self.perms

    def require_printer(self, printer_id: int, kind: Literal["print", "control"]) -> None:
        """403 unless the global ``printers:<kind>`` perm AND the per-printer
        ACL flag both hold (admins always pass)."""
        if self.is_admin:
            return
        acl = self.printer_acl.get(printer_id)
        flag = acl is not None and (acl.can_print if kind == "print" else acl.can_control)
        if f"printers:{kind}" not in self.perms or not flag:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "printer access denied")


def _unauthenticated() -> HTTPException:
    """401 for any missing/invalid/expired session.

    Deviation from the plan: the plan text says 403 here. 401 is the
    semantically correct code for "not authenticated at all" (403 is for
    "authenticated but not allowed"), and it's what the frontend task's
    unauthenticated -> /login redirect is built around. See task-3 report.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Cookie"},
    )


async def require_session(
    tdmm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Resolve the ``tdmm_session`` cookie to a live session + user, or 401.

    Also throttles ``last_seen_at`` bumps to at most once per
    ``LAST_SEEN_THROTTLE`` so authenticated traffic doesn't cost a write per
    request.
    """
    if tdmm_session is None:
        raise _unauthenticated()
    try:
        token = uuid.UUID(tdmm_session)
    except ValueError:
        raise _unauthenticated() from None

    row = (
        await db.execute(
            select(Session, User).join(User, User.id == Session.user_id).where(Session.id == token)
        )
    ).one_or_none()
    if row is None:
        raise _unauthenticated()
    session, user = row
    if not user.is_active:
        raise _unauthenticated()

    now = datetime.now(UTC)
    if session.expires_at <= now:
        raise _unauthenticated()

    if now - session.last_seen_at > LAST_SEEN_THROTTLE:
        session.last_seen_at = now
        await db.commit()

    groups, perms, acl = await load_grants(db, user)
    return AuthContext(user=user, session=session, perms=perms, printer_acl=acl, groups=groups)


def _check_perms(ctx: AuthContext, perms: tuple[str, ...]) -> None:
    for p in perms:
        if not ctx.can(p):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {p}")


def _validate_names(perms: tuple[str, ...]) -> None:
    for p in perms:
        if p not in PERMS:
            raise ValueError(f"unknown permission: {p}")


def require_perm(*perms: Perm) -> Callable[..., Awaitable[AuthContext]]:
    """Dependency factory: every listed permission is required, else 403."""
    _validate_names(perms)

    async def dep(ctx: AuthContext = Depends(require_session)) -> AuthContext:
        _check_perms(ctx, perms)
        return ctx

    return dep


def require_perm_on_write(*perms: Perm) -> Callable[..., Awaitable[AuthContext]]:
    """Like ``require_perm`` but skipped for GET/HEAD/OPTIONS."""
    _validate_names(perms)

    async def dep(request: Request, ctx: AuthContext = Depends(require_session)) -> AuthContext:
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            _check_perms(ctx, perms)
        return ctx

    return dep


def require_printer_access(
    kind: Literal["print", "control"],
) -> Callable[..., Awaitable[AuthContext]]:
    """Dependency factory reading the ``printer_id`` path param."""

    async def dep(printer_id: int, ctx: AuthContext = Depends(require_session)) -> AuthContext:
        ctx.require_printer(printer_id, kind)
        return ctx

    return dep


def visible_printer_ids(ctx: AuthContext) -> set[int] | None:
    """``None`` = all printers (admin); else ids with any ACL row."""
    if ctx.is_admin:
        return None
    return set(ctx.printer_acl)


def _unauthenticated_bearer() -> HTTPException:
    """401 for a missing/malformed/unknown/revoked ``Authorization: Bearer``
    token -- the ``/ext`` router's counterpart to ``_unauthenticated()``
    above. Never include the token in ``detail``; never log it.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_api_token(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> ApiToken:
    """Resolve an ``Authorization: Bearer <token>`` header to a live
    ``ApiToken`` row, or 401. This is the separate, narrowly-scoped auth
    plane the ``/ext`` router (browser extension) uses instead of
    ``require_session`` -- a browser extension can't present the httponly
    session cookie cross-site, and a leaked extension token must not carry
    the full session-cookie API's privileges.
    """
    if authorization is None:
        raise _unauthenticated_bearer()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthenticated_bearer()

    row = await api_tokens.verify(db, token)
    if row is None:
        raise _unauthenticated_bearer()
    return row


async def require_printer_enabled(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> None:
    """503 when the printer flag is off (Global Constraints). Routes stay
    mounted so the frontend can tell 'disabled' from a genuine 404; the app
    is fully functional with the flag off.

    Round 10 T3: reads the DB-backed ``AppConfig`` live, on every request,
    instead of the env-only ``Settings.printer_enabled`` snapshot -- flipping
    the flag via ``PUT /settings/app`` takes effect on the very next request,
    no restart. Existing router-level ``Depends(require_printer_enabled)``
    call sites are unchanged; FastAPI awaits async dependencies the same way.
    """
    config = await get_app_config(db, settings)
    if not config.printer_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "printer integration is disabled")


async def get_storage_backend(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StorageBackend:
    """Shared FastAPI dependency wrapping ``storage_config.resolve_backend``
    (Task 7 backlog fold): three routers (``models``/``files``/``revisions``)
    each carried an identical private ``_backend()`` copy of this one-liner;
    consolidated here so a future storage-backend change has one call site to
    update instead of three.

    DB-aware since M3 Task 1 (Global Constraints "Backend selection is
    DB-driven"): resolves the active backend + its config from the
    ``settings`` table, defaulting to local when unset. FastAPI awaits async
    dependencies, so every existing ``Depends(get_storage_backend)`` call
    site is unchanged.
    """
    from app.services.storage_config import resolve_backend

    return await resolve_backend(db, settings)
