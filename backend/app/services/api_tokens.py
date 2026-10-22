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


async def verify(db: AsyncSession, token: str) -> ApiToken | None:
    """Look up the row for ``token`` by hash, throttled-bump ``last_used_at``
    on a hit, and return the row (or ``None`` for an unknown/revoked
    token)."""
    row = (
        await db.execute(select(ApiToken).where(ApiToken.token_hash == _hash(token)))
    ).scalar_one_or_none()
    if row is None:
        return None

    now = datetime.now(UTC)
    if row.last_used_at is None or now - row.last_used_at > LAST_USED_THROTTLE:
        row.last_used_at = now
        await db.commit()

    return row


async def list_tokens(db: AsyncSession) -> list[ApiToken]:
    rows = (await db.execute(select(ApiToken).order_by(ApiToken.created_at.desc()))).scalars().all()
    return list(rows)


async def revoke(db: AsyncSession, token_id: int) -> bool:
    """Delete the token row by id. Returns whether a row was actually
    deleted (``False`` for an unknown id, so the caller can 404)."""
    result = await db.execute(delete(ApiToken).where(ApiToken.id == token_id))
    await db.commit()
    return result.rowcount > 0
