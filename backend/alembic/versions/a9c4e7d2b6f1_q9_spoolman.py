"""q9 spoolman: spools.spoolman_id, spoolman_sync_state

Revision ID: a9c4e7d2b6f1
Revises: c5d8a1e7f3b9
Create Date: 2026-10-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c4e7d2b6f1"
down_revision: str | Sequence[str] | None = "c5d8a1e7f3b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("spools", sa.Column("spoolman_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(op.f("uq_spools_spoolman_id"), "spools", ["spoolman_id"])
    op.create_table(
        "spoolman_sync_state",
        sa.Column("spool_id", sa.BigInteger(), nullable=False),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pulled_weight", sa.Float(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("spool_id", name=op.f("pk_spoolman_sync_state")),
        sa.ForeignKeyConstraint(
            ["spool_id"],
            ["spools.id"],
            name=op.f("fk_spoolman_sync_state_spool_id_spools"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_spoolman_sync_state_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        op.f("ix_spoolman_sync_state_created_by_user_id"),
        "spoolman_sync_state",
        ["created_by_user_id"],
    )


def downgrade() -> None:
    op.drop_table("spoolman_sync_state")
    op.drop_constraint(op.f("uq_spools_spoolman_id"), "spools", type_="unique")
    op.drop_column("spools", "spoolman_id")
