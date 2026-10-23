from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

OpenMode = Literal["iframe", "newtab"]


def _check_url(value: str) -> str:
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    return value


class ExternalLinkIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2048)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int = 0
    open_mode: OpenMode = "iframe"

    _url = field_validator("url")(_check_url)


class ExternalLinkPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, max_length=2048)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None
    open_mode: OpenMode | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None else _check_url(value)


class ExternalLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    icon: str | None
    sort_order: int
    open_mode: OpenMode
    created_at: datetime
