"""One home for the ``settings`` key/value row helpers every config module used
to re-implement: read a JSON row (async / sync), replace or merge it, plus the
write-only secret conventions (the ``"***"`` keep-sentinel and the Fernet
decrypt-with-raw-fallback for values stored before encryption existed)."""

from __future__ import annotations

from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import decrypt_secret
from app.models import Setting

KEEP = "***"  # write-only secret sentinel: omitted / None / "***" keeps, "" clears


def is_keep(value: object) -> bool:
    """True when a secret patch value means "leave the stored secret alone"."""
    return value is None or value == KEEP


def decrypt_or_raw(settings: Settings, token: str) -> str:
    """Decrypt ``token``; a value that was never encrypted (pre-encryption
    rows, or a rotated key) is returned as stored rather than raising."""
    try:
        return decrypt_secret(settings, token)
    except InvalidToken:
        return token


async def get_value(db: AsyncSession, key: str) -> dict | None:
    row = await db.get(Setting, key)
    return row.value if row else None


def get_value_sync(session: Session, key: str) -> dict | None:
    row = session.get(Setting, key)
    return row.value if row else None


async def put_value(db: AsyncSession, key: str, value: dict) -> None:
    """Replace the row (caller commits)."""
    row = await db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def put_value_sync(session: Session, key: str, value: dict) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


async def merge_value(db: AsyncSession, key: str, patch: dict) -> dict:
    """Shallow-merge ``patch`` into the row (caller commits); returns the result."""
    value = {**(await get_value(db, key) or {}), **patch}
    await put_value(db, key, value)
    return value
