"""q11 virtual printers

Revision ID: a7c3e9d1b5f2
Revises: c5d8a1e7f3b9
Create Date: 2026-10-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c3e9d1b5f2"
down_revision: str | Sequence[str] | None = "c5d8a1e7f3b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "virtual_printers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("serial", sa.String(), nullable=False),
        sa.Column("access_code_enc", sa.Text(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                "queue",
                "review",
                "archive",
                "proxy",
                name="virtual_printer_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("target_printer_id", sa.BigInteger(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_virtual_printers")),
        sa.UniqueConstraint("serial", name=op.f("uq_virtual_printers_serial")),
        sa.ForeignKeyConstraint(
            ["target_printer_id"],
            ["printers.id"],
            name=op.f("fk_virtual_printers_target_printer_id_printers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_virtual_printers_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_virtual_printers_target_printer_id", "virtual_printers", ["target_printer_id"]
    )
    op.create_index(
        "ix_virtual_printers_created_by_user_id", "virtual_printers", ["created_by_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_virtual_printers_created_by_user_id", table_name="virtual_printers")
    op.drop_index("ix_virtual_printers_target_printer_id", table_name="virtual_printers")
    op.drop_table("virtual_printers")
