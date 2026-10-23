"""Printer sensor history (Q8b): one row per sampled snapshot."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Identity, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PrinterSensorSample(Base):
    __tablename__ = "printer_sensor_samples"
    __table_args__ = (Index("ix_printer_sensor_samples_printer_id_ts", "printer_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    printer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    nozzle: Mapped[float | None] = mapped_column(Float)
    nozzle_target: Mapped[float | None] = mapped_column(Float)
    bed: Mapped[float | None] = mapped_column(Float)
    bed_target: Mapped[float | None] = mapped_column(Float)
    chamber: Mapped[float | None] = mapped_column(Float)
    fan_part: Mapped[int | None] = mapped_column(BigInteger)
    # [{"id": int, "humidity": int|None, "temp": float|None}]
    ams: Mapped[list | None] = mapped_column(JSONB)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
