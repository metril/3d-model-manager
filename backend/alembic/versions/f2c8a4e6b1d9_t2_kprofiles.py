"""t2 kprofiles: spool_k_profiles + kprofile_notes

Revision ID: f2c8a4e6b1d9
Revises: f4b2d8a6c1e3
Create Date: 2026-10-24 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2c8a4e6b1d9"
down_revision: str | Sequence[str] | None = "f4b2d8a6c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_by(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["created_by_user_id"],
        ["users.id"],
        name=op.f(f"fk_{table}_created_by_user_id_users"),
        ondelete="SET NULL",
    )


def upgrade() -> None:
    op.create_table(
        "spool_k_profiles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("spool_id", sa.BigInteger(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column("extruder", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("nozzle_diameter", sa.Numeric(3, 2), nullable=False),
        sa.Column("nozzle_type", sa.Text(), nullable=True),
        sa.Column("k_value", sa.Numeric(8, 6), nullable=False),
        sa.Column("n_coef", sa.Numeric(8, 6), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("cali_idx", sa.Integer(), nullable=True),
        sa.Column("setting_id", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["spool_id"],
            ["spools.id"],
            name=op.f("fk_spool_k_profiles_spool_id_spools"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_spool_k_profiles_printer_id_printers"),
            ondelete="CASCADE",
        ),
        _created_by("spool_k_profiles"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spool_k_profiles")),
        sa.UniqueConstraint(
            "spool_id",
            "printer_id",
            "extruder",
            "nozzle_diameter",
            name="uq_spool_k_profiles_spool_printer_extruder_nozzle",
        ),
    )
    op.create_index(op.f("ix_spool_k_profiles_spool_id"), "spool_k_profiles", ["spool_id"])
    op.create_index(op.f("ix_spool_k_profiles_printer_id"), "spool_k_profiles", ["printer_id"])
    op.create_table(
        "kprofile_notes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column("setting_id", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_kprofile_notes_printer_id_printers"),
            ondelete="CASCADE",
        ),
        _created_by("kprofile_notes"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kprofile_notes")),
        sa.UniqueConstraint("printer_id", "setting_id", name="uq_kprofile_notes_printer_setting"),
    )
    op.create_index(op.f("ix_kprofile_notes_printer_id"), "kprofile_notes", ["printer_id"])


def downgrade() -> None:
    op.drop_table("kprofile_notes")
    op.drop_table("spool_k_profiles")
