"""Two-factor auth endpoints (T6): the public login-completion pair and the
session-only self-service enrolment routes. See
``docs/superpowers/plans/2026-10-24-t6-be.md`` for the contract."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import MeOut, build_me, start_session
from app.api.deps import AuthContext, require_session
from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import get_db
from app.models import AuditLog, AuthEphemeralToken, User, UserTotp
from app.services import mailer
from app.services import two_factor as tf
from app.services.permissions import load_grants

public_router = APIRouter(prefix="/auth/2fa", tags=["auth"])
protected_router = APIRouter(prefix="/auth/2fa", tags=["auth"])

_PRE_AUTH_TYPES = (tf.PRE_AUTH, tf.OIDC_2FA)


class VerifyIn(BaseModel):
    pre_auth_token: str = Field(min_length=1, max_length=256)
    method: Literal["totp", "email", "backup"]
    code: str = Field(min_length=1, max_length=64)


class VerifyOut(MeOut):
    return_to: str | None = None


class PreAuthIn(BaseModel):
    pre_auth_token: str = Field(min_length=1, max_length=256)


class SentOut(BaseModel):
    sent: bool = True


def _gone() -> Exception:
    return tf.err(status.HTTP_410_GONE, "pre_auth_expired", "sign-in expired; start again")


async def _pre_auth_user(db: AsyncSession, raw: str) -> tuple[AuthEphemeralToken, User]:
    tok = await tf.load_token(db, raw, _PRE_AUTH_TYPES)
    if tok is None or tok.user_id is None:
        raise _gone()
    user = await db.get(User, tok.user_id)
    if user is None or not user.is_active:
        await db.delete(tok)
        await db.commit()
        raise _gone()
    return tok, user


@public_router.post("/verify", response_model=VerifyOut)
async def verify(
    payload: VerifyIn,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VerifyOut:
    tok, user = await _pre_auth_user(db, payload.pre_auth_token)
    st = await tf.status_for(db, user)
    if payload.method not in st.methods:
        raise tf.err(422, "method_unavailable", "that method is not enabled")
    if not await tf.verify_method(db, user, payload.method, payload.code):
        if await tf.fail_token(db, tok):
            raise _gone()
        raise tf.err(status.HTTP_401_UNAUTHORIZED, "invalid_code", "invalid code")
    return_to = (tok.payload or {}).get("return_to")
    await db.delete(tok)  # single use; committed together with the session
    session = await start_session(db, response, user, request)
    groups, perms, acl = await load_grants(db, user)
    ctx = AuthContext(user=user, session=session, perms=perms, printer_acl=acl, groups=groups)
    me = await build_me(db, ctx)
    return VerifyOut(**me.model_dump(), return_to=return_to)


@public_router.post("/email/send", response_model=SentOut)
async def email_send(payload: PreAuthIn, db: AsyncSession = Depends(get_db)) -> SentOut:
    _, user = await _pre_auth_user(db, payload.pre_auth_token)
    if not user.email_otp_enabled:
        raise tf.err(422, "method_unavailable", "email codes are not enabled")
    await tf.send_email_otp(db, user)
    await db.commit()
    return SentOut()


# --- self-service -----------------------------------------------------------


class StatusOut(BaseModel):
    totp_enabled: bool
    email_otp_enabled: bool
    backup_codes_remaining: int
    methods: list[str]
    smtp_configured: bool
    email_available: bool


@protected_router.get("/status", response_model=StatusOut)
async def get_status(
    ctx: AuthContext = Depends(require_session), db: AsyncSession = Depends(get_db)
) -> StatusOut:
    st = await tf.status_for(db, ctx.user)
    smtp = await mailer.is_configured(db)
    return StatusOut(
        totp_enabled=st.totp,
        email_otp_enabled=st.email,
        backup_codes_remaining=st.backup_remaining,
        methods=st.methods,
        smtp_configured=smtp,
        email_available=smtp and bool(ctx.user.email),
    )


def _audit(ctx: AuthContext, action: str, target: int | None = None) -> AuditLog:
    return AuditLog(
        user_id=ctx.user.id,
        created_by_user_id=ctx.user.id,
        action=action,
        payload={"user_id": target or ctx.user.id},
    )


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_url: str
    setup_token: str


@protected_router.post("/totp/setup", response_model=TotpSetupOut)
async def totp_setup(
    ctx: AuthContext = Depends(require_session), db: AsyncSession = Depends(get_db)
) -> TotpSetupOut:
    if await tf.get_totp(db, ctx.user.id) is not None:
        raise tf.err(409, "already_enabled", "an authenticator is already enrolled")
    await db.execute(
        delete(AuthEphemeralToken).where(
            AuthEphemeralToken.user_id == ctx.user.id,
            AuthEphemeralToken.token_type == tf.TOTP_SETUP,
        )
    )
    secret = tf.new_secret()
    token = await tf.issue_token(
        db,
        tf.TOTP_SETUP,
        ctx.user.id,
        tf.SETUP_TTL,
        {"secret_enc": encrypt_secret(get_settings(), secret)},
    )
    await db.commit()
    return TotpSetupOut(
        secret=secret, otpauth_url=tf.provisioning_uri(secret, ctx.user), setup_token=token
    )


class TotpEnableIn(BaseModel):
    setup_token: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=64)


class BackupCodesOut(BaseModel):
    backup_codes: list[str]


@protected_router.post("/totp/enable", response_model=BackupCodesOut)
async def totp_enable(
    payload: TotpEnableIn,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> BackupCodesOut:
    tok = await tf.load_token(db, payload.setup_token, (tf.TOTP_SETUP,), user_id=ctx.user.id)
    if tok is None:
        raise tf.err(410, "setup_expired", "setup expired; start again")
    secret = decrypt_secret(get_settings(), tok.payload["secret_enc"])
    step = tf.match_counter(secret, payload.code)
    if step is None:
        if await tf.fail_token(db, tok):
            raise tf.err(410, "setup_expired", "too many attempts; start again")
        raise tf.err(400, "invalid_code", "invalid code")
    if await tf.get_totp(db, ctx.user.id) is not None:
        raise tf.err(409, "already_enabled", "an authenticator is already enrolled")
    codes = tf.generate_backup_codes()
    hashes = await tf.hash_backup_codes(codes)
    await db.execute(delete(UserTotp).where(UserTotp.user_id == ctx.user.id))
    db.add(
        UserTotp(
            user_id=ctx.user.id,
            secret_enc=tok.payload["secret_enc"],
            is_enabled=True,
            backup_codes_json=hashes,
            last_totp_counter=step,
            enabled_at=tf.now(),
            created_by_user_id=ctx.user.id,
        )
    )
    await db.delete(tok)
    db.add(_audit(ctx, "user.2fa.totp_enabled"))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise tf.err(409, "already_enabled", "an authenticator is already enrolled") from None
    return BackupCodesOut(backup_codes=codes)


class ReauthIn(BaseModel):
    code_or_password: str = Field(min_length=1, max_length=256)


async def _reauth_or_400(db: AsyncSession, user: User, value: str) -> None:
    if not await tf.verify_reauth(db, user, value):
        await db.commit()  # persist attempt counters
        raise tf.err(400, "invalid_code", "invalid code or password")


@protected_router.post("/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(
    payload: ReauthIn,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    if await tf.get_totp(db, ctx.user.id) is None:
        raise tf.err(409, "totp_not_enabled", "no authenticator is enrolled")
    await _reauth_or_400(db, ctx.user, payload.code_or_password)
    await db.execute(delete(UserTotp).where(UserTotp.user_id == ctx.user.id))
    db.add(_audit(ctx, "user.2fa.totp_disabled"))
    await db.commit()


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)


@protected_router.post("/backup/regenerate", response_model=BackupCodesOut)
async def backup_regenerate(
    payload: CodeIn,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> BackupCodesOut:
    if await tf.get_totp(db, ctx.user.id) is None:
        raise tf.err(409, "totp_not_enabled", "no authenticator is enrolled")
    if not await tf.verify_totp(db, ctx.user.id, payload.code):
        await db.commit()
        raise tf.err(400, "invalid_code", "invalid code")
    codes = tf.generate_backup_codes()
    row = await tf.get_totp(db, ctx.user.id, lock=True)
    assert row is not None  # noqa: S101 - locked above in the same transaction
    row.backup_codes_json = await tf.hash_backup_codes(codes)
    db.add(_audit(ctx, "user.2fa.backup_regenerated"))
    await db.commit()
    return BackupCodesOut(backup_codes=codes)


@protected_router.post("/email/request", response_model=SentOut)
async def email_request(
    ctx: AuthContext = Depends(require_session), db: AsyncSession = Depends(get_db)
) -> SentOut:
    """Email a code for re-authenticating a sensitive action (e.g. disable)."""
    if not ctx.user.email_otp_enabled:
        raise tf.err(409, "not_enabled", "email codes are not enabled")
    await tf.send_email_otp(db, ctx.user)
    await db.commit()
    return SentOut()


@protected_router.post("/email/enable", response_model=SentOut)
async def email_enable(
    ctx: AuthContext = Depends(require_session), db: AsyncSession = Depends(get_db)
) -> SentOut:
    if ctx.user.email_otp_enabled:
        raise tf.err(409, "already_enabled", "email codes are already enabled")
    await tf.send_email_otp(db, ctx.user)
    await db.commit()
    return SentOut()


@protected_router.post("/email/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def email_confirm(
    payload: CodeIn,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    if not await tf.verify_email_otp(db, ctx.user.id, payload.code):
        await db.commit()
        raise tf.err(400, "invalid_code", "invalid code")
    ctx.user.email_otp_enabled = True
    db.add(_audit(ctx, "user.2fa.email_enabled"))
    await db.commit()


@protected_router.post("/email/disable", status_code=status.HTTP_204_NO_CONTENT)
async def email_disable(
    payload: ReauthIn,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    if not ctx.user.email_otp_enabled:
        raise tf.err(409, "not_enabled", "email codes are not enabled")
    await _reauth_or_400(db, ctx.user, payload.code_or_password)
    ctx.user.email_otp_enabled = False
    db.add(_audit(ctx, "user.2fa.email_disabled"))
    await db.commit()
