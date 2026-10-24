"""Public OIDC login endpoints (pre-session: browser redirects to/from the IdP)."""

import secrets
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import start_session
from app.config import get_settings
from app.db import get_db
from app.models import OidcProvider
from app.services import oidc as svc
from app.services import two_factor
from app.services.server_config import cookie_secure, get_server_config, public_origin

BIND_COOKIE = "tdmm_oidc"

public_router = APIRouter(prefix="/auth/oidc", tags=["auth"])


class ProviderPublic(BaseModel):
    slug: str
    name: str


def _fail(code: str) -> RedirectResponse:
    return RedirectResponse(f"/login?error={code}", status_code=302)


async def _redirect_uri(db: AsyncSession, request: Request, slug: str) -> str:
    origin = public_origin(await get_server_config(db), request)
    return f"{origin}/api/auth/oidc/{slug}/callback"


async def _provider(db: AsyncSession, slug: str) -> OidcProvider | None:
    return await db.scalar(
        select(OidcProvider).where(OidcProvider.slug == slug, OidcProvider.enabled.is_(True))
    )


@public_router.get("/providers", response_model=list[ProviderPublic])
async def list_providers(db: AsyncSession = Depends(get_db)) -> list[ProviderPublic]:
    rows = await db.scalars(
        select(OidcProvider).where(OidcProvider.enabled.is_(True)).order_by(OidcProvider.name)
    )
    return [ProviderPublic(slug=p.slug, name=p.name) for p in rows]


@public_router.get("/{slug}/start")
async def start(
    slug: str,
    request: Request,
    return_to: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    provider = await _provider(db, slug)
    if provider is None:
        return _fail("oidc_failed")
    redis = Redis.from_url(get_settings().redis_url)
    try:
        url = await svc.start_login(
            redis,
            provider,
            redirect_uri=await _redirect_uri(db, request, slug),
            return_to=return_to,
        )
    except svc.OidcError as e:
        return _fail(e.code)
    finally:
        await redis.aclose()
    response = RedirectResponse(url, status_code=302)
    # Bind the flow to this browser (login-CSRF defence): cookie holds sha256(state).
    response.set_cookie(
        key=BIND_COOKIE,
        value=svc.state_binding(parse_qs(urlparse(url).query)["state"][0]),
        max_age=svc.STATE_TTL_S,
        path="/api/auth/oidc",
        httponly=True,
        samesite="lax",
        secure=cookie_secure(await get_server_config(db), request),
    )
    return response


@public_router.get("/{slug}/callback")
async def callback(
    slug: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    tdmm_oidc: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    provider = await _provider(db, slug)
    if provider is None or error or not code or not state:
        return _fail("oidc_failed")
    if tdmm_oidc is None or not secrets.compare_digest(tdmm_oidc, svc.state_binding(state)):
        failure = _fail("invalid_state")
        failure.delete_cookie(BIND_COOKIE, path="/api/auth/oidc")
        return failure
    redis = Redis.from_url(get_settings().redis_url)
    try:
        user, return_to = await svc.complete_login(
            db,
            redis,
            provider,
            code=code,
            state=state,
            redirect_uri=await _redirect_uri(db, request, slug),
        )
    except svc.OidcError as e:
        failure = _fail(e.code)
        failure.delete_cookie(BIND_COOKIE, path="/api/auth/oidc")
        return failure
    finally:
        await redis.aclose()
    if not provider.trusts_idp_mfa:
        st = await two_factor.status_for(db, user)
        if st.enabled:
            token = await two_factor.issue_token(
                db,
                two_factor.OIDC_2FA,
                user.id,
                two_factor.PRE_AUTH_TTL,
                {"return_to": return_to},
            )
            await db.commit()
            step = RedirectResponse(
                f"/login?step=2fa&token={token}&methods={','.join(st.methods)}", status_code=302
            )
            step.delete_cookie(BIND_COOKIE, path="/api/auth/oidc")
            return step
    response = RedirectResponse(return_to, status_code=302)
    response.delete_cookie(BIND_COOKIE, path="/api/auth/oidc")
    await start_session(db, response, user, request)
    return response
