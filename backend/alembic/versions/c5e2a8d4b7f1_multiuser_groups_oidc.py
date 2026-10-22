"""multi-user: roles, groups, printer access, oidc providers, ownership

P1: users gain email/display_name/is_active/role/oidc identity/last_login_at
and a nullable password_hash (OIDC-only accounts); new groups, group_members,
printer_access, oidc_providers tables; api_tokens.user_id (NOT NULL) and
created_by_user_id on the nine owned tables. Every pre-existing user becomes
an admin and the lowest-id user adopts all existing tokens and rows.

Revision ID: c5e2a8d4b7f1
Revises: f3a1c9d2e8b7
Create Date: 2026-10-21 00:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c5e2a8d4b7f1"
down_revision = "f3a1c9d2e8b7"
branch_labels = None
depends_on = None

_OWNED = (
    "models",
    "revisions",
    "files",
    "notes",
    "followed_collections",
    "imports",
    "print_queue",
    "prints",
    "print_jobs",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.add_column("users", sa.Column("email", postgresql.CITEXT(), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.Enum(
                "admin",
                "member",
                "viewer",
                name="user_role",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="member",
            nullable=False,
        ),
    )
    op.add_column("users", sa.Column("oidc_issuer", sa.String(), nullable=True))
    op.add_column("users", sa.Column("oidc_subject", sa.String(), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_unique_constraint("uq_users_oidc_identity", "users", ["oidc_issuer", "oidc_subject"])
    op.alter_column("users", "password_hash", existing_type=sa.String(), nullable=True)

    op.create_table(
        "groups",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "permissions",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_groups"),
        sa.UniqueConstraint("name", name="uq_groups_name"),
    )
    op.create_table(
        "group_members",
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "local",
                "oidc",
                name="membership_source",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="local",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["groups.id"], name="fk_group_members_group_id_groups", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_group_members_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("group_id", "user_id", name="pk_group_members"),
    )
    op.create_table(
        "printer_access",
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column("can_print", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("can_control", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("requires_review", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name="fk_printer_access_group_id_groups",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name="fk_printer_access_printer_id_printers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "printer_id", name="pk_printer_access"),
    )
    op.create_table(
        "oidc_providers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("discovery_url", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_secret_enc", sa.String(), nullable=False),
        sa.Column(
            "scopes", sa.String(), server_default="openid profile email groups", nullable=False
        ),
        sa.Column("groups_claim", sa.String(), server_default="groups", nullable=False),
        sa.Column(
            "group_mapping",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("admin_group", sa.String(), nullable=True),
        sa.Column("auto_create_users", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("link_by_email", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_oidc_providers"),
        sa.UniqueConstraint("slug", name="uq_oidc_providers_slug"),
    )

    # Ownership data step: existing users become admins; the lowest-id user
    # adopts everything that predates ownership.
    op.execute("UPDATE users SET role = 'admin'")
    op.add_column("api_tokens", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.execute("UPDATE api_tokens SET user_id = (SELECT min(id) FROM users)")
    op.execute("DELETE FROM api_tokens WHERE user_id IS NULL")
    op.alter_column("api_tokens", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_api_tokens_user_id_users",
        "api_tokens",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])

    for table in _OWNED:
        op.add_column(table, sa.Column("created_by_user_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_created_by_user_id_users",
            table,
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.execute(f"UPDATE {table} SET created_by_user_id = (SELECT min(id) FROM users)")  # noqa: S608
        op.create_index(f"ix_{table}_created_by_user_id", table, ["created_by_user_id"])


def downgrade() -> None:
    for table in reversed(_OWNED):
        op.drop_index(f"ix_{table}_created_by_user_id", table_name=table)
        op.drop_constraint(f"fk_{table}_created_by_user_id_users", table, type_="foreignkey")
        op.drop_column(table, "created_by_user_id")

    op.drop_index("ix_api_tokens_user_id", table_name="api_tokens")
    op.drop_constraint("fk_api_tokens_user_id_users", "api_tokens", type_="foreignkey")
    op.drop_column("api_tokens", "user_id")

    op.drop_table("oidc_providers")
    op.drop_table("printer_access")
    op.drop_table("group_members")
    op.drop_table("groups")

    op.execute("UPDATE users SET password_hash = '!' WHERE password_hash IS NULL")
    op.alter_column("users", "password_hash", existing_type=sa.String(), nullable=False)
    op.drop_constraint("uq_users_oidc_identity", "users", type_="unique")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    for col in (
        "last_login_at",
        "oidc_subject",
        "oidc_issuer",
        "role",
        "is_active",
        "display_name",
        "email",
    ):
        op.drop_column("users", col)
