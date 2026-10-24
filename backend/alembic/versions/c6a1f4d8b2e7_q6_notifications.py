"""q6 notifications

Revision ID: c6a1f4d8b2e7
Revises: b7e1c4a9d3f2
Create Date: 2026-10-23 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c6a1f4d8b2e7"
down_revision: str | Sequence[str] | None = "b7e1c4a9d3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _creator() -> sa.Column:
    return sa.Column("created_by_user_id", sa.BigInteger(), nullable=True)


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("config_enc", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "events", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("quiet_hours", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("digest_cron", sa.String(), nullable=True),
        sa.Column("attach_photo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _creator(),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_channels")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_notification_channels_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_notification_channels_created_by_user_id",
        "notification_channels",
        ["created_by_user_id"],
    )
    op.create_table(
        "notification_templates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("title_tpl", sa.Text(), nullable=False),
        sa.Column("body_tpl", sa.Text(), nullable=False),
        _creator(),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_templates")),
        sa.UniqueConstraint("event", name=op.f("uq_notification_templates_event")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_notification_templates_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_notification_templates_created_by_user_id",
        "notification_templates",
        ["created_by_user_id"],
    )
    op.create_table(
        "notification_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _ts("sent_at"),
        _creator(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_log")),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name=op.f("fk_notification_log_channel_id_notification_channels"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_notification_log_printer_id_printers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_notification_log_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_notification_log_channel_id", "notification_log", ["channel_id"])
    op.create_index("ix_notification_log_printer_id", "notification_log", ["printer_id"])
    op.create_index(
        "ix_notification_log_created_by_user_id", "notification_log", ["created_by_user_id"]
    )


def downgrade() -> None:
    op.drop_table("notification_log")
    op.drop_table("notification_templates")
    op.drop_table("notification_channels")
