"""Fake OIDC IdP (respx) + OidcProvider factory for the login-flow tests.

respx only intercepts httpx's real network transports; the ASGI test client
talks to the app in-process, so only the app's outbound IdP calls are mocked.
"""

import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from joserfc import jwt
from joserfc.jwk import RSAKey
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import OidcProvider

ISSUER = "https://idp.test/application/o/tdmm/"
CLIENT_ID = "tdmm-client"
MakeProvider = Callable[..., Awaitable[OidcProvider]]


class FakeIdp:
    issuer = ISSUER

    def __init__(self, router: respx.MockRouter) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig"})
        self.claims: dict[str, Any] = {}
        self.nonce_override: str | None = None
        self.nonce: str | None = None
        self.state: str | None = None
        base = ISSUER.rstrip("/")
        router.get(f"{base}/.well-known/openid-configuration").respond(
            json={
                "issuer": ISSUER,
                "authorization_endpoint": "https://idp.test/authorize",
                "token_endpoint": "https://idp.test/token",
                "jwks_uri": "https://idp.test/jwks",
                "userinfo_endpoint": "https://idp.test/userinfo",
            }
        )
        router.get("https://idp.test/jwks").respond(
            json={"keys": [self.key.as_dict(private=False)]}
        )
        router.post("https://idp.test/token").mock(side_effect=self._token)
        router.get("https://idp.test/userinfo").mock(side_effect=self._userinfo)

    def _token(self, request: httpx.Request) -> httpx.Response:
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "sub-1",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": self.nonce_override or self.nonce,
            **self.claims,
        }
        id_token = jwt.encode({"alg": "RS256", "kid": "k1"}, claims, self.key)
        return httpx.Response(
            200, json={"access_token": "at", "token_type": "Bearer", "id_token": id_token}
        )

    def _userinfo(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sub": "sub-1", **self.claims})

    async def start(
        self, client: httpx.AsyncClient, slug: str, return_to: str | None = None
    ) -> httpx.Response:
        params = {"return_to": return_to} if return_to else None
        r = await client.get(f"/api/auth/oidc/{slug}/start", params=params)
        if r.status_code == 302:
            q = parse_qs(urlparse(r.headers["location"]).query)
            self.state = q["state"][0]
            self.nonce = q["nonce"][0]
        return r

    async def callback(
        self, client: httpx.AsyncClient, slug: str, state: str | None = None
    ) -> httpx.Response:
        return await client.get(
            f"/api/auth/oidc/{slug}/callback",
            params={"code": "code-1", "state": state or self.state},
        )

    async def login(
        self,
        client: httpx.AsyncClient,
        slug: str,
        claims: dict[str, Any] | None = None,
        return_to: str | None = None,
    ) -> httpx.Response:
        self.claims = dict(claims or {})
        await self.start(client, slug, return_to)
        return await self.callback(client, slug)


@pytest.fixture
async def fake_idp() -> AsyncGenerator[FakeIdp, None]:
    with respx.mock(assert_all_called=False) as router:
        yield FakeIdp(router)


@pytest.fixture
def make_provider(db_session: AsyncSession) -> MakeProvider:
    async def _make(**overrides: Any) -> OidcProvider:
        fields: dict[str, Any] = {
            "name": "Authentik",
            "slug": "authentik",
            "discovery_url": f"{ISSUER}.well-known/openid-configuration",
            "client_id": CLIENT_ID,
            "client_secret_enc": encrypt_secret(get_settings(), "s3cret"),
            "scopes": "openid profile email groups",
            "groups_claim": "groups",
            "group_mapping": {},
            "enabled": True,
        }
        fields.update(overrides)
        p = OidcProvider(**fields)
        db_session.add(p)
        await db_session.commit()
        await db_session.refresh(p)
        return p

    return _make
