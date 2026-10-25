"""V4 projects: a named build (status, due date, BOM) made of library models,
files/plates and purchased items. Progress is derived from archived runs."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # planning | active | on_hold | completed | archived
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'planning'"))
    due_date: Mapped[date | None] = mapped_column(Date)
    cover_model_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("models.id", ondelete="SET NULL")
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    is_template: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    completed_at: Mapped[datetime | None]
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectItem(Base):
    """A BOM line: ``kind='part'`` (a library model, optionally narrowed to a
    file and plate) or ``kind='purchased'`` (name + unit cost + link)."""

    __tablename__ = "project_items"
    __table_args__ = (Index("ix_project_items_project_id", "project_id", "position"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    name: Mapped[str | None] = mapped_column(Text)
    model_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("models.id", ondelete="SET NULL"), index=True
    )
    file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="SET NULL")
    )
    plate: Mapped[int | None] = mapped_column(Integer)
    # Parts: target print count. Purchased: quantity needed.
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Purchased only: how many are in hand.
    acquired: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 2))
    url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class ProjectEvent(Base):
    __tablename__ = "project_events"
    __table_args__ = (Index("ix_project_events_project_id", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class ProjectAttachment(Base):
    __tablename__ = "project_attachments"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    stored_name: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
