"""Per-printer overlay tokens (Q4): grant read-only access to the public
``/overlay/{printer_id}`` page plus that printer's camera stream/snapshot
(for OBS browser sources). Only the sha256 of the token is stored."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Identity, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OverlayToken(Base):
    __tablename__ = "overlay_tokens"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    printer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
