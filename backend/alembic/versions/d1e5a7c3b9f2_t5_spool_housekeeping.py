"""t5 spool housekeeping: suppliers, storage locations, HA sensors, colour catalog

Revision ID: d1e5a7c3b9f2
Revises: d3a7c1e5f9b2
Create Date: 2026-10-24 00:00:00.000000

"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d1e5a7c3b9f2"
down_revision: str | Sequence[str] | None = "d3a7c1e5f9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CATALOG = Path(__file__).resolve().parents[2] / "app" / "data" / "color_catalog.json"


def _audit_cols() -> list[sa.Column]:
    return [
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _user_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["created_by_user_id"],
        ["users.id"],
        name=op.f(f"fk_{table}_created_by_user_id_users"),
        ondelete="SET NULL",
    )


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("name_key", sa.String(), nullable=False),
        sa.Column("website", sa.String(), nullable=True),
        sa.Column("customer_number", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_audit_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppliers")),
        sa.UniqueConstraint("name_key", name=op.f("uq_suppliers_name_key")),
        _user_fk("suppliers"),
    )
    op.create_index("ix_suppliers_created_by_user_id", "suppliers", ["created_by_user_id"])

    op.create_table(
        "storage_locations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("name_key", sa.String(), nullable=False),
        sa.Column("identifier", sa.String(), nullable=True),
        *_audit_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_locations")),
        sa.UniqueConstraint("name", name=op.f("uq_storage_locations_name")),
        sa.UniqueConstraint("name_key", name=op.f("uq_storage_locations_name_key")),
        _user_fk("storage_locations"),
    )
    op.create_index(
        "ix_storage_locations_created_by_user_id", "storage_locations", ["created_by_user_id"]
    )

    op.create_table(
        "spool_suppliers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("spool_id", sa.BigInteger(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("article_number", sa.String(), nullable=True),
        sa.Column("quoted_price_per_kg", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "is_purchase_source", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        *_audit_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spool_suppliers")),
        sa.UniqueConstraint(
            "spool_id", "supplier_id", name="uq_spool_suppliers_spool_id_supplier_id"
        ),
        sa.ForeignKeyConstraint(
            ["spool_id"],
            ["spools.id"],
            name=op.f("fk_spool_suppliers_spool_id_spools"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            name=op.f("fk_spool_suppliers_supplier_id_suppliers"),
            ondelete="CASCADE",
        ),
        _user_fk("spool_suppliers"),
    )
    op.create_index("ix_spool_suppliers_spool_id", "spool_suppliers", ["spool_id"])
    op.create_index("ix_spool_suppliers_supplier_id", "spool_suppliers", ["supplier_id"])
    op.create_index(
        "ix_spool_suppliers_created_by_user_id", "spool_suppliers", ["created_by_user_id"]
    )

    op.create_table(
        "location_ha_sensors",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("location_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), server_default=sa.text("'other'"), nullable=False),
        sa.Column("device_class", sa.String(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("alert_above", sa.Numeric(), nullable=True),
        sa.Column("alert_below", sa.Numeric(), nullable=True),
        sa.Column("notify_on_alert", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("show_on_card", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_state", sa.String(), nullable=True),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alerting", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_audit_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_location_ha_sensors")),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["storage_locations.id"],
            name=op.f("fk_location_ha_sensors_location_id_storage_locations"),
            ondelete="CASCADE",
        ),
        _user_fk("location_ha_sensors"),
    )
    op.create_index("ix_location_ha_sensors_location_id", "location_ha_sensors", ["location_id"])
    op.create_index(
        "ix_location_ha_sensors_created_by_user_id", "location_ha_sensors", ["created_by_user_id"]
    )

    op.create_table(
        "color_catalog",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("manufacturer", sa.String(), nullable=False),
        sa.Column("color_name", sa.String(), nullable=False),
        sa.Column("hex", sa.String(), nullable=False),
        sa.Column("material", sa.String(), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("extra_colors", postgresql.JSONB(), nullable=True),
        sa.Column("effect_type", sa.String(), nullable=True),
        *_audit_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_color_catalog")),
        _user_fk("color_catalog"),
    )
    op.create_index("ix_color_catalog_created_by_user_id", "color_catalog", ["created_by_user_id"])
    # coalesce so NULL-material rows cannot duplicate
    op.create_index(
        "uq_color_catalog_mfr_name_material",
        "color_catalog",
        ["manufacturer", "color_name", sa.text("coalesce(material, '')")],
        unique=True,
    )

    bind = op.get_bind()
    rows = json.loads(_CATALOG.read_text())
    bind.execute(
        sa.text(
            "INSERT INTO color_catalog (manufacturer, color_name, hex, material, is_default) "
            "VALUES (:manufacturer, :color_name, :hex, :material, true)"
        ),
        rows,
    )

    # spools.location (free text) -> storage_locations + spools.location_id
    op.add_column("spools", sa.Column("location_id", sa.BigInteger(), nullable=True))
    op.execute(
        "INSERT INTO storage_locations (name, name_key) "
        "SELECT DISTINCT ON (lower(btrim(location))) btrim(location), lower(btrim(location)) "
        "FROM spools WHERE location IS NOT NULL AND btrim(location) <> '' "
        "ORDER BY lower(btrim(location)), id"
    )
    op.execute(
        "UPDATE spools s SET location_id = l.id FROM storage_locations l "
        "WHERE s.location IS NOT NULL AND lower(btrim(s.location)) = l.name_key"
    )
    op.create_foreign_key(
        op.f("fk_spools_location_id_storage_locations"),
        "spools",
        "storage_locations",
        ["location_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_spools_location_id", "spools", ["location_id"])
    op.drop_column("spools", "location")


def downgrade() -> None:
    op.add_column("spools", sa.Column("location", sa.String(), nullable=True))
    op.execute(
        "UPDATE spools s SET location = l.name FROM storage_locations l WHERE s.location_id = l.id"
    )
    op.drop_index("ix_spools_location_id", table_name="spools")
    op.drop_constraint(
        op.f("fk_spools_location_id_storage_locations"), "spools", type_="foreignkey"
    )
    op.drop_column("spools", "location_id")
    for t in (
        "color_catalog",
        "location_ha_sensors",
        "spool_suppliers",
        "storage_locations",
        "suppliers",
    ):
        op.drop_table(t)
