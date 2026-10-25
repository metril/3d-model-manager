"""Browser-extension bearer tokens (M10 Workstream A). A leaked extension
token must only ever be able to create imports and set the MakerWorld
cookie (``app.api.ext``) -- narrowly scoped, unlike the cookie session which
grants the full session-gated API.

Hashing choice: the token itself is ``TOKEN_BYTES`` of ``secrets``-module
randomness (256 bits of entropy from ``token_urlsafe``), not a human-chosen
secret. A fast one-way hash (SHA-256) is the correct primitive here -- it
lets ``verify`` cost one cheap digest per request instead of paying argon2's
deliberately-slow KDF (``app.security``, used for the human password) on
every single API call, and argon2's brute-force resistance buys nothing
against a secret with this much entropy: there is no dictionary to attack.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiToken

TOKEN_BYTES = 32

# Mirrors app.api.deps.LAST_SEEN_THROTTLE -- avoids a DB write on every
# single token-authenticated request.
LAST_USED_THROTTLE = timedelta(seconds=60)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def mint(db: AsyncSession, *, user_id: int, label: str) -> tuple[str, ApiToken]:
    """Generate a new token, store only its hash, and return the plaintext
    ONCE -- the caller (``POST /settings/api-tokens``) is the only place the
    plaintext is ever visible; it is never stored or logged."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    row = ApiToken(token_hash=_hash(token), user_id=user_id, label=label)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return token, row


# Scoped API keys (v4). scope -> the owner permissions it exercises. A key's
# effective perms are owner-perms & the union of its scopes' perms, so a key
# can never exceed its creator, and never carries ``admin``/``settings:write``.
KEY_PREFIX = "tdmm_"
SCOPE_PERMS: dict[str, frozenset[str]] = {
    "read": frozenset(),
    "control": frozenset({"printers:control"}),
    "queue": frozenset({"printers:print"}),
    "upload": frozenset({"library:write"}),
}
SCOPES = tuple(SCOPE_PERMS)
DEFAULT_RATE_LIMIT = 120  # requests / minute / key
MAX_RATE_LIMIT = 6000


def scope_perms(scopes: list[str] | None) -> frozenset[str]:
    out: set[str] = set()
    for sc in scopes or []:
        out |= SCOPE_PERMS.get(sc, frozenset())
    return frozenset(out)


async def mint_key(
    db: AsyncSession,
    *,
    user_id: int,
    label: str,
    scopes: list[str],
    printer_ids: list[int] | None = None,
    expires_at: datetime | None = None,
    rate_limit_per_min: int | None = None,
) -> tuple[str, ApiToken]:
    """Mint a scoped API key. Plaintext is returned once; only the hash and a
    short display prefix are stored."""
    key = KEY_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    row = ApiToken(
        token_hash=_hash(key),
        user_id=user_id,
        label=label,
        scopes=sorted(set(scopes)),
        printer_ids=sorted(set(printer_ids)) if printer_ids else None,
        expires_at=expires_at,
        rate_limit_per_min=rate_limit_per_min,
        key_prefix=key[:10],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return key, row


async def verify(db: AsyncSession, token: str) -> ApiToken | None:
    """Look up the row for ``token`` by hash, throttled-bump ``last_used_at``
    on a hit, and return the row (or ``None`` for an unknown/revoked/expired
    token)."""
    row = (
        await db.execute(select(ApiToken).where(ApiToken.token_hash == _hash(token)))
    ).scalar_one_or_none()
    if row is None:
        return None

    now = datetime.now(UTC)
    if row.expires_at is not None and row.expires_at <= now:
        return None
    if row.last_used_at is None or now - row.last_used_at > LAST_USED_THROTTLE:
        row.last_used_at = now
        await db.commit()

    return row


async def list_tokens(
    db: AsyncSession, *, user_id: int | None = None, keys: bool | None = None
) -> list[ApiToken]:
    """``user_id=None`` lists every user's tokens (admin view). ``keys``:
    ``True`` = scoped API keys only, ``False`` = legacy extension tokens only."""
    stmt = select(ApiToken).order_by(ApiToken.created_at.desc())
    if user_id is not None:
        stmt = stmt.where(ApiToken.user_id == user_id)
    if keys is True:
        stmt = stmt.where(ApiToken.scopes.is_not(None))
    elif keys is False:
        stmt = stmt.where(ApiToken.scopes.is_(None))
    return list((await db.execute(stmt)).scalars().all())


async def revoke(
    db: AsyncSession, token_id: int, *, user_id: int | None = None, keys: bool | None = None
) -> bool:
    """Delete the token row by id (scoped to ``user_id`` unless ``None``).
    Returns whether a row was actually deleted (``False`` for an unknown or
    not-owned id, so the caller can 404)."""
    stmt = delete(ApiToken).where(ApiToken.id == token_id)
    if user_id is not None:
        stmt = stmt.where(ApiToken.user_id == user_id)
    if keys is True:
        stmt = stmt.where(ApiToken.scopes.is_not(None))
    elif keys is False:
        stmt = stmt.where(ApiToken.scopes.is_(None))
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount > 0
