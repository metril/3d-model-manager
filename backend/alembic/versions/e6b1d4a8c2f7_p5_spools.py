"""p5 spools: spools, spool_slots, spool_usage, print_jobs.tray_snapshot

P5 spool tray plan Task 1: ``spools`` (one row per physical filament spool),
``spool_slots`` (which spool sits in which printer AMS slot right now --
unique per ``(printer_id, ams_unit, slot)`` and per ``spool_id``, so a spool
occupies at most one slot), and ``spool_usage`` (a ledger of grams debited
from a spool by a print, source-tagged so a 3MF-parsed estimate, an
AMS-weight-delta inference, and a manual correction are distinguishable).
``print_jobs.tray_snapshot`` captures AMS tray state at job start/end for
reconciling usage after the fact.

Revision ID: e6b1d4a8c2f7
Revises: 142d0b67c477
Create Date: 2026-10-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6b1d4a8c2f7"
down_revision: str | Sequence[str] | None = "142d0b67c477"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "spools",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("material_id", sa.BigInteger(), nullable=True),
        sa.Column("material_type", sa.String(), nullable=False),
        sa.Column("brand", sa.String(), nullable=True),
        sa.Column("color_hex", sa.String(), nullable=True),
        sa.Column("color_name", sa.String(), nullable=True),
        sa.Column("label_weight_g", sa.Float(), server_default="1000", nullable=False),
        sa.Column("empty_weight_g", sa.Float(), nullable=True),
        sa.Column("weight_used_g", sa.Float(), server_default="0", nullable=False),
        sa.Column("cost_per_kg", sa.Float(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("low_stock_g", sa.Float(), server_default="100", nullable=False),
        sa.Column("tray_uuid", sa.String(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spools")),
        sa.UniqueConstraint("tray_uuid", name=op.f("uq_spools_tray_uuid")),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["materials.id"],
            name=op.f("fk_spools_material_id_materials"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_spools_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_spools_material_id", "spools", ["material_id"])
    op.create_index("ix_spools_created_by_user_id", "spools", ["created_by_user_id"])

    op.create_table(
        "spool_slots",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column("ams_unit", sa.Integer(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("spool_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spool_slots")),
        sa.UniqueConstraint("spool_id", name=op.f("uq_spool_slots_spool_id")),
        sa.UniqueConstraint(
            "printer_id",
            "ams_unit",
            "slot",
            name="uq_spool_slots_printer_id_ams_unit_slot",
        ),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_spool_slots_printer_id_printers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["spool_id"],
            ["spools.id"],
            name=op.f("fk_spool_slots_spool_id_spools"),
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "spool_usage",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("spool_id", sa.BigInteger(), nullable=False),
        sa.Column("print_job_id", sa.BigInteger(), nullable=True),
        sa.Column("grams", sa.Float(), nullable=False),
        sa.Column(
            "source",
            _enum("spool_usage_source", "3mf", "ams_delta", "manual"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spool_usage")),
        sa.ForeignKeyConstraint(
            ["spool_id"],
            ["spools.id"],
            name=op.f("fk_spool_usage_spool_id_spools"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["print_job_id"],
            ["print_jobs.id"],
            name=op.f("fk_spool_usage_print_job_id_print_jobs"),
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_spool_usage_spool_id", "spool_usage", ["spool_id"])
    op.create_index("ix_spool_usage_print_job_id", "spool_usage", ["print_job_id"])

    op.add_column("print_jobs", sa.Column("tray_snapshot", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("print_jobs", "tray_snapshot")

    op.drop_index("ix_spool_usage_print_job_id", table_name="spool_usage")
    op.drop_index("ix_spool_usage_spool_id", table_name="spool_usage")
    op.drop_table("spool_usage")

    op.drop_table("spool_slots")

    op.drop_index("ix_spools_created_by_user_id", table_name="spools")
    op.drop_index("ix_spools_material_id", table_name="spools")
    op.drop_table("spools")
