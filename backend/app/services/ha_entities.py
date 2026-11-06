"""Filtering of Home Assistant `/api/states` + `/api/services` payloads for
the web entity pickers (`GET /api/ha/entities`, `GET /api/ha/services`)."""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.schemas.housekeeping import HaEntityOut
from app.services.ha_client import HaClient, HaUnreachable
from app.services.ha_config import get_ha_config

_MAX_RESULTS = 2000


def csv(value: str | None) -> set[str]:
    return {v.strip().lower() for v in (value or "").split(",") if v.strip()}


async def fetch_ha(db: AsyncSession, settings: Settings, what: str) -> list[dict]:
    """One HA call (`states` or `services`) per request; 503 when HA is off
    or unreachable."""
    cfg = await get_ha_config(db, settings)
    if not cfg.ha_url or not cfg.ha_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "home assistant not configured")

    def _fetch() -> list[dict]:
        client = HaClient(cfg.ha_url, cfg.ha_token)
        try:
            return getattr(client, what)()
        finally:
            client.close()

    try:
        return await run_in_threadpool(_fetch)
    except HaUnreachable as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "could not reach home assistant"
        ) from e


def filter_entities(
    states: Iterable[dict],
    *,
    domains: set[str],
    device_classes: set[str],
    q: str | None,
) -> list[HaEntityOut]:
    needle = q.lower() if q else None
    out: list[HaEntityOut] = []
    for st in states:
        eid = st.get("entity_id", "")
        attrs = st.get("attributes") or {}
        if domains and eid.split(".", 1)[0].lower() not in domains:
            continue
        dc = attrs.get("device_class")
        if device_classes and str(dc or "").lower() not in device_classes:
            continue
        fname = attrs.get("friendly_name")
        if needle and needle not in eid.lower() and needle not in str(fname or "").lower():
            continue
        out.append(
            HaEntityOut(
                entity_id=eid,
                friendly_name=fname,
                state=st.get("state"),
                unit=attrs.get("unit_of_measurement"),
                device_class=dc,
            )
        )
    out.sort(key=lambda e: e.entity_id)
    return out[:_MAX_RESULTS]


def filter_services(raw: Iterable[dict], domain: str) -> list[str]:
    """Service names as `domain.service`, e.g. `notify.mobile_app_phone`."""
    want = domain.strip().lower()
    names: list[str] = []
    for entry in raw:
        if str(entry.get("domain", "")).lower() != want:
            continue
        names.extend(f"{want}.{name}" for name in (entry.get("services") or {}))
    return sorted(names)
