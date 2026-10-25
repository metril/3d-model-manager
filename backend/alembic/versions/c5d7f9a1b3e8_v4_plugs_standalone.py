"""v4 standalone smart plugs + power-off-when-drained

Revision ID: c5d7f9a1b3e8
Revises: a4c6e8b1d3f5
Create Date: 2026-10-25 12:00:00.000000

Plugs become standalone (name, optional printer link, many per printer);
existing rows keep their printer link and gain ``auto_off`` from their old
``auto_off_after_min``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5d7f9a1b3e8"
down_revision: str | Sequence[str] | None = "a4c6e8b1d3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_T = "smart_plugs"


def _false() -> sa.sql.elements.ColumnElement:
    return sa.false()


def upgrade() -> None:
    op.add_column(_T, sa.Column("name", sa.Text(), server_default="", nullable=False))
    op.add_column(
        _T,
        sa.Column("controls_printer_power", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(_T, sa.Column("auto_off", sa.Boolean(), server_default=_false(), nullable=False))
    op.add_column(_T, sa.Column("off_delay_mode", sa.Text(), server_default="time", nullable=False))
    op.add_column(
        _T, sa.Column("off_temp_threshold", sa.Integer(), server_default="40", nullable=False)
    )
    op.add_column(
        _T, sa.Column("drying_off_delay_min", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        _T, sa.Column("power_alert_enabled", sa.Boolean(), server_default=_false(), nullable=False)
    )
    op.add_column(_T, sa.Column("power_alert_high_w", sa.Float(), nullable=True))
    op.add_column(_T, sa.Column("power_alert_low_w", sa.Float(), nullable=True))
    op.add_column(_T, sa.Column("power_alert_last_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        _T, sa.Column("schedule_enabled", sa.Boolean(), server_default=_false(), nullable=False)
    )
    op.add_column(_T, sa.Column("schedule_on_time", sa.Text(), nullable=True))
    op.add_column(_T, sa.Column("schedule_off_time", sa.Text(), nullable=True))
    op.add_column(_T, sa.Column("schedule_tz", sa.Text(), server_default="UTC", nullable=False))
    op.add_column(
        _T, sa.Column("show_in_switchbar", sa.Boolean(), server_default=_false(), nullable=False)
    )
    op.add_column(_T, sa.Column("energy_kwh", sa.Float(), server_default="0", nullable=False))
    op.execute(
        "UPDATE smart_plugs SET auto_off = (auto_off_after_min IS NOT NULL), "
        "name = 'Plug ' || id"
    )
    op.drop_constraint("uq_smart_plugs_printer_id", _T, type_="unique")
    op.drop_constraint("fk_smart_plugs_printer_id_printers", _T, type_="foreignkey")
    op.alter_column(_T, "printer_id", nullable=True)
    op.create_foreign_key(
        "fk_smart_plugs_printer_id_printers",
        _T,
        "printers",
        ["printer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_smart_plugs_printer_id", _T, ["printer_id"])
    op.add_column(
        "printers",
        sa.Column("power_off_when_drained", sa.Boolean(), server_default=_false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("printers", "power_off_when_drained")
    op.drop_index("ix_smart_plugs_printer_id", table_name=_T)
    # Standalone plugs have no printer; several plugs per printer collapse to one.
    op.execute("DELETE FROM smart_plugs WHERE printer_id IS NULL")
    op.execute(
        "DELETE FROM smart_plugs a USING smart_plugs b "
        "WHERE a.printer_id = b.printer_id AND a.id > b.id"
    )
    op.drop_constraint("fk_smart_plugs_printer_id_printers", _T, type_="foreignkey")
    op.alter_column(_T, "printer_id", nullable=False)
    op.create_foreign_key(
        "fk_smart_plugs_printer_id_printers",
        _T,
        "printers",
        ["printer_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint("uq_smart_plugs_printer_id", _T, ["printer_id"])
    for c in (
        "energy_kwh",
        "show_in_switchbar",
        "schedule_tz",
        "schedule_off_time",
        "schedule_on_time",
        "schedule_enabled",
        "power_alert_last_at",
        "power_alert_low_w",
        "power_alert_high_w",
        "power_alert_enabled",
        "drying_off_delay_min",
        "off_temp_threshold",
        "off_delay_mode",
        "auto_off",
        "controls_printer_power",
        "name",
    ):
        op.drop_column(_T, c)
