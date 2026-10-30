from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QuietHours(BaseModel):
    start: str = Field(pattern=r"^\d{1,2}:\d{2}$")
    end: str = Field(pattern=r"^\d{1,2}:\d{2}$")
    tz: str = "UTC"


class NotifySettingsOut(BaseModel):
    hms_renotify_clear_s: int
    hms_renotify_after_s: int


class NotifySettingsPatch(BaseModel):
    hms_renotify_clear_s: int | None = Field(default=None, ge=0, le=86400)
    hms_renotify_after_s: int | None = Field(default=None, ge=0, le=604800)


class ChannelIn(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, Any] = {}
    enabled: bool = True
    events: list[str] = []
    quiet_hours: QuietHours | None = None
    digest_cron: str | None = None
    attach_photo: bool = False


class ChannelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    events: list[str] | None = None
    quiet_hours: QuietHours | None = None
    digest_cron: str | None = None
    attach_photo: bool | None = None


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    name: str
    config: dict[str, Any]
    enabled: bool
    events: list[str]
    quiet_hours: dict | None
    digest_cron: str | None
    attach_photo: bool
    created_at: datetime
    updated_at: datetime


class TestResult(BaseModel):
    ok: bool
    error: str | None = None


class TemplateIn(BaseModel):
    event: str
    title_tpl: str = Field(max_length=2000)
    body_tpl: str = Field(max_length=8000)


class TemplatePatch(BaseModel):
    title_tpl: str | None = Field(default=None, max_length=2000)
    body_tpl: str | None = Field(default=None, max_length=8000)


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event: str
    title_tpl: str
    body_tpl: str


class PreviewOut(BaseModel):
    title: str
    body: str
    error: str | None = None


class LogOut(BaseModel):
    id: int
    channel_id: int | None
    channel_name: str | None
    event: str
    printer_id: int | None
    status: str
    error: str | None
    sent_at: datetime


class LogPage(BaseModel):
    items: list[LogOut]
    total: int
