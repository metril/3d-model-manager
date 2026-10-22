"""p8 home assistant: ha_links table, print_jobs energy snapshots

P8 Home Assistant plan Task 1: ``ha_links`` (linked HA entities feeding
dispatcher holds, print pauses and energy accounting, SPEC P8), the
``ha_role`` enum, and two new ``print_jobs`` columns
(``energy_start_kwh``/``energy_end_kwh``) that snapshot the HA energy
accumulator at job start/finish.

Revision ID: e8b3f1a6c2d4
Revises: d2a7c4e9f1b3
Create Date: 2026-10-22 00:00:04.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8b3f1a6c2d4"
down_revision: str | Sequence[str] | None = "d2a7c4e9f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ha_links",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=True),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column(
            "role",
            _enum("ha_role", "energy_kwh", "power_w", "door", "smoke", "temp", "humidity"),
            nullable=False,
        ),
        sa.Column("alert_condition", JSONB(), nullable=True),
        sa.Column("hold_queue", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("pause_print", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ha_links")),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_ha_links_printer_id_printers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_ha_links_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_ha_links_printer_id", "ha_links", ["printer_id"])
    op.create_index("ix_ha_links_created_by_user_id", "ha_links", ["created_by_user_id"])

    op.add_column("print_jobs", sa.Column("energy_start_kwh", sa.Float(), nullable=True))
    op.add_column("print_jobs", sa.Column("energy_end_kwh", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("print_jobs", "energy_end_kwh")
    op.drop_column("print_jobs", "energy_start_kwh")

    op.drop_index("ix_ha_links_created_by_user_id", table_name="ha_links")
    op.drop_index("ix_ha_links_printer_id", table_name="ha_links")
    op.drop_table("ha_links")
