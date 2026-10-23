"""q7 ops: maintenance, smart plugs, backup runs, printers.maintenance_mode

Revision ID: b7e1c4a9d3f2
Revises: b6d1e8a3c5f2
Create Date: 2026-10-23 00:00:02.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e1c4a9d3f2"
down_revision: str | Sequence[str] | None = "b6d1e8a3c5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.func.now()


def _user_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["created_by_user_id"],
        ["users.id"],
        name=op.f(f"fk_{table}_created_by_user_id_users"),
        ondelete="SET NULL",
    )


def upgrade() -> None:
    op.add_column(
        "printers",
        sa.Column("maintenance_mode", sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    op.create_table(
        "maintenance_tasks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("interval_hours", sa.Float(), nullable=True),
        sa.Column("interval_days", sa.Integer(), nullable=True),
        sa.Column("last_done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_done_hours", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maintenance_tasks")),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_maintenance_tasks_printer_id_printers"),
            ondelete="CASCADE",
        ),
        _user_fk("maintenance_tasks"),
    )
    op.create_index("ix_maintenance_tasks_printer_id", "maintenance_tasks", ["printer_id"])
    op.create_index(
        "ix_maintenance_tasks_created_by_user_id", "maintenance_tasks", ["created_by_user_id"]
    )

    op.create_table(
        "maintenance_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("hours_at", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maintenance_logs")),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["maintenance_tasks.id"],
            name=op.f("fk_maintenance_logs_task_id_maintenance_tasks"),
            ondelete="CASCADE",
        ),
        _user_fk("maintenance_logs"),
    )
    op.create_index("ix_maintenance_logs_task_id", "maintenance_logs", ["task_id"])
    op.create_index(
        "ix_maintenance_logs_created_by_user_id", "maintenance_logs", ["created_by_user_id"]
    )

    op.create_table(
        "smart_plugs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("config_enc", sa.Text(), nullable=False),
        sa.Column("auto_off_after_min", sa.Integer(), nullable=True),
        sa.Column("auto_on_before_print", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_state", sa.Text(), nullable=True),
        sa.Column("last_power_w", sa.Float(), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_smart_plugs")),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_smart_plugs_printer_id_printers"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("printer_id", name=op.f("uq_smart_plugs_printer_id")),
        sa.CheckConstraint(
            "kind IN ('tasmota', 'ha', 'mqtt', 'rest')", name=op.f("ck_smart_plugs_kind")
        ),
        _user_fk("smart_plugs"),
    )
    op.create_index("ix_smart_plugs_created_by_user_id", "smart_plugs", ["created_by_user_id"])

    op.create_table(
        "backup_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("git_pushed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backup_runs")),
        _user_fk("backup_runs"),
    )
    op.create_index("ix_backup_runs_created_by_user_id", "backup_runs", ["created_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_backup_runs_created_by_user_id", table_name="backup_runs")
    op.drop_table("backup_runs")
    op.drop_index("ix_smart_plugs_created_by_user_id", table_name="smart_plugs")
    op.drop_table("smart_plugs")
    op.drop_index("ix_maintenance_logs_created_by_user_id", table_name="maintenance_logs")
    op.drop_index("ix_maintenance_logs_task_id", table_name="maintenance_logs")
    op.drop_table("maintenance_logs")
    op.drop_index("ix_maintenance_tasks_created_by_user_id", table_name="maintenance_tasks")
    op.drop_index("ix_maintenance_tasks_printer_id", table_name="maintenance_tasks")
    op.drop_table("maintenance_tasks")
    op.drop_column("printers", "maintenance_mode")
