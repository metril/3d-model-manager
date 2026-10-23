"""q8b printer_sensor_samples

Revision ID: b8c3d5e7a1f2
Revises: a8e2d4c6b1f3
Create Date: 2026-10-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b8c3d5e7a1f2"
down_revision: str | Sequence[str] | None = "a8e2d4c6b1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "printer_sensor_samples",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("nozzle", sa.Float(), nullable=True),
        sa.Column("nozzle_target", sa.Float(), nullable=True),
        sa.Column("bed", sa.Float(), nullable=True),
        sa.Column("bed_target", sa.Float(), nullable=True),
        sa.Column("chamber", sa.Float(), nullable=True),
        sa.Column("fan_part", sa.BigInteger(), nullable=True),
        sa.Column("ams", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_printer_sensor_samples_printer_id_printers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_printer_sensor_samples_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_printer_sensor_samples")),
    )
    op.create_index(
        "ix_printer_sensor_samples_printer_id_ts", "printer_sensor_samples", ["printer_id", "ts"]
    )


def downgrade() -> None:
    op.drop_index("ix_printer_sensor_samples_printer_id_ts", table_name="printer_sensor_samples")
    op.drop_table("printer_sensor_samples")
