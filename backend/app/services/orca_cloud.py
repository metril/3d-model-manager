"""Orca Cloud account: pairing + profile pull, for cloud preset sync.

Auth is OAuth 2.0 Device Authorization Grant (RFC 8628) as a public client
(``client_id`` only): the user approves a short ``user_code`` in their Orca
Cloud settings while we poll the token endpoint. Tokens are an opaque 24h
access token plus a single-use, rotating refresh token (reuse of a consumed
refresh token beyond a ~60s grace revokes the pairing, so a rotated pair is
persisted before it is used).

    POST /oauth/device/code   -> device_code, user_code, verification_uri, expires_in, interval
    POST /oauth/token         -> device_code poll, then refresh_token grant
    GET  /api/v1/external/sync/pull  (no cursor = full snapshot) -> upserts: [{id, name, content}]

Read-only (``sync:read``): we never write to the user's cloud. Cloudflare
fronts the API and blocks unusual User-Agents, so we send an honest one.

Storage (SECURITY): the ``settings`` row ``orca_cloud`` holds the access and
refresh tokens Fernet-encrypted (``app.crypto``, same seam as ``bambu_auth``);
the pending device code is encrypted too. Nothing here is ever returned by an
API response or logged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models import Setting

SETTINGS_KEY = "orca_cloud"
SCOPE = "sync:read"
_UA = "3d-model-manager/1.0 (+https://github.com/metril/3d-model-manager)"
# Refresh when the access token has less than this left, so a slow call
# doesn't expire mid-flight.
_REFRESH_LEEWAY_S = 300
DEVICE_CODE_TTL_S = 600

PENDING = "authorization_pending"
SLOW_DOWN = "slow_down"
DENIED = "access_denied"
EXPIRED = "expired_token"
COMPLETE = "complete"


class OrcaCloudError(Exception):
    """Network / unexpected-server failure."""


class OrcaCloudAuthError(OrcaCloudError):
    """Not connected, or the pairing is dead: the user must re-pair.

    ``revoked`` is True only when the server said ``invalid_grant`` (the
    refresh token is genuinely dead); a bare 401/403 (e.g. a Cloudflare
    challenge) is not proof and must not wipe the pairing.
    """

    def __init__(self, msg: str, revoked: bool = False) -> None:
        super().__init__(msg)
        self.revoked = revoked


def _client() -> httpx.Client:
    """The ONE httpx seam tests monkeypatch."""
    return httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": _UA})


def _error_code(resp: httpx.Response) -> str | None:
    try:
        data = resp.json()
    except ValueError:
        return None
    err = data.get("error") if isinstance(data, dict) else None
    return err if isinstance(err, str) and err else None


def _describe(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return (resp.text or "<empty body>")[:200]
    if isinstance(data, dict):
        for key in ("error_description", "msg", "error", "message"):
            if isinstance(data.get(key), str) and data[key]:
                return data[key]
    return str(data)[:200]


def request_device_code(settings: Settings, instance_label: str | None = None) -> dict[str, Any]:
    form = {"client_id": settings.orca_client_id, "scope": SCOPE}
    if instance_label:
        form["instance_label"] = instance_label
    try:
        with _client() as c:
            resp = c.post(f"{settings.orca_api_base.rstrip('/')}/oauth/device/code", data=form)
    except httpx.HTTPError as exc:
        raise OrcaCloudError(f"network error requesting an Orca Cloud device code: {exc}") from exc
    if resp.status_code in (400, 401, 403):
        raise OrcaCloudAuthError(f"Orca Cloud rejected the device-code request: {_describe(resp)}")
    if resp.status_code >= 400:
        raise OrcaCloudError(f"device-code request failed ({resp.status_code}): {_describe(resp)}")
    return resp.json()


def poll_token(settings: Settings, device_code: str) -> tuple[str, dict[str, Any] | None]:
    """One poll of the token endpoint: ``(status, token_response | None)``."""
    form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": settings.orca_client_id,
    }
    try:
        with _client() as c:
            resp = c.post(f"{settings.orca_api_base.rstrip('/')}/oauth/token", data=form)
    except httpx.HTTPError as exc:
        raise OrcaCloudError(f"network error polling the Orca Cloud token endpoint: {exc}") from exc
    if resp.status_code < 400:
        return COMPLETE, resp.json()
    error = _error_code(resp)
    if error in (PENDING, SLOW_DOWN, DENIED):
        return error, None
    if error in (EXPIRED, "invalid_grant"):
        return EXPIRED, None
    raise OrcaCloudError(f"token poll failed ({resp.status_code}): {_describe(resp)}")


def refresh_tokens(settings: Settings, refresh_token: str) -> dict[str, Any]:
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.orca_client_id,
    }
    try:
        with _client() as c:
            resp = c.post(f"{settings.orca_api_base.rstrip('/')}/oauth/token", data=form)
    except httpx.HTTPError as exc:
        raise OrcaCloudError(f"network error refreshing the Orca Cloud token: {exc}") from exc
    if resp.status_code in (400, 401, 403):
        raise OrcaCloudAuthError(
            f"Orca Cloud refresh rejected: {_describe(resp)}",
            revoked=_error_code(resp) == "invalid_grant",
        )
    if resp.status_code >= 400:
        raise OrcaCloudError(f"refresh failed ({resp.status_code}): {_describe(resp)}")
    return resp.json()


def list_profiles(settings: Settings, access_token: str) -> list[dict[str, Any]]:
    """The user's Orca Cloud profiles (``{id, name, content, updated_time, ...}``)
    via a full-snapshot sync pull (no ``cursor`` -- ``cursor=0`` is a 410)."""
    try:
        with _client() as c:
            resp = c.get(
                f"{settings.orca_api_base.rstrip('/')}/api/v1/external/sync/pull",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise OrcaCloudError(f"network error listing Orca Cloud profiles: {exc}") from exc
    if resp.status_code == 401:
        raise OrcaCloudAuthError("Orca Cloud token expired or revoked")
    if resp.status_code >= 400:
        raise OrcaCloudError(f"profile list failed ({resp.status_code}): {_describe(resp)}")
    data = resp.json()
    if isinstance(data, dict):
        for key in ("upserts", "profiles", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return data if isinstance(data, list) else []


# --- stored credentials ----------------------------------------------------


@dataclass
class OrcaAuthState:
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: float | None = None
    connected_at: float | None = None
    pending_device_code: str | None = None
    pending_interval: int = 5
    pending_expires_at: float | None = None

    @property
    def connected(self) -> bool:
        return bool(self.refresh_token or self.access_token)

    def access_valid(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return bool(
            self.access_token and self.expires_at and self.expires_at - _REFRESH_LEEWAY_S > now
        )


def _dec(settings: Settings, raw: str | None) -> str | None:
    return decrypt_secret(settings, raw) if raw else None


def _state(settings: Settings, value: dict | None) -> OrcaAuthState:
    v = value or {}
    return OrcaAuthState(
        access_token=_dec(settings, v.get("access_token")),
        refresh_token=_dec(settings, v.get("refresh_token")),
        expires_at=v.get("expires_at"),
        connected_at=v.get("connected_at"),
        pending_device_code=_dec(settings, v.get("pending_device_code")),
        pending_interval=int(v.get("pending_interval") or 5),
        pending_expires_at=v.get("pending_expires_at"),
    )


def _value(settings: Settings, st: OrcaAuthState) -> dict:
    out: dict = {}
    if st.access_token:
        out["access_token"] = encrypt_secret(settings, st.access_token)
    if st.refresh_token:
        out["refresh_token"] = encrypt_secret(settings, st.refresh_token)
    if st.expires_at is not None:
        out["expires_at"] = st.expires_at
    if st.connected_at is not None:
        out["connected_at"] = st.connected_at
    if st.pending_device_code:
        out["pending_device_code"] = encrypt_secret(settings, st.pending_device_code)
        out["pending_interval"] = st.pending_interval
        out["pending_expires_at"] = st.pending_expires_at
    return out


async def get_state(db: AsyncSession, settings: Settings) -> OrcaAuthState:
    row = await db.get(Setting, SETTINGS_KEY)
    return _state(settings, row.value if row else None)


async def save_state(db: AsyncSession, settings: Settings, st: OrcaAuthState) -> None:
    value = _value(settings, st)
    row = await db.get(Setting, SETTINGS_KEY)
    if not value:
        if row is not None:
            await db.delete(row)
    elif row is None:
        db.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    await db.commit()


def apply_token_response(st: OrcaAuthState, data: dict[str, Any]) -> None:
    access = data.get("access_token")
    if not access:
        raise OrcaCloudAuthError("Orca Cloud token response is missing access_token")
    st.access_token = access
    # The endpoint always rotates the refresh token; keep the old one only if
    # a response ever omits it, rather than stranding the pairing.
    if data.get("refresh_token"):
        st.refresh_token = data["refresh_token"]
    expires_in = data.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        st.expires_at = time.time() + int(expires_in)
    else:
        # Missing expires_in: prefer a JWT ``exp``, else the documented 24h TTL
        # (never None -- that would force a refresh, burning the single-use
        # refresh token, on every call).
        st.expires_at = _jwt_exp(access) or time.time() + DEFAULT_TTL_S


DEFAULT_TTL_S = 24 * 3600


def _jwt_exp(token: str) -> float | None:
    import base64
    import json

    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        exp = payload.get("exp")
        return float(exp) if isinstance(exp, (int, float)) and exp > 0 else None
    except (ValueError, TypeError, AttributeError):
        return None


_REFRESH_LOCK_KEY = 0x4F52434143  # "ORCAC": pg advisory lock id for token refresh


async def ensure_access_token(db: AsyncSession, settings: Settings) -> str:
    """A valid access token, refreshing (and persisting the rotated pair
    BEFORE returning) when it is near expiry.

    Refresh is serialized across processes with a transaction-scoped advisory
    lock and the stored state is re-read after acquiring it, so concurrent
    callers never spend the single-use refresh token twice. The pairing is
    cleared only when the server answers ``invalid_grant``.
    """
    import anyio
    from sqlalchemy import select, text

    st = await get_state(db, settings)
    if not st.connected:
        raise OrcaCloudAuthError("Orca Cloud is not connected")
    if st.access_valid():
        return st.access_token  # type: ignore[return-value]
    if not st.refresh_token:
        raise OrcaCloudAuthError("Orca Cloud session expired; reconnect")
    try:
        await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _REFRESH_LOCK_KEY})
        row = (
            await db.execute(
                select(Setting)
                .where(Setting.key == SETTINGS_KEY)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        st = _state(settings, row.value if row else None)
        if not st.connected:
            raise OrcaCloudAuthError("Orca Cloud is not connected")
        if st.access_valid():  # another caller refreshed while we waited
            await db.rollback()
            return st.access_token  # type: ignore[return-value]
        if not st.refresh_token:
            raise OrcaCloudAuthError("Orca Cloud session expired; reconnect")
        try:
            data = await anyio.to_thread.run_sync(refresh_tokens, settings, st.refresh_token)
        except OrcaCloudAuthError as exc:
            if exc.revoked:
                await save_state(db, settings, OrcaAuthState())
            else:
                await db.rollback()
            raise
        apply_token_response(st, data)
        await save_state(db, settings, st)  # commit releases the advisory lock
    except BaseException:
        await db.rollback()
        raise
    return st.access_token  # type: ignore[return-value]
