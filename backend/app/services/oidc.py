"""OIDC login: authlib PKCE/code exchange, joserfc ID-token validation,
Redis one-shot state, account resolution and IdP group sync."""

import hashlib
import json
import re
import secrets
import time
import warnings
from collections.abc import Mapping
from typing import Any, Literal

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import Group, GroupMember, OidcProvider, User
from app.models.enums import MembershipSource, UserRole
from app.services.permissions import load_grants

with warnings.catch_warnings():
    # authlib falls back to plain httpx and warns at import (httpx2 isn't
    # packaged); its own deprecate module also forces "always", so a pytest
    # filterwarnings entry can't catch it. Scoped to this one import.
    import authlib.deprecate  # noqa: F401  (installs its "always" filter first)

    warnings.simplefilter("ignore", DeprecationWarning)
    from authlib.integrations.httpx_client import AsyncOAuth2Client

STATE_TTL_S = 600
STATE_KEY = "oidc:state:{state}"
_HTTP_TIMEOUT = 10.0


class OidcError(Exception):
    def __init__(
        self, code: Literal["not_provisioned", "inactive", "invalid_state", "oidc_failed"]
    ) -> None:
        super().__init__(code)
        self.code = code


def state_binding(state: str) -> str:
    """Value of the browser-bound ``tdmm_oidc`` cookie for a given state."""
    return hashlib.sha256(state.encode()).hexdigest()


_ASYMMETRIC_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512")


def _allowed_algs(meta: dict[str, Any]) -> list[str]:
    advertised = meta.get("id_token_signing_alg_values_supported")
    algs = [a for a in advertised if a in _ASYMMETRIC_ALGS] if isinstance(advertised, list) else []
    return algs or ["RS256"]


def safe_return_to(value: str | None) -> str:
    """Only same-origin absolute paths; anything else falls back to ``/``."""
    if value and value.startswith("/") and not value.startswith(("//", "/\\")):
        return value
    return "/"


def extract_claim(claims: Mapping[str, Any], path: str) -> list[str]:
    cur: Any = claims
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return []
        cur = cur[part]
    if isinstance(cur, str):
        return [cur]
    if isinstance(cur, list):
        return [str(v) for v in cur]
    return []


async def _discover(provider: OidcProvider) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            r = await http.get(provider.discovery_url)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        raise OidcError("oidc_failed") from exc


def _oauth_client(provider: OidcProvider, redirect_uri: str) -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=provider.client_id,
        client_secret=decrypt_secret(get_settings(), provider.client_secret_enc),
        scope=provider.scopes,
        redirect_uri=redirect_uri,
        code_challenge_method="S256",
        timeout=_HTTP_TIMEOUT,
    )


async def start_login(
    redis: Redis, provider: OidcProvider, *, redirect_uri: str, return_to: str | None
) -> str:
    meta = await _discover(provider)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    async with _oauth_client(provider, redirect_uri) as oauth:
        url, _ = oauth.create_authorization_url(
            meta["authorization_endpoint"], state=state, nonce=nonce, code_verifier=verifier
        )
    await redis.set(
        STATE_KEY.format(state=state),
        json.dumps(
            {
                "slug": provider.slug,
                "nonce": nonce,
                "code_verifier": verifier,
                "return_to": safe_return_to(return_to),
            }
        ),
        ex=STATE_TTL_S,
    )
    return url


async def _fetch_claims(
    provider: OidcProvider,
    meta: dict[str, Any],
    saved: dict[str, Any],
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    try:
        async with _oauth_client(provider, redirect_uri) as oauth:
            token = await oauth.fetch_token(
                meta["token_endpoint"], code=code, code_verifier=saved["code_verifier"]
            )
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
                jwks = (await http.get(meta["jwks_uri"])).json()
            decoded = jwt.decode(
                token["id_token"], KeySet.import_key_set(jwks), algorithms=_allowed_algs(meta)
            )
            claims: dict[str, Any] = dict(decoded.claims)
            aud = claims.get("aud")
            auds = [aud] if isinstance(aud, str) else list(aud or [])
            if (
                claims.get("iss") != meta["issuer"]
                or provider.client_id not in auds
                or claims.get("nonce") != saved["nonce"]
                or float(claims["exp"]) <= time.time()
            ):
                raise OidcError("oidc_failed")
            info: dict[str, Any] = {}
            if meta.get("userinfo_endpoint"):
                try:
                    resp = await oauth.get(meta["userinfo_endpoint"])
                    if resp.status_code == 200:
                        body = resp.json()
                        # OIDC Core 5.3.2: userinfo is only trusted for the same subject.
                        if isinstance(body, dict) and body.get("sub") == claims.get("sub"):
                            info = body
                except Exception:  # userinfo is best-effort enrichment
                    info = {}
            return {**info, **claims}
    except OidcError:
        raise
    except Exception as exc:
        raise OidcError("oidc_failed") from exc


async def complete_login(
    db: AsyncSession,
    redis: Redis,
    provider: OidcProvider,
    *,
    code: str,
    state: str,
    redirect_uri: str,
) -> tuple[User, str]:
    raw = await redis.getdel(STATE_KEY.format(state=state))
    if raw is None:
        raise OidcError("invalid_state")
    saved = json.loads(raw)
    if saved.get("slug") != provider.slug:
        raise OidcError("invalid_state")
    meta = await _discover(provider)
    claims = await _fetch_claims(provider, meta, saved, code, redirect_uri)
    user = await resolve_user(db, provider, claims)
    await sync_groups(db, provider, user, extract_claim(claims, provider.groups_claim))
    await db.commit()
    return user, safe_return_to(saved.get("return_to"))


async def _unique_username(db: AsyncSession, base: str) -> str:
    candidate, n = base, 1
    while await db.scalar(select(User.id).where(User.username == candidate)) is not None:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


async def resolve_user(db: AsyncSession, provider: OidcProvider, claims: Mapping[str, Any]) -> User:
    iss, sub = str(claims["iss"]), str(claims["sub"])
    email = claims.get("email")
    verified = claims.get("email_verified") is True and isinstance(email, str) and bool(email)

    user = await db.scalar(select(User).where(User.oidc_issuer == iss, User.oidc_subject == sub))
    if user is None and provider.link_by_email and verified:
        user = await db.scalar(select(User).where(User.email == email, User.oidc_subject.is_(None)))
        if user is not None:
            user.oidc_issuer, user.oidc_subject = iss, sub
    if user is None and provider.auto_create_users:
        base = (
            claims.get("preferred_username")
            or (email.split("@")[0] if isinstance(email, str) and email else None)
            or sub
        )
        base = re.sub(r"\s+", "-", str(base).strip()) or sub
        store_email = (
            email
            if verified and await db.scalar(select(User.id).where(User.email == email)) is None
            else None
        )
        user = User(
            username=await _unique_username(db, base),
            display_name=claims.get("name"),
            email=store_email,
            password_hash=None,
            role=UserRole.MEMBER,
            is_active=True,
            oidc_issuer=iss,
            oidc_subject=sub,
        )
        db.add(user)
    if user is None:
        raise OidcError("not_provisioned")
    if not user.is_active:
        raise OidcError("inactive")
    await db.flush()
    return user


async def _other_active_admins(db: AsyncSession, user: User) -> int:
    users = (
        await db.scalars(select(User).where(User.is_active.is_(True), User.id != user.id))
    ).all()
    n = 0
    for u in users:
        _, perms, _ = await load_grants(db, u)
        n += "admin" in perms
    return n


async def sync_groups(
    db: AsyncSession, provider: OidcProvider, user: User, claim_values: list[str]
) -> None:
    mapping = provider.group_mapping or {}
    wanted = {mapping[v] for v in claim_values if v in mapping}
    if wanted:
        wanted = set(await db.scalars(select(Group.id).where(Group.id.in_(wanted))))
    old_oidc = set(
        await db.scalars(
            select(GroupMember.group_id).where(
                GroupMember.user_id == user.id, GroupMember.source == MembershipSource.OIDC
            )
        )
    )
    _, before, _ = await load_grants(db, user)
    await db.execute(
        delete(GroupMember).where(
            GroupMember.user_id == user.id, GroupMember.source == MembershipSource.OIDC
        )
    )
    local = set(
        await db.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user.id))
    )
    for gid in wanted - local:
        db.add(GroupMember(group_id=gid, user_id=user.id, source=MembershipSource.OIDC))
    if provider.admin_group:
        if provider.admin_group in claim_values:
            user.role = UserRole.ADMIN
        elif user.role == UserRole.ADMIN and await _other_active_admins(db, user) > 0:
            user.role = UserRole.MEMBER
    await db.flush()
    _, after, _ = await load_grants(db, user)
    if "admin" in before and "admin" not in after and await _other_active_admins(db, user) == 0:
        # Last-admin guard also covers admin granted via an OIDC-mapped group.
        await db.execute(
            delete(GroupMember).where(
                GroupMember.user_id == user.id, GroupMember.source == MembershipSource.OIDC
            )
        )
        await db.flush()
        for gid in old_oidc - local:
            db.add(GroupMember(group_id=gid, user_id=user.id, source=MembershipSource.OIDC))
        await db.flush()
