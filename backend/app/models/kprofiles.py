"""T2: stored per-spool K-profile (pressure advance) links and profile notes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SpoolKProfile(Base):
    """The K-profile a spool should print with on one printer/extruder/nozzle."""

    __tablename__ = "spool_k_profiles"
    __table_args__ = (
        UniqueConstraint(
            "spool_id",
            "printer_id",
            "extruder",
            "nozzle_diameter",
            name="uq_spool_k_profiles_spool_printer_extruder_nozzle",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    spool_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("spools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    printer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    extruder: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    nozzle_diameter: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    nozzle_type: Mapped[str | None] = mapped_column(Text)
    k_value: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    n_coef: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    name: Mapped[str | None] = mapped_column(Text)
    cali_idx: Mapped[int | None] = mapped_column(Integer)
    setting_id: Mapped[str | None] = mapped_column(String)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class KProfileNote(Base):
    """Free-text note on a printer's K-profile (keyed by its ``setting_id``)."""

    __tablename__ = "kprofile_notes"
    __table_args__ = (
        UniqueConstraint("printer_id", "setting_id", name="uq_kprofile_notes_printer_setting"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    printer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    setting_id: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
