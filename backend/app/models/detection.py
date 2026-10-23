"""Obico failure-detection events (Q10): threshold crossings only, not frames."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Identity, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FailureDetection(Base):
    __tablename__ = "failure_detections"
    __table_args__ = (
        Index("ix_failure_detections_printer_id_created_at", "printer_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    printer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False
    )
    print_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("print_jobs.id", ondelete="SET NULL"), nullable=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    smoothed: Mapped[float] = mapped_column(Float, nullable=False)
    # Redis key holding the JPEG of the triggering frame (7-day TTL); NULL if none.
    frame_blob_key: Mapped[str | None] = mapped_column(Text)
    # warn | none | notify | pause
    action: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
