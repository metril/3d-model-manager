"""Q7 operations tables: maintenance tasks/logs, smart plugs, backup runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Identity, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MaintenanceTask(Base):
    __tablename__ = "maintenance_tasks"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    printer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    interval_hours: Mapped[float | None] = mapped_column(Float)
    interval_days: Mapped[int | None] = mapped_column(Integer)
    last_done_at: Mapped[datetime | None]
    # Archive print hours at the last completion (baseline for interval_hours).
    last_done_hours: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MaintenanceLog(Base):
    __tablename__ = "maintenance_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("maintenance_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    done_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    hours_at: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )


class SmartPlug(Base):
    __tablename__ = "smart_plugs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    printer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    config_enc: Mapped[str] = mapped_column(Text, nullable=False)
    auto_off_after_min: Mapped[int | None] = mapped_column(Integer)
    auto_on_before_print: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # T1: power the plug off when an AMS drying cycle ends (printer idle).
    auto_off_after_drying: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    last_state: Mapped[str | None] = mapped_column(Text)
    last_power_w: Mapped[float | None] = mapped_column(Float)
    last_polled_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BackupRun(Base):
    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # manual | scheduled
    status: Mapped[str] = mapped_column(Text, nullable=False)  # running | ok | failed
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    location: Mapped[str | None] = mapped_column(Text)
    git_pushed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    error: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None]
