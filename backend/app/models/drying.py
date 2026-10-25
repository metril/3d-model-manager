"""T1: scheduled AMS drying runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Identity, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ScheduledDrying(Base):
    """A drying run to start now/later (``manual``) or created by the
    ambient / queue automation. ``status``: pending | waiting | running |
    done | failed | cancelled."""

    __tablename__ = "scheduled_dryings"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    printer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ams_id: Mapped[int] = mapped_column(Integer, nullable=False)
    temp: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    filament: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    rotate_tray: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    start_after: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    waiting_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
