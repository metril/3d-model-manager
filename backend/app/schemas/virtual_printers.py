"""Schemas for ``/api/virtual-printers`` (Q11)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import VirtualPrinterMode


class VirtualPrinterIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    model: str = "N1"
    mode: VirtualPrinterMode = VirtualPrinterMode.QUEUE
    target_printer_id: int | None = None
    enabled: bool = True


class VirtualPrinterPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = None
    mode: VirtualPrinterMode | None = None
    target_printer_id: int | None = None
    enabled: bool | None = None
    regenerate_access_code: bool = False


class VirtualPrinterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    model: str
    serial: str
    access_code: str
    mode: VirtualPrinterMode
    target_printer_id: int | None
    enabled: bool
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
