"""Virtual printer (Q11): a fake LAN Bambu printer slicers can "print" to.
See ``app.virtual_printer`` for the service that serves the enabled row."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Identity, String, Text, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum
from app.models.enums import VirtualPrinterMode


class VirtualPrinter(Base):
    __tablename__ = "virtual_printers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Bambu SSDP model code (``N1`` = A1 mini, ``N2S`` = A1, ``C12`` = P1S, ...).
    model: Mapped[str] = mapped_column(String, nullable=False)
    serial: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    access_code_enc: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[VirtualPrinterMode] = mapped_column(
        str_enum(VirtualPrinterMode, "virtual_printer_mode"), nullable=False
    )
    target_printer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
