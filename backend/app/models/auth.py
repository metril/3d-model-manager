"""Auth domain: ``users``, ``sessions`` (SPEC "Data model")."""

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum
from app.models.enums import MembershipSource, UserRole


class User(Base):
    """Account (local password and/or OIDC identity) with a coarse role."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # NULL for OIDC-only accounts (no local password login possible).
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    email: Mapped[str | None] = mapped_column(postgresql.CITEXT(), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true(), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        str_enum(UserRole, "user_role"), server_default=UserRole.MEMBER.value, nullable=False
    )
    oidc_issuer: Mapped[str | None] = mapped_column(String, nullable=True)
    oidc_subject: Mapped[str | None] = mapped_column(String, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
    email_otp_enabled: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)


class Session(Base):
    """Cookie-backed login session, keyed by the token itself (SPEC:
    ``sessions(id uuid=cookie token, user_id, created_at, expires_at,
    last_seen_at)``).
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class ApiToken(Base):
    """Bearer token for the browser-extension auth plane (M10 Workstream A),
    separate from the cookie session above -- a browser extension browsing a
    gallery site can't present ``tdmm_session`` (samesite=lax, cross-site, no
    CORS), so it authenticates with one of these instead. Only the SHA-256
    hash is stored (see ``app.services.api_tokens`` for why a fast hash is
    the right call here, unlike the argon2id used for the human password);
    the plaintext token is generated once at mint time and never persisted.
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # v4 scoped API keys. ``scopes IS NULL`` marks a legacy browser-extension
    # token (``/ext`` v1 only); a non-NULL list is a scoped key usable on
    # ``/ext/v2`` only.
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    # Optional printer allowlist (NULL = every printer the owner may reach).
    printer_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    rate_limit_per_min: Mapped[int | None] = mapped_column(nullable=True)
    key_prefix: Mapped[str | None] = mapped_column(String, nullable=True)


class Group(Base):
    """Named set of users carrying a list of permission strings."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    permissions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[MembershipSource] = mapped_column(
        str_enum(MembershipSource, "membership_source"),
        server_default=MembershipSource.LOCAL.value,
        nullable=False,
    )


class PrinterAccess(Base):
    """Per-group grant on one printer."""

    __tablename__ = "printer_access"

    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    printer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), primary_key=True
    )
    can_print: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)
    can_control: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)


class OidcProvider(Base):
    __tablename__ = "oidc_providers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    discovery_url: Mapped[str] = mapped_column(String, nullable=False)
    client_id: Mapped[str] = mapped_column(String, nullable=False)
    client_secret_enc: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[str] = mapped_column(
        String, server_default="openid profile email groups", nullable=False
    )
    groups_claim: Mapped[str] = mapped_column(String, server_default="groups", nullable=False)
    group_mapping: Mapped[dict[str, int]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    admin_group: Mapped[str | None] = mapped_column(String, nullable=True)
    auto_create_users: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)
    link_by_email: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=true(), nullable=False)
    # T6: the IdP enforces its own MFA, so skip our second factor for this provider.
    trusts_idp_mfa: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class UserTotp(Base):
    """A user's authenticator-app (TOTP) enrolment plus single-use backup codes."""

    __tablename__ = "user_totp"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    secret_enc: Mapped[str] = mapped_column(String, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)
    # argon2 hashes of the unused backup codes.
    backup_codes_json: Mapped[list[str]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    # Highest TOTP time-step already accepted (replay guard).
    last_totp_counter: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0"), nullable=False
    )
    enabled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class UserOtpCode(Base):
    """An emailed one-time code (argon2-hashed)."""

    __tablename__ = "user_otp_codes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class AuthEphemeralToken(Base):
    """Short-lived opaque token (sha256-hashed): pre-auth, TOTP setup, OIDC 2FA."""

    __tablename__ = "auth_ephemeral_tokens"
    __table_args__ = (
        CheckConstraint("token_type IN ('pre_auth', 'totp_setup', 'oidc_2fa')", name="token_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    token_type: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
