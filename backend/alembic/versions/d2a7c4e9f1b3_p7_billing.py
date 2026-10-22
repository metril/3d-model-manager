"""p7 billing: cost_centers, ledger_entries, print_queue review/billing cols

P7 review/billing plan Task 1: ``cost_centers`` (a budget owned by exactly
one user or one group, CHECK-enforced), ``ledger_entries`` (an append-only
log of signed amounts, idempotent per ``(print_queue_id, kind)`` via
``uq_ledger_queue_kind``), and the new ``print_queue`` review/billing
columns (``cost_center_id``, ``review_state`` defaulting to ``none``,
``reviewed_by``/``reviewed_at``/``review_note``, ``reserved_cost``,
``hold_reason``).

Revision ID: d2a7c4e9f1b3
Revises: a2b3c4d5e6f7
Create Date: 2026-10-22 00:00:03.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2a7c4e9f1b3"
down_revision: str | Sequence[str] | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cost_centers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("budget_total", sa.Numeric(12, 2), nullable=True),
        sa.Column("budget_monthly", sa.Numeric(12, 2), nullable=True),
        sa.Column("balance", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_centers")),
        sa.CheckConstraint(
            "(owner_user_id IS NULL) != (group_id IS NULL)",
            name="ck_cost_centers_owner_xor_group",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_cost_centers_owner_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_cost_centers_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_cost_centers_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_cost_centers_group_id", "cost_centers", ["group_id"])
    op.create_index("ix_cost_centers_created_by_user_id", "cost_centers", ["created_by_user_id"])
    op.create_index(
        "uq_cost_centers_owner_user_id",
        "cost_centers",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("cost_center_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            _enum("ledger_kind", "deposit", "adjustment", "charge", "reservation", "release"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("print_queue_id", sa.BigInteger(), nullable=True),
        sa.Column("print_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.ForeignKeyConstraint(
            ["cost_center_id"],
            ["cost_centers.id"],
            name=op.f("fk_ledger_entries_cost_center_id_cost_centers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["print_queue_id"],
            ["print_queue.id"],
            name=op.f("fk_ledger_entries_print_queue_id_print_queue"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["print_id"],
            ["prints.id"],
            name=op.f("fk_ledger_entries_print_id_prints"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_ledger_entries_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_ledger_entries_cost_center_id", "ledger_entries", ["cost_center_id"])
    op.create_index("ix_ledger_entries_print_queue_id", "ledger_entries", ["print_queue_id"])
    op.create_index("ix_ledger_entries_print_id", "ledger_entries", ["print_id"])
    op.create_index(
        "ix_ledger_entries_created_by_user_id", "ledger_entries", ["created_by_user_id"]
    )
    op.create_index(
        "ix_ledger_entries_cost_center_id_created_at",
        "ledger_entries",
        ["cost_center_id", "created_at"],
    )
    op.create_index(
        "uq_ledger_queue_kind",
        "ledger_entries",
        ["print_queue_id", "kind"],
        unique=True,
        postgresql_where=sa.text("print_queue_id IS NOT NULL"),
    )

    op.add_column("print_queue", sa.Column("cost_center_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "print_queue",
        sa.Column(
            "review_state",
            _enum("review_state", "none", "pending", "approved", "rejected"),
            server_default="none",
            nullable=False,
        ),
    )
    op.add_column("print_queue", sa.Column("reviewed_by", sa.BigInteger(), nullable=True))
    op.add_column(
        "print_queue",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("print_queue", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column("print_queue", sa.Column("reserved_cost", sa.Numeric(12, 2), nullable=True))
    op.add_column("print_queue", sa.Column("hold_reason", sa.Text(), nullable=True))

    op.create_foreign_key(
        op.f("fk_print_queue_cost_center_id_cost_centers"),
        "print_queue",
        "cost_centers",
        ["cost_center_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_print_queue_reviewed_by_users"),
        "print_queue",
        "users",
        ["reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_print_queue_cost_center_id", "print_queue", ["cost_center_id"])
    op.create_index("ix_print_queue_review_state", "print_queue", ["review_state"])
    op.create_index("ix_print_queue_reviewed_by", "print_queue", ["reviewed_by"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_print_queue_reviewed_by", table_name="print_queue")
    op.drop_index("ix_print_queue_review_state", table_name="print_queue")
    op.drop_index("ix_print_queue_cost_center_id", table_name="print_queue")
    op.drop_constraint(op.f("fk_print_queue_reviewed_by_users"), "print_queue", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_print_queue_cost_center_id_cost_centers"), "print_queue", type_="foreignkey"
    )
    op.drop_column("print_queue", "hold_reason")
    op.drop_column("print_queue", "reserved_cost")
    op.drop_column("print_queue", "review_note")
    op.drop_column("print_queue", "reviewed_at")
    op.drop_column("print_queue", "reviewed_by")
    op.drop_column("print_queue", "review_state")
    op.drop_column("print_queue", "cost_center_id")

    op.drop_index("uq_ledger_queue_kind", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_cost_center_id_created_at", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_created_by_user_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_print_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_print_queue_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_cost_center_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")

    op.drop_index("uq_cost_centers_owner_user_id", table_name="cost_centers")
    op.drop_index("ix_cost_centers_created_by_user_id", table_name="cost_centers")
    op.drop_index("ix_cost_centers_group_id", table_name="cost_centers")
    op.drop_table("cost_centers")
