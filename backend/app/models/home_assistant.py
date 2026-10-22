"""Home Assistant integration links (P8): a linked HA entity feeding either
an alert condition (can hold the dispatcher and/or pause a running print)
or energy accounting (SPEC P8 ``ha_links``). See ``app.models.enums.HaRole``
for the role vocabulary and ``app.services.ha_config`` for the separate
``settings["home_assistant"]`` connection config this table doesn't carry.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Identity, Text, false, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum
from app.models.enums import HaRole


class HaLink(Base):
    """One linked HA entity (SPEC P8 ``ha_links``). ``printer_id`` NULL
    means the link applies to every printer (e.g. a shared door sensor).
    ``alert_condition`` (validated at the API layer, Task 7) is the
    ``{"op": ..., "value": ...}`` shape that decides when this link fires;
    it's unused for ``energy_kwh``/``power_w`` roles. ``hold_queue``/
    ``pause_print`` default to false -- a link can simply be a passive
    sensor feed with neither set.
    """

    __tablename__ = "ha_links"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    printer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[HaRole] = mapped_column(str_enum(HaRole, "ha_role"), nullable=False)
    alert_condition: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    hold_queue: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    pause_print: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
