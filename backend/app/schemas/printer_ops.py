"""Q5 schemas: HMS, firmware, diagnostics, discovery."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HmsFaultOut(BaseModel):
    code: str
    full_code: str
    kind: Literal["hms", "print_error"]
    severity: str
    severity_level: int
    module: str
    description: str | None = None
    wiki_url: str
    actions: list[str] = Field(default_factory=list)


class HmsActionIn(BaseModel):
    print_error: str = Field(min_length=8, max_length=16, pattern=r"^[0-9A-Fa-f]+$")
    action: str = Field(min_length=1, max_length=64)
    job_id: str | None = Field(default=None, max_length=64)


class FirmwareInfoOut(BaseModel):
    model_supported: bool
    current: str | None = None
    latest: str | None = None
    release_notes: str | None = None
    release_time: str | None = None
    checked_at: str | None = None
    download_available: bool = False
    update_available: bool = False
    upload: dict | None = None  # last SD-stage status: {status, version, ...}


class DiagnosticIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    serial: str = Field(min_length=1, max_length=64)
    access_code: str = Field(min_length=1, max_length=64)
    model: str | None = None


class DiagnosticCheckOut(BaseModel):
    id: str
    status: Literal["ok", "fail", "skip"]
    hint: str
    detail: str | None = None


class DiagnosticOut(BaseModel):
    ok: bool
    checks: list[DiagnosticCheckOut]


class DiscoverIn(BaseModel):
    cidr: str | None = Field(default=None, max_length=64)


class DiscoveredOut(BaseModel):
    host: str
    serial: str
    model_hint: str | None = None
    source: Literal["ssdp", "sweep"]
