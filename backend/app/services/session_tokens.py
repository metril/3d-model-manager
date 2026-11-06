"""Login-session token helpers: the cookie carries a random token, the DB
stores only its SHA-256 (same scheme as API tokens), so a DB read never
yields a usable cookie."""

from __future__ import annotations

import hashlib
import secrets


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
