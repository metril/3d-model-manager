"""t6 two factor auth

Revision ID: d2e7a1c5b903
Revises: d7a3c5e9f1b4
Create Date: 2026-10-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d2e7a1c5b903"
down_revision: str | Sequence[str] | None = "d7a3c5e9f1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_by(table: str) -> tuple[sa.Column, sa.ForeignKeyConstraint]:
    return (
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f(f"fk_{table}_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_otp_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "oidc_providers",
        sa.Column("trusts_idp_mfa", sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    col, fk = _created_by("user_totp")
    op.create_table(
        "user_totp",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("secret_enc", sa.String(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "backup_codes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "last_totp_counter", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        col,
        _created_at(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_totp_user_id_users"),
            ondelete="CASCADE",
        ),
        fk,
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_totp")),
        sa.UniqueConstraint("user_id", name=op.f("uq_user_totp_user_id")),
    )

    col, fk = _created_by("user_otp_codes")
    op.create_table(
        "user_otp_codes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("used", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        col,
        _created_at(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_otp_codes_user_id_users"),
            ondelete="CASCADE",
        ),
        fk,
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_otp_codes")),
    )
    op.create_index(op.f("ix_user_otp_codes_user_id"), "user_otp_codes", ["user_id"])

    col, fk = _created_by("auth_ephemeral_tokens")
    op.create_table(
        "auth_ephemeral_tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("token_type", sa.String(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        col,
        _created_at(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_ephemeral_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        fk,
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_ephemeral_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_ephemeral_tokens_token_hash")),
        sa.CheckConstraint(
            "token_type IN ('pre_auth', 'totp_setup', 'oidc_2fa')",
            name=op.f("ck_auth_ephemeral_tokens_token_type"),
        ),
    )
    op.create_index(op.f("ix_auth_ephemeral_tokens_user_id"), "auth_ephemeral_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_ephemeral_tokens_user_id"), table_name="auth_ephemeral_tokens")
    op.drop_table("auth_ephemeral_tokens")
    op.drop_index(op.f("ix_user_otp_codes_user_id"), table_name="user_otp_codes")
    op.drop_table("user_otp_codes")
    op.drop_table("user_totp")
    op.drop_column("oidc_providers", "trusts_idp_mfa")
    op.drop_column("users", "email_otp_enabled")
