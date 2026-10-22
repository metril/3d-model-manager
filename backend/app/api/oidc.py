"""Public OIDC login endpoints (pre-session: browser redirects to/from the IdP)."""

from fastapi import APIRouter, Depends, Request
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

public_router = APIRouter(prefix="/auth/oidc", tags=["auth"])


class ProviderPublic(BaseModel):
    slug: str
    name: str


def _fail(code: str) -> RedirectResponse:
    return RedirectResponse(f"/login?error={code}", status_code=302)


def _redirect_uri(request: Request, slug: str) -> str:
    settings = get_settings()
    origin = (settings.public_url or str(request.base_url)).rstrip("/")
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
            redis, provider, redirect_uri=_redirect_uri(request, slug), return_to=return_to
        )
    except svc.OidcError as e:
        return _fail(e.code)
    finally:
        await redis.aclose()
    return RedirectResponse(url, status_code=302)


@public_router.get("/{slug}/callback")
async def callback(
    slug: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    provider = await _provider(db, slug)
    if provider is None or error or not code or not state:
        return _fail("oidc_failed")
    redis = Redis.from_url(get_settings().redis_url)
    try:
        user, return_to = await svc.complete_login(
            db,
            redis,
            provider,
            code=code,
            state=state,
            redirect_uri=_redirect_uri(request, slug),
        )
    except svc.OidcError as e:
        return _fail(e.code)
    finally:
        await redis.aclose()
    response = RedirectResponse(return_to, status_code=302)
    await start_session(db, response, user)
    return response
