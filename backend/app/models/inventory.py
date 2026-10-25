"""Filament inventory domain: physical spools, their AMS slot assignment,
and the usage ledger that debits grams against them (P5 spool tray plan).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, str_enum
from app.models.enums import SpoolUsageSource


class Spool(Base):
    """A physical filament spool a user has registered (P5 ``spools``)."""

    __tablename__ = "spools"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    material_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("materials.id", ondelete="SET NULL"), nullable=True, index=True
    )
    material_type: Mapped[str] = mapped_column(String, nullable=False)
    brand: Mapped[str | None] = mapped_column(String)
    # "#RRGGBB"
    color_hex: Mapped[str | None] = mapped_column(String)
    color_name: Mapped[str | None] = mapped_column(String)
    label_weight_g: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("1000")
    )
    empty_weight_g: Mapped[float | None] = mapped_column(Float)
    weight_used_g: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    cost_per_kg: Mapped[float | None] = mapped_column(Float)
    location_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("storage_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    low_stock_g: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("100"))
    tray_uuid: Mapped[str | None] = mapped_column(String, unique=True)
    # Linked Spoolman spool id (Q9); NULL = not linked.
    spoolman_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    archived_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    location_ref: Mapped["StorageLocation | None"] = relationship(lazy="joined")

    @property
    def location(self) -> str | None:
        """Read-only storage-location name (compat with the old text column)."""
        return self.location_ref.name if self.location_ref is not None else None


class SpoolSlot(Base):
    """Which spool currently sits in a printer's AMS slot (P5
    ``spool_slots``). A spool occupies at most one slot at a time.
    """

    __tablename__ = "spool_slots"
    __table_args__ = (
        UniqueConstraint(
            "printer_id", "ams_unit", "slot", name="uq_spool_slots_printer_id_ams_unit_slot"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    printer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False
    )
    ams_unit: Mapped[int] = mapped_column(Integer, nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    spool_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("spools.id", ondelete="CASCADE"), nullable=False, unique=True
    )


class SpoolUsage(Base):
    """A ledger entry of filament consumed from a spool by a print (P5
    ``spool_usage``).
    """

    __tablename__ = "spool_usage"
    __table_args__ = (Index("ix_spool_usage_print_job_id", "print_job_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    spool_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("spools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    print_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("print_jobs.id", ondelete="SET NULL"), nullable=True
    )
    grams: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[SpoolUsageSource] = mapped_column(
        str_enum(SpoolUsageSource, "spool_usage_source"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class SpoolmanSyncState(Base):
    """Per-spool Spoolman sync bookkeeping (Q9). Only written when the
    Spoolman mode is not ``off``."""

    __tablename__ = "spoolman_sync_state"

    spool_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("spools.id", ondelete="CASCADE"), primary_key=True
    )
    last_pushed_at: Mapped[datetime | None]
    last_pulled_at: Mapped[datetime | None]
    last_pulled_weight: Mapped[float | None] = mapped_column(Float)
    last_error: Mapped[str | None] = mapped_column(String)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class SpoolmanPush(Base):
    """Grams already pushed to Spoolman per (job, spool), so an archive
    redelivery only sends the delta (Q9)."""

    __tablename__ = "spoolman_pushes"

    print_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("print_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    spool_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("spools.id", ondelete="CASCADE"), primary_key=True
    )
    grams: Mapped[float] = mapped_column(Float, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Supplier(Base):
    """A filament vendor (T5). ``name_key`` = lower(trim(name)), unique."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    name_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    website: Mapped[str | None] = mapped_column(String)
    customer_number: Mapped[str | None] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class SpoolSupplier(Base):
    __tablename__ = "spool_suppliers"
    __table_args__ = (
        UniqueConstraint("spool_id", "supplier_id", name="uq_spool_suppliers_spool_id_supplier_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    spool_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("spools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    article_number: Mapped[str | None] = mapped_column(String)
    quoted_price_per_kg: Mapped[float | None] = mapped_column(Numeric(10, 2))
    is_purchase_source: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class StorageLocation(Base):
    __tablename__ = "storage_locations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    identifier: Mapped[str | None] = mapped_column(String)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class LocationHaSensor(Base):
    """A Home Assistant entity attached to a storage location (T5)."""

    __tablename__ = "location_ha_sensors"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    location_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("storage_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'other'"))
    device_class: Mapped[str | None] = mapped_column(String)
    unit: Mapped[str | None] = mapped_column(String)
    alert_above: Mapped[float | None] = mapped_column(Numeric)
    alert_below: Mapped[float | None] = mapped_column(Numeric)
    notify_on_alert: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    show_on_card: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_state: Mapped[str | None] = mapped_column(String)
    last_changed_at: Mapped[datetime | None]
    alerting: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class ColorCatalogEntry(Base):
    __tablename__ = "color_catalog"
    __table_args__ = (
        Index(
            "uq_color_catalog_mfr_name_material",
            "manufacturer",
            "color_name",
            text("coalesce(material, '')"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    manufacturer: Mapped[str] = mapped_column(String, nullable=False)
    color_name: Mapped[str] = mapped_column(String, nullable=False)
    hex: Mapped[str] = mapped_column(String, nullable=False)
    material: Mapped[str | None] = mapped_column(String)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    extra_colors: Mapped[list | None] = mapped_column(JSONB)
    effect_type: Mapped[str | None] = mapped_column(String)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
