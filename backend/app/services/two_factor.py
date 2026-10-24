"""Second-factor logic: TOTP (replay-guarded), single-use backup codes, emailed
OTP codes and the hashed short-lived tokens that glue the login steps together.

Everything stored is hashed or encrypted: ephemeral tokens as sha256 (they are
256-bit random, so a fast hash is right), OTP + backup codes with argon2, the
TOTP secret with the at-rest Fernet key. Codes and secrets are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
import pyotp
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import AuthEphemeralToken, Setting, User, UserOtpCode, UserTotp
from app.security import hash_password, verify_password
from app.services import mailer

PRE_AUTH = "pre_auth"
TOTP_SETUP = "totp_setup"
OIDC_2FA = "oidc_2fa"

PRE_AUTH_TTL = timedelta(minutes=5)
SETUP_TTL = timedelta(minutes=10)
OTP_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
OTP_MAX_SENDS = 3
OTP_SEND_WINDOW = timedelta(minutes=10)
TOTP_STEP_S = 30
TOTP_WINDOW = 1

BACKUP_COUNT = 10
BACKUP_LEN = 8
# No 0/O/1/I/L: unambiguous when read off a printout.
BACKUP_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

ISSUER = "3D Model Manager"
SECURITY_KEY = "security"


def err(status: int, code: str, message: str = "") -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message or code})


def now() -> datetime:
    return datetime.now(UTC)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- security settings ------------------------------------------------------


async def require_2fa_enabled(db: AsyncSession) -> bool:
    row = await db.get(Setting, SECURITY_KEY)
    return bool(row and (row.value or {}).get("require_2fa") is True)


async def set_require_2fa(db: AsyncSession, value: bool) -> None:
    row = await db.get(Setting, SECURITY_KEY)
    if row is None:
        db.add(Setting(key=SECURITY_KEY, value={"require_2fa": value}))
    else:
        row.value = {**(row.value or {}), "require_2fa": value}


# --- state ------------------------------------------------------------------


async def get_totp(db: AsyncSession, user_id: int, *, lock: bool = False) -> UserTotp | None:
    q = select(UserTotp).where(UserTotp.user_id == user_id, UserTotp.is_enabled.is_(True))
    if lock:
        q = q.with_for_update()
    return (await db.execute(q)).scalar_one_or_none()


@dataclass(slots=True)
class Status:
    totp: bool
    email: bool
    backup_remaining: int

    @property
    def methods(self) -> list[str]:
        out = []
        if self.totp:
            out.append("totp")
        if self.email:
            out.append("email")
        if self.totp and self.backup_remaining > 0:
            out.append("backup")
        return out

    @property
    def enabled(self) -> bool:
        return self.totp or self.email


async def status_for(db: AsyncSession, user: User) -> Status:
    row = await get_totp(db, user.id)
    return Status(
        totp=row is not None,
        email=bool(user.email_otp_enabled),
        backup_remaining=len(row.backup_codes_json) if row else 0,
    )


async def must_enroll(db: AsyncSession, user: User) -> bool:
    return await require_2fa_enabled(db) and not (await status_for(db, user)).enabled


# --- ephemeral tokens ---------------------------------------------------------


async def issue_token(
    db: AsyncSession,
    token_type: str,
    user_id: int,
    ttl: timedelta,
    payload: dict | None = None,
) -> str:
    """Create a token row (caller commits); returns the raw token, shown once."""
    raw = secrets.token_urlsafe(32)
    db.add(
        AuthEphemeralToken(
            token_hash=_sha(raw),
            token_type=token_type,
            user_id=user_id,
            payload=payload or {},
            expires_at=now() + ttl,
            created_by_user_id=user_id,
        )
    )
    await db.flush()
    return raw


async def load_token(
    db: AsyncSession, raw: str, types: tuple[str, ...], *, user_id: int | None = None
) -> AuthEphemeralToken | None:
    """Row-locked live token, or ``None`` (unknown / wrong type / expired).
    The lock serialises concurrent guesses so the attempt cap holds."""
    q = (
        select(AuthEphemeralToken)
        .where(
            AuthEphemeralToken.token_hash == _sha(raw),
            AuthEphemeralToken.token_type.in_(types),
            AuthEphemeralToken.expires_at > now(),
        )
        .with_for_update()
    )
    tok = (await db.execute(q)).scalar_one_or_none()
    if tok is None or (user_id is not None and tok.user_id != user_id):
        return None
    return tok


async def fail_token(db: AsyncSession, tok: AuthEphemeralToken) -> bool:
    """Count a failed attempt and commit. ``True`` when the token is now dead."""
    tok.attempts += 1
    dead = tok.attempts >= MAX_ATTEMPTS
    if dead:
        await db.delete(tok)
    await db.commit()
    return dead


# --- TOTP ---------------------------------------------------------------------


def _now_ts() -> float:
    return time.time()


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, user: User) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.email or user.username, issuer_name=ISSUER)


def match_counter(secret: str, code: str, at: float | None = None) -> int | None:
    """The time-step ``code`` is valid for within +-``TOTP_WINDOW``, else ``None``.
    Every step is compared in constant time."""
    code = code.strip().replace(" ", "")
    if not (code.isascii() and code.isdigit() and len(code) == 6):
        return None
    totp = pyotp.TOTP(secret)
    now_step = int((_now_ts() if at is None else at) // TOTP_STEP_S)
    hit: int | None = None
    for step in range(now_step - TOTP_WINDOW, now_step + TOTP_WINDOW + 1):
        if hmac.compare_digest(totp.generate_otp(step), code):
            hit = step
    return hit


def _secret_of(row: UserTotp) -> str | None:
    try:
        return decrypt_secret(get_settings(), row.secret_enc)
    except InvalidToken:
        return None


async def verify_totp(db: AsyncSession, user_id: int, code: str) -> bool:
    """Accept ``code`` once: a step <= ``last_totp_counter`` is a replay."""
    row = await get_totp(db, user_id, lock=True)
    secret = _secret_of(row) if row else None
    if row is None or secret is None:
        return False
    step = match_counter(secret, code)
    if step is None or step <= row.last_totp_counter:
        return False
    row.last_totp_counter = step
    return True


# --- backup codes -------------------------------------------------------------


def normalise_backup(code: str) -> str:
    return code.replace("-", "").replace(" ", "").upper()


def generate_backup_codes() -> list[str]:
    return [
        "".join(secrets.choice(BACKUP_ALPHABET) for _ in range(BACKUP_LEN))
        for _ in range(BACKUP_COUNT)
    ]


async def hash_backup_codes(codes: list[str]) -> list[str]:
    return await anyio.to_thread.run_sync(lambda: [hash_password(c) for c in codes])


def _match_backup(code: str, hashes: list[str]) -> int | None:
    hit: int | None = None
    for i, h in enumerate(hashes):  # never short-circuit: constant work
        if verify_password(code, h) and hit is None:
            hit = i
    return hit


async def verify_backup(db: AsyncSession, user_id: int, code: str) -> bool:
    row = await get_totp(db, user_id, lock=True)
    code = normalise_backup(code)
    if row is None or len(code) != BACKUP_LEN:
        return False
    hashes = list(row.backup_codes_json)
    idx = await anyio.to_thread.run_sync(_match_backup, code, hashes)
    if idx is None:
        return False
    row.backup_codes_json = [h for i, h in enumerate(hashes) if i != idx]
    return True


# --- email OTP ------------------------------------------------------------------


async def send_email_otp(db: AsyncSession, user: User) -> None:
    """Mint + email a 6-digit code (caller commits). Raises coded HTTP errors."""
    if not user.email:
        raise err(409, "no_email", "this account has no email address")
    if not await mailer.is_configured(db):
        raise err(409, "smtp_not_configured", "server SMTP is not configured")
    recent = await db.scalar(
        select(func.count())
        .select_from(UserOtpCode)
        .where(
            UserOtpCode.user_id == user.id,
            UserOtpCode.created_at > now() - OTP_SEND_WINDOW,
        )
    )
    if (recent or 0) >= OTP_MAX_SENDS:
        raise err(409, "rate_limited", "too many codes requested; try again later")
    await db.execute(
        update(UserOtpCode)
        .where(UserOtpCode.user_id == user.id, UserOtpCode.used.is_(False))
        .values(used=True)
    )
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(
        UserOtpCode(
            user_id=user.id,
            code_hash=await anyio.to_thread.run_sync(hash_password, code),
            expires_at=now() + OTP_TTL,
            created_by_user_id=user.id,
        )
    )
    await db.flush()
    mailer.send(
        user.email,
        "Your sign-in code",
        f"Your verification code is {code}. It expires in 10 minutes.\n"
        "If you did not request it, you can ignore this email.",
    )


async def verify_email_otp(db: AsyncSession, user_id: int, code: str) -> bool:
    row = (
        await db.execute(
            select(UserOtpCode)
            .where(
                UserOtpCode.user_id == user_id,
                UserOtpCode.used.is_(False),
                UserOtpCode.expires_at > now(),
            )
            .order_by(UserOtpCode.id.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    code = code.strip()
    if row is None or row.attempts >= MAX_ATTEMPTS:
        return False
    row.attempts += 1
    ok = await anyio.to_thread.run_sync(verify_password, code, row.code_hash)
    if ok:
        row.used = True
    return ok


# --- dispatch -----------------------------------------------------------------


async def verify_method(db: AsyncSession, user: User, method: str, code: str) -> bool:
    if method == "totp":
        return await verify_totp(db, user.id, code)
    if method == "backup":
        return await verify_backup(db, user.id, code)
    if method == "email":
        return await verify_email_otp(db, user.id, code)
    return False


async def verify_reauth(db: AsyncSession, user: User, value: str) -> bool:
    """Sensitive self-service actions: current password OR any enrolled factor."""
    if user.password_hash and await anyio.to_thread.run_sync(
        verify_password, value, user.password_hash
    ):
        return True
    st = await status_for(db, user)
    for method, enrolled in (("totp", st.totp), ("backup", st.totp), ("email", st.email)):
        if enrolled and await verify_method(db, user, method, value):
            return True
    return False


async def wipe_user_2fa(db: AsyncSession, user: User) -> None:
    """Remove every second factor and pending token/code for ``user``."""
    await db.execute(delete(UserTotp).where(UserTotp.user_id == user.id))
    await db.execute(delete(UserOtpCode).where(UserOtpCode.user_id == user.id))
    await db.execute(delete(AuthEphemeralToken).where(AuthEphemeralToken.user_id == user.id))
    user.email_otp_enabled = False
