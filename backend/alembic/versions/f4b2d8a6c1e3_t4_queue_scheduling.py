"""t4 queue scheduling + batches

Revision ID: f4b2d8a6c1e3
Revises: d3a7c1e5f9b2
Create Date: 2026-10-24 12:00:00.000000

``queue_state`` is a VARCHAR + CHECK (``str_enum``), so the new ``skipped``
value is a drop-and-recreate of the CHECK constraint (no ``ALTER TYPE``).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4b2d8a6c1e3"
down_revision: str | Sequence[str] | None = "d3a7c1e5f9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "ck_print_queue_queue_state"
_OLD = ("waiting", "slicing", "ready", "dispatched", "held", "failed", "done")
_NEW = (*_OLD, "skipped")


def _in(values: tuple[str, ...]) -> str:
    return "state IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "print_batches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=True),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        sa.Column("recipe_id", sa.BigInteger(), nullable=True),
        sa.Column("printer_id", sa.BigInteger(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "plate_targets",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("cost_center_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            name=op.f("fk_print_batches_model_id_models"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["files.id"],
            name=op.f("fk_print_batches_file_id_files"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["print_recipes.id"],
            name=op.f("fk_print_batches_recipe_id_print_recipes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_print_batches_printer_id_printers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["cost_center_id"],
            ["cost_centers.id"],
            name=op.f("fk_print_batches_cost_center_id_cost_centers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_print_batches_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_print_batches")),
    )
    op.create_index(
        op.f("ix_print_batches_created_by_user_id"), "print_batches", ["created_by_user_id"]
    )

    op.add_column(
        "print_queue", sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True)
    )
    for col in ("manual_start", "require_previous_success", "auto_off_after"):
        op.add_column(
            "print_queue",
            sa.Column(col, sa.Boolean(), server_default=sa.false(), nullable=False),
        )
    op.add_column("print_queue", sa.Column("preheat_override", sa.Text(), nullable=True))
    op.add_column(
        "print_queue", sa.Column("preheat_chamber_target", sa.SmallInteger(), nullable=True)
    )
    op.add_column("print_queue", sa.Column("batch_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "print_queue", sa.Column("released_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("print_queue", sa.Column("released_by_user_id", sa.BigInteger(), nullable=True))
    op.add_column("print_queue", sa.Column("stage", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_print_queue_batch_id_print_batches"),
        "print_queue",
        "print_batches",
        ["batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_print_queue_released_by_user_id_users"),
        "print_queue",
        "users",
        ["released_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_print_queue_batch_id"), "print_queue", ["batch_id"])

    op.drop_constraint(op.f(_CK), "print_queue", type_="check")
    op.create_check_constraint(op.f(_CK), "print_queue", _in(_NEW))


def downgrade() -> None:
    op.execute("UPDATE print_queue SET state = 'held' WHERE state = 'skipped'")
    op.drop_constraint(op.f(_CK), "print_queue", type_="check")
    op.create_check_constraint(op.f(_CK), "print_queue", _in(_OLD))
    op.drop_index(op.f("ix_print_queue_batch_id"), table_name="print_queue")
    op.drop_constraint(
        op.f("fk_print_queue_released_by_user_id_users"), "print_queue", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_print_queue_batch_id_print_batches"), "print_queue", type_="foreignkey"
    )
    for col in (
        "stage",
        "released_by_user_id",
        "released_at",
        "batch_id",
        "preheat_chamber_target",
        "preheat_override",
        "auto_off_after",
        "require_previous_success",
        "manual_start",
        "scheduled_at",
    ):
        op.drop_column("print_queue", col)
    op.drop_index(op.f("ix_print_batches_created_by_user_id"), table_name="print_batches")
    op.drop_table("print_batches")
