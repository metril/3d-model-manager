"""Bambu Lab account auth (Workstream B task B2; SPEC/full-design-doc line
230 "Connect Bambu account" flow). MakerWorld's model-FILE downloads are
gated behind a Bambu Lab cloud account -- this module owns login/MFA/refresh
against ``api.bambulab.com`` (global) / ``api.bambulab.cn`` (China), and the
at-rest storage of the resulting credential, mirroring
``app.services.import_tokens``'s posture exactly:

**Storage (SECURITY):** ``account`` + ``region`` + the ``refresh_token`` + (as
of the access-token fix below) the login/refresh-issued ``access_token`` and
its ``access_expires_at`` (a non-secret float epoch ESTIMATE; see
``_estimate_expiry``) are persisted in the ``settings`` table under key
``"bambu_auth"``. BOTH the refresh token and the access token are
Fernet-encrypted (``app.crypto``, the same seam as the M4 printer access code
/ M6 storage secrets / the Thingiverse import token), each with its own
``InvalidToken`` legacy-plaintext fallback on read (mirrors
``import_tokens.py`` even though this key is new -- cheap insurance against a
key file ever being swapped out from under an existing row). The PASSWORD is
used only transiently inside ``login()`` to make the one login HTTP call and
is never stored, logged, or returned. Bambu's login access token is actually
a LONG-LIVED JWT (not the short-lived token this module originally assumed),
so ``get_access_token_sync`` now PREFERS the stored, still-valid access token
over any network call, only reaching ``refresh()`` once that stored token is
within the early-refresh safety margin of its estimated expiry -- and, if
that refresh call itself fails, falling back to the stored access token
again if it's still (barely) unexpired. An in-memory, per-process cache
(``_ACCESS_TOKEN_CACHE``) keyed by the refresh token currently on file still
exists, now purely to avoid re-decrypting/re-estimating on every call within
one process rather than to avoid a network round trip (see the "Contract
confidence" refresh note below for why refresh is now best-effort). Neither
the access nor refresh token is ever logged.

**Contract confidence (BE HONEST about this at acceptance):**
- Login (``POST /user-service/user/login``) and its bad-credentials shape
  (HTTP 400 ``{"code":1,"error":"Incorrect account or password."}``) were
  LIVE-VERIFIED (grounding probe). The SUCCESS shape (direct
  ``accessToken``/``refreshToken``) and the MFA-challenge shape (some
  ``loginType``/continuation-token payload signaling an emailed verification
  code is needed, per SPEC full-design line 230's ``loginType:"verifyCode"``)
  are DOCUMENTED, NOT LIVE-CAPTURED -- no real Bambu account was available to
  grounding. ``_parse_login_response`` therefore parses tolerantly (several
  candidate field names) rather than asserting one exact shape, and treats
  "no recognizable token pair in an HTTP-200 body" as "MFA required",
  carrying the ENTIRE response body through as the opaque continuation
  context for ``verify_code`` rather than guessing which subset of fields
  matters. **This must be verified against one real login before being
  trusted in production** -- if the real shape differs, only
  ``_parse_login_response`` should need adjusting.
- Refresh (``POST /user-service/user/refreshtoken``) was LIVE-VERIFIED to
  EXIST and to 401 a fake token (grounding probe); its SUCCESS response shape
  is likewise documented-not-captured, so ``refresh()`` parses tolerantly
  too. The full design doc separately notes Bambu's refresh endpoint has
  historically been unreliable ("refresh endpoint is broken -> re-login UX
  with expiry banner") -- and that was subsequently LIVE-VERIFIED the hard
  way (2026-07-12): a real user's login (``POST /user-service/user/login``)
  succeeded four times in one evening, and every single time the very next
  worker call to ``refreshtoken`` -- against that SAME fresh, never-yet-used
  refresh token, seconds later -- 401'd, re-stamping ``refresh_failed_at``
  and producing a "sign in again" loop. Refresh is therefore now treated as
  BEST-EFFORT, not depended on: ``get_access_token_sync`` reaches ``refresh()``
  only once the stored access token nears its estimated expiry (see Storage
  above), and a refresh failure surfaces as a ``kind="expired"``
  ``BambuAuthError`` (prompting reconnection) ONLY when no usable access
  token remains at all -- a merely-failed refresh with a still-good access
  token on file is not treated as a reconnect-worthy failure. When a
  reconnect-worthy failure does happen, it persists a non-secret
  ``refresh_failed_at`` marker on the stored row (``mark_refresh_failed``/
  ``_sync``, cleared by ``clear_refresh_failed``/``_sync`` or any fresh
  ``set_bambu_auth``/``_sync`` write) so ``GET /settings/bambu`` can report
  ``needs_reconnect`` -- gated, in turn, by ``BambuAuthState.has_valid_
  access_token`` so a stale marker next to a still-valid access token doesn't
  falsely tell the operator to reconnect.
"""

from __future__ import annotations

import base64
import contextlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from cryptography.fernet import InvalidToken
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from app.config import Settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models import Setting

SETTINGS_KEY = "bambu_auth"

_BASE_URLS = {
    "global": "https://api.bambulab.com/v1",
    "china": "https://api.bambulab.cn/v1",
}
_UA = "3d-model-manager/1.0 (+https://github.com/metril/3d-model-manager)"

# Safety margin subtracted from an estimated access-token expiry so
# `get_access_token_sync` refreshes a little BEFORE Bambu would actually
# reject the token (clock skew / in-flight request latency), never after.
_EXPIRY_SAFETY_MARGIN_S = 60
# Used only when neither the login/refresh response body nor the access
# token's own (unverified) JWT `exp` claim yields a usable expiry -- a
# conservative floor that forces a fresh refresh soon rather than caching an
# access token for an unknown, possibly-already-elapsed lifetime.
_FALLBACK_ACCESS_TOKEN_TTL_S = 300

# In-memory ONLY (never persisted -- see module docstring), keyed by the
# refresh token currently on file so a credential change (new login, or
# Bambu rotating the refresh token on use) can never serve a stale cached
# access token for a DIFFERENT refresh token.
_ACCESS_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class BambuAuthError(Exception):
    """A Bambu auth call failed in an expected way (bad credentials, bad/
    expired verification code, invalid/expired refresh token, or no account
    connected at all) -- always a clean, user-facing message, never a raw
    httpx/JSON exception leaking upward.

    ``kind`` lets a caller distinguish two flavors WITHOUT string-matching
    the message (Task: expiry-banner UX):
    - ``"not_configured"`` -- no Bambu account is stored at all.
    - ``"expired"`` -- an account IS stored but its refresh token was
      rejected (HTTP 401 from ``POST /user-service/user/refreshtoken``).
    - ``None`` -- any other failure (bad login credentials, bad/expired MFA
      code, a malformed refresh response, ...) that doesn't fit either
      bucket above."""

    def __init__(self, message: str, *, kind: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class BambuLoginResult:
    """Outcome of ``login``/``verify_code``. ``status`` is ``"connected"``
    (tokens present) or ``"mfa_required"`` (a verification code is needed --
    ``mfa_context`` is the ENTIRE raw response body, carried through opaquely
    for the follow-up ``verify_code`` call; see module docstring). ``expires_at``
    is a best-effort estimate (``_estimate_expiry``) of the access token's
    lifetime, set only on the ``"connected"`` path -- callers persist it
    alongside ``access_token`` (see ``set_bambu_auth``)."""

    status: str
    access_token: str | None = None
    refresh_token: str | None = None
    mfa_context: dict | None = None
    expires_at: float | None = None


@dataclass(frozen=True)
class BambuAccessToken:
    """Result of ``refresh()``: a fresh access token, the refresh token to
    use NEXT time (Bambu may rotate it on use -- equal to the input when it
    doesn't), and a best-effort expiry (see ``_estimate_expiry``)."""

    access_token: str
    refresh_token: str
    expires_at: float


class BambuAuthState(BaseModel):
    """Decrypted at-rest shape. ``refresh_token`` is ``None`` when no
    account is connected. ``access_token``/``access_expires_at`` are the
    login/refresh-issued access token and its best-effort expiry estimate
    (see module docstring's Storage section) -- both ``None`` on a row
    written before this fix, or when a stored access token has never been
    set for some other reason; no migration needed since the settings row is
    plain JSON. ``refresh_failed_at`` (UTC ISO string, non-secret) is set
    when the LAST refresh attempt against the currently-stored refresh token
    failed (``mark_refresh_failed``/``_sync``) and cleared by a subsequent
    successful refresh or a fresh login -- ``GET /settings/bambu``'s
    ``needs_reconnect`` is ``bool(refresh_token and refresh_failed_at) and
    not has_valid_access_token()`` (a still-good stored access token means
    the account doesn't actually need reconnecting even if the last refresh
    attempt failed)."""

    account: str | None = None
    region: str = "global"
    refresh_token: str | None = None
    refresh_failed_at: str | None = None
    access_token: str | None = None
    access_expires_at: float | None = None

    def has_valid_access_token(self, now: float | None = None) -> bool:
        """True when a stored access token exists and hasn't (yet) passed
        its estimated expiry -- deliberately NO safety margin here (unlike
        ``get_access_token_sync``'s early-refresh check), since this is used
        both for the honest ``needs_reconnect`` determination (``GET
        /settings/bambu``) and for ``get_access_token_sync``'s late,
        refresh-just-failed fallback -- a token that's still technically
        good shouldn't be discarded just because a refresh attempt (which
        fires early, inside the margin) happened to fail."""
        if now is None:
            now = time.time()
        return (
            bool(self.access_token)
            and self.access_expires_at is not None
            and self.access_expires_at > now
        )


def base_url_for_region(region: str) -> str:
    """Shared region->host mapping (also used by
    ``app.importers.makerworld`` for the authenticated download call, which
    hits the SAME Bambu account host as auth -- see that module)."""
    return _BASE_URLS.get(region, _BASE_URLS["global"])


def _client(region: str = "global") -> httpx.Client:
    """The ONE httpx seam tests monkeypatch (mirrors every importer's
    ``_client``)."""
    return httpx.Client(
        base_url=base_url_for_region(region),
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": _UA},
    )


def _raise_on_error_status(
    r: httpx.Response, *, unauthorized_message: str, kind: str | None = None
) -> None:
    if r.status_code == 400:
        detail = "Incorrect account or password."
        with contextlib.suppress(ValueError, AttributeError):
            detail = r.json().get("error") or detail
        raise BambuAuthError(detail)
    if r.status_code == 401:
        raise BambuAuthError(unauthorized_message, kind=kind)
    r.raise_for_status()


def _post_login(body: dict, region: str) -> dict:
    with _client(region) as c:
        r = c.post("/user-service/user/login", json=body)
    _raise_on_error_status(
        r, unauthorized_message="Bambu rejected the login request (unauthorized)."
    )
    return r.json()


def _strip_secretish_keys(body: dict) -> dict:
    """Defense-in-depth for the MFA continuation context. The login-success/
    MFA-challenge shape is UNVERIFIED (see module docstring), so an
    unrecognized-but-still-sensitive value (a session/temp token under a key
    name not in our recognized set) could otherwise ride ``mfa_context`` all
    the way to the browser -- violating "no token ever leaves the backend".
    Drop any key whose lowercased name CONTAINS "token", or EQUALS
    "password"/"secret"/"access"/"accesstoken", while KEEPING the MFA
    continuation fields (``tfaKey``, ``loginType``, ...) the verify step
    needs. Deliberately NOT a blanket "*key*" match: ``tfaKey`` ends in
    "Key" and must survive."""
    dropped = {"password", "secret", "access", "accesstoken"}
    return {k: v for k, v in body.items() if "token" not in k.lower() and k.lower() not in dropped}


def _parse_login_response(body: dict) -> BambuLoginResult:
    """DOCUMENTED, NOT LIVE-CAPTURED (see module docstring) -- tolerant of
    several candidate field-name casings. Any recognized access+refresh
    token pair means success; otherwise the (secret-stripped) body becomes
    the opaque MFA continuation context."""
    access = body.get("accessToken") or body.get("access_token") or body.get("token")
    refresh_token = body.get("refreshToken") or body.get("refresh_token")
    if access and refresh_token:
        return BambuLoginResult(
            status="connected",
            access_token=access,
            refresh_token=refresh_token,
            expires_at=_estimate_expiry(body, access),
        )
    return BambuLoginResult(status="mfa_required", mfa_context=_strip_secretish_keys(body))


def login(account: str, password: str, region: str = "global") -> BambuLoginResult:
    """One login attempt. The password is used ONLY for this one HTTP call
    -- never stored, logged, or echoed back."""
    result = _parse_login_response(_post_login({"account": account, "password": password}, region))
    return result


# Only these client-echoed continuation keys may reach the outbound body.
_MFA_CONTEXT_KEYS = frozenset({"tfaKey", "loginType"})


def verify_code(
    account: str, code: str, region: str, context: dict | None = None
) -> BambuLoginResult:
    """Complete an MFA login with the emailed/SMS code. ``context`` is
    whatever ``login()`` returned as ``mfa_context`` -- its fields (e.g. a
    documented ``tfaKey``-style continuation token) are merged into the
    request body without overriding the explicit ``account``/``code``.
    Raises ``BambuAuthError`` if Bambu still doesn't return tokens (wrong or
    expired code) rather than looping into a second MFA round, since no such
    multi-round flow is documented."""
    body: dict = {"account": account, "code": code}
    for key, value in (context or {}).items():
        if key in _MFA_CONTEXT_KEYS and isinstance(value, str | int):
            body.setdefault(key, value)
    result = _parse_login_response(_post_login(body, region))
    if result.status != "connected":
        raise BambuAuthError("Bambu did not accept the verification code (wrong or expired code?).")
    return result


def refresh(refresh_token: str, region: str = "global") -> BambuAccessToken:
    """Exchange a stored refresh token for a fresh access token. Raises a
    ``kind="expired"`` ``BambuAuthError`` on an invalid/expired refresh token
    (HTTP 401, live-verified -- and, per the module docstring, verified to
    happen even on a fresh, never-yet-used token, which is why refresh is
    best-effort now). The caller (``get_access_token_sync``) surfaces this as
    "reconnect your Bambu account" and persists a ``refresh_failed_at``
    marker (``mark_refresh_failed_sync``) ONLY once no usable stored access
    token remains either -- see that function -- so the expiry survives past
    this one process/request (expiry-banner UX, ``GET /settings/bambu``'s
    ``needs_reconnect``)."""
    with _client(region) as c:
        r = c.post("/user-service/user/refreshtoken", json={"refreshToken": refresh_token})
    _raise_on_error_status(
        r,
        unauthorized_message=(
            "Bambu refresh token is invalid or expired -- reconnect the Bambu account in Settings."
        ),
        kind="expired",
    )
    body = r.json()
    access = body.get("accessToken") or body.get("access_token") or body.get("token")
    if not access:
        raise BambuAuthError("Bambu's refresh response did not include an access token.")
    new_refresh = body.get("refreshToken") or body.get("refresh_token") or refresh_token
    return BambuAccessToken(
        access_token=access,
        refresh_token=new_refresh,
        expires_at=_estimate_expiry(body, access),
    )


def _decode_jwt_exp(token: str) -> float | None:
    """Best-effort, UNVERIFIED (no signature check -- we trust the issuer,
    Bambu, not the bearer) read of a JWT's ``exp`` claim, used only as an
    expiry ESTIMATE for cache freshness. Never raises: any malformed token
    just falls through to the fixed fallback TTL in ``_estimate_expiry``."""
    try:
        _, payload_b64, _ = token.split(".")
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:  # noqa: BLE001 -- purely best-effort; any failure mode falls back
        return None


def _estimate_expiry(body: dict, access_token: str) -> float:
    for key in ("expiresIn", "accessTokenExpiresIn", "expires_in"):
        value = body.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return time.time() + float(value) - _EXPIRY_SAFETY_MARGIN_S
    exp_claim = _decode_jwt_exp(access_token)
    if exp_claim is not None:
        return exp_claim - _EXPIRY_SAFETY_MARGIN_S
    return time.time() + _FALLBACK_ACCESS_TOKEN_TTL_S


def _decrypt_optional_secret(settings: Settings, raw: str | None) -> str | None:
    """Like the ``refresh_token`` decrypt-with-legacy-plaintext-fallback
    below, but for a value that may be entirely absent (rows written before
    the access-token fix, or a state that was never given one)."""
    if not raw:
        return None
    try:
        return decrypt_secret(settings, raw)
    except InvalidToken:
        return raw  # defensive legacy-plaintext fallback, mirrors import_tokens.py


def _parse_float_or_none(value: object) -> float | None:
    """Tolerant read of ``access_expires_at`` off the settings row -- the
    column is plain JSON (no schema enforcement), so accept an int or a
    stringified float and quietly drop anything unparseable rather than
    raising (a garbage/missing expiry just means ``has_valid_access_token``
    treats the token as not-usable, never a hard failure to read the row at
    all)."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _decrypt_state(settings: Settings, value: dict | None) -> BambuAuthState:
    if not value:
        return BambuAuthState()
    raw = value.get("refresh_token")
    refresh_failed_at = value.get("refresh_failed_at")
    access_token = _decrypt_optional_secret(settings, value.get("access_token"))
    access_expires_at = _parse_float_or_none(value.get("access_expires_at"))
    if not raw:
        return BambuAuthState(
            account=value.get("account"),
            region=value.get("region") or "global",
            refresh_failed_at=refresh_failed_at,
            access_token=access_token,
            access_expires_at=access_expires_at,
        )
    try:
        token = decrypt_secret(settings, raw)
    except InvalidToken:
        token = raw  # defensive legacy-plaintext fallback, mirrors import_tokens.py
    return BambuAuthState(
        account=value.get("account"),
        region=value.get("region") or "global",
        refresh_token=token,
        refresh_failed_at=refresh_failed_at,
        access_token=access_token,
        access_expires_at=access_expires_at,
    )


async def get_bambu_auth(db: AsyncSession, settings: Settings) -> BambuAuthState:
    row = await db.get(Setting, SETTINGS_KEY)
    return _decrypt_state(settings, row.value if row else None)


def get_bambu_auth_sync(session: SyncSession, settings: Settings) -> BambuAuthState:
    row = session.get(Setting, SETTINGS_KEY)
    return _decrypt_state(settings, row.value if row else None)


def _bambu_auth_row_value(
    settings: Settings,
    *,
    account: str | None,
    region: str,
    refresh_token: str,
    access_token: str | None,
    access_expires_at: float | None,
) -> dict:
    """Shared row-shape builder for the async/sync twins below -- whole-row
    replace, deliberately WITHOUT a `refresh_failed_at` key: this is always
    called with a token Bambu just accepted (a fresh login/verify, or
    `get_access_token_sync` persisting the result of a SUCCESSFUL refresh),
    so any stale expiry marker from a previous attempt must not survive it --
    see `mark_refresh_failed`/`_sync` below for the ONLY place that key gets
    written. `access_token`/`access_expires_at` are stored (Fernet-encrypted
    for the token; the expiry is a non-secret float) only when the caller
    actually has one to give -- omitted, not written as null, when `None`
    (e.g. a caller that only has a rotated refresh token and no new access
    token; should not happen in practice, but tolerated)."""
    value: dict = {
        "account": account,
        "region": region,
        "refresh_token": encrypt_secret(settings, refresh_token),
    }
    if access_token is not None:
        value["access_token"] = encrypt_secret(settings, access_token)
    if access_expires_at is not None:
        value["access_expires_at"] = float(access_expires_at)
    return value


async def set_bambu_auth(
    db: AsyncSession,
    settings: Settings,
    *,
    account: str | None,
    region: str,
    refresh_token: str,
    access_token: str | None = None,
    access_expires_at: float | None = None,
) -> None:
    value = _bambu_auth_row_value(
        settings,
        account=account,
        region=region,
        refresh_token=refresh_token,
        access_token=access_token,
        access_expires_at=access_expires_at,
    )
    row = await db.get(Setting, SETTINGS_KEY)
    if row is None:
        db.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    _ACCESS_TOKEN_CACHE.clear()  # credential changed -- any cached access token is stale


def set_bambu_auth_sync(
    session: SyncSession,
    settings: Settings,
    *,
    account: str | None,
    region: str,
    refresh_token: str,
    access_token: str | None = None,
    access_expires_at: float | None = None,
) -> None:
    # See the async twin above -- same whole-row-replace-clears-the-marker
    # reasoning.
    value = _bambu_auth_row_value(
        settings,
        account=account,
        region=region,
        refresh_token=refresh_token,
        access_token=access_token,
        access_expires_at=access_expires_at,
    )
    row = session.get(Setting, SETTINGS_KEY)
    if row is None:
        session.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    session.commit()
    _ACCESS_TOKEN_CACHE.clear()


async def clear_bambu_auth(db: AsyncSession, settings: Settings) -> None:
    row = await db.get(Setting, SETTINGS_KEY)
    if row is not None:
        await db.delete(row)
        await db.commit()
    _ACCESS_TOKEN_CACHE.clear()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Expiry marker (`refresh_failed_at`, task: expiry-banner UX -- see module
# docstring's "re-login UX with expiry banner"). A NON-SECRET, UTC-ISO
# timestamp patched onto the SAME `bambu_auth` settings row, next to
# `account`/`region`/the encrypted `refresh_token` -- never a new row, never
# logged, never containing a token value itself. `mark_*` is only ever
# called right after a `refresh()` call that raised `kind="expired"`
# (see `get_access_token_sync`); `clear_*` handles the one case a fresh
# `set_bambu_auth(_sync)` write doesn't already cover for free (a refresh
# that succeeds WITHOUT rotating the refresh token, so no such write
# happens) -- see `get_access_token_sync` below. Both are no-ops when
# nothing is connected (nothing to stamp/clear) so a caller never needs to
# guard the call itself.
#
# `mark_*` additionally take the REJECTED refresh token and only stamp when
# the row's CURRENT refresh token still equals it (compare-and-set, post-
# review fix M1). Two workers can race to refresh the same expiring token:
# if worker A wins and rotates in a new, valid refresh token first (which
# also clears any marker -- see `set_bambu_auth`/`_sync` above), worker B's
# now-stale rejection must not stamp a failure onto that already-healthy
# row. `populate_existing=True` forces a fresh read past this session's own
# identity map (both sessionmakers use `expire_on_commit=False`, so a row
# fetched earlier in the SAME session -- e.g. by `get_bambu_auth_sync` at
# the top of `get_access_token_sync` -- would otherwise still show the
# pre-race snapshot even after another session committed the rotation).
# Comparison is plain equality on the DECRYPTED value -- Fernet ciphertext
# isn't stable across encryptions of the same plaintext, and neither side of
# the comparison is ever logged or printed.
# ---------------------------------------------------------------------------


def _decrypt_refresh_token(settings: Settings, raw: str) -> str:
    try:
        return decrypt_secret(settings, raw)
    except InvalidToken:
        return raw  # defensive legacy-plaintext fallback, mirrors _decrypt_state


async def mark_refresh_failed(
    db: AsyncSession, settings: Settings, rejected_refresh_token: str
) -> None:
    row = await db.get(Setting, SETTINGS_KEY, populate_existing=True)
    if row is None or not row.value or not row.value.get("refresh_token"):
        return
    stored = _decrypt_refresh_token(settings, row.value["refresh_token"])
    if stored != rejected_refresh_token:
        return
    row.value = {**row.value, "refresh_failed_at": _utc_now_iso()}
    await db.commit()


def mark_refresh_failed_sync(
    session: SyncSession, settings: Settings, rejected_refresh_token: str
) -> None:
    row = session.get(Setting, SETTINGS_KEY, populate_existing=True)
    if row is None or not row.value or not row.value.get("refresh_token"):
        return
    stored = _decrypt_refresh_token(settings, row.value["refresh_token"])
    if stored != rejected_refresh_token:
        return
    row.value = {**row.value, "refresh_failed_at": _utc_now_iso()}
    session.commit()


async def clear_refresh_failed(db: AsyncSession, settings: Settings) -> None:
    row = await db.get(Setting, SETTINGS_KEY)
    if row is None or not row.value or "refresh_failed_at" not in row.value:
        return
    value = dict(row.value)
    value.pop("refresh_failed_at", None)
    row.value = value
    await db.commit()


def clear_refresh_failed_sync(session: SyncSession, settings: Settings) -> None:
    row = session.get(Setting, SETTINGS_KEY)
    if row is None or not row.value or "refresh_failed_at" not in row.value:
        return
    value = dict(row.value)
    value.pop("refresh_failed_at", None)
    row.value = value
    session.commit()


def get_access_token_sync(session: SyncSession, settings: Settings) -> str:
    """A currently-valid Bambu access token for worker use (MakerWorld's
    authenticated download/search calls, ``app.importers.makerworld``).

    Order of preference (see module docstring's Storage section for why):
    1. The in-memory, per-process cache (``_ACCESS_TOKEN_CACHE``, keyed by
       the refresh token on file) -- unchanged, avoids re-decrypting the row
       on every call within one process.
    2. The STORED access token (Fernet-encrypted at rest, decrypted by
       ``get_bambu_auth_sync``), if it's still valid past the early-refresh
       safety margin -- NO network call. This is the headline fix: Bambu's
       login-issued access token is a long-lived JWT, so most calls never
       need to touch ``refresh()`` (Bambu's refresh endpoint, historically
       unreliable and live-verified 2026-07-12 to reject even fresh, never-
       yet-used refresh tokens) at all.
    3. ``refresh()``, once the stored access token is within the margin of
       its estimated expiry (or absent). A successful refresh always
       persists the new access token + expiry (``set_bambu_auth_sync``,
       whether or not the refresh token itself rotated) -- the whole-row
       write also clears any stale ``refresh_failed_at`` marker for free.
    4. If ``refresh()`` itself fails with ``kind="expired"``: the stored
       access token ONE more time, this time with NO margin (plain
       not-yet-expired) -- a refresh attempt fires early (inside the
       margin), so its failure alone shouldn't strand a caller holding a
       token that's still technically good. Only when no usable access
       token remains at all does this persist ``refresh_failed_at``
       (``mark_refresh_failed_sync``, expiry-banner UX,
       ``GET /settings/bambu``'s ``needs_reconnect``) and re-raise.

    Raises a ``kind="not_configured"`` ``BambuAuthError`` when NEITHER a
    refresh token nor a valid stored access token exists (nothing connected,
    or a connection whose access token has expired with no refresh token to
    fall back on).
    """
    state = get_bambu_auth_sync(session, settings)
    now = time.time()

    if state.refresh_token:
        cached = _ACCESS_TOKEN_CACHE.get(state.refresh_token)
        if cached is not None and cached[1] > now:
            return cached[0]

    if (
        state.access_token
        and state.access_expires_at
        and (state.access_expires_at > now + _EXPIRY_SAFETY_MARGIN_S)
    ):
        if state.refresh_token:
            _ACCESS_TOKEN_CACHE[state.refresh_token] = (state.access_token, state.access_expires_at)
        return state.access_token

    if not state.refresh_token:
        raise BambuAuthError(
            "no Bambu account is connected -- connect one in Settings.", kind="not_configured"
        )

    try:
        token = refresh(state.refresh_token, state.region)
    except BambuAuthError as exc:
        if exc.kind == "expired":
            if state.has_valid_access_token(now):
                # The refresh call fired early (inside the margin) and
                # failed (Bambu's refresh endpoint is unreliable -- see
                # module docstring) but the stored access token itself is
                # still genuinely unexpired -- use it rather than stamping
                # a reconnect-worthy failure the account doesn't actually
                # have yet.
                return state.access_token
            mark_refresh_failed_sync(session, settings, state.refresh_token)
        raise

    # Persist the new access token + expiry either way (rotation or not) --
    # the whole-row write also clears any stale `refresh_failed_at` marker
    # (see `_bambu_auth_row_value`'s comment), which subsumes the old
    # rotation-only-write / explicit-clear split.
    set_bambu_auth_sync(
        session,
        settings,
        account=state.account,
        region=state.region,
        refresh_token=token.refresh_token,
        access_token=token.access_token,
        access_expires_at=token.expires_at,
    )
    _ACCESS_TOKEN_CACHE[token.refresh_token] = (token.access_token, token.expires_at)
    return token.access_token
