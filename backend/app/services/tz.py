"""IANA time-zone query-param parsing shared by the calendar / stats buckets."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status


def parse_tz(name: str | None) -> ZoneInfo:
    """Validate an IANA zone name (default UTC); 422 on unknown names."""
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown time zone") from e
