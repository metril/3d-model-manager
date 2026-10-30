"""Electricity price sampled from a Home Assistant sensor (print start/end)."""

from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from app.config import Settings
from app.services.app_config import get_app_config_sync
from app.services.ha_client import HaClient, HaUnreachable
from app.services.ha_config import get_ha_config_sync

logger = logging.getLogger(__name__)

_warned = False
CACHE_TTL_S = 60.0
_cache: dict[str, tuple[float, float]] = {}  # entity -> (monotonic ts, price)


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        logger.warning("%s; using the fixed energy price", msg)


def sample_energy_price(
    session: Session, settings: Settings, client: HaClient | None = None
) -> float | None:
    """Current price from the configured HA entity, or ``None`` when the source
    is fixed or the sensor is missing/unavailable/non-numeric (logged once)."""
    cfg = get_app_config_sync(session, settings)
    entity = cfg.energy_price_ha_entity.strip()
    if cfg.energy_price_source != "homeassistant" or not entity:
        return None
    hit = _cache.get(entity)
    if hit is not None and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    owns = client is None
    try:
        if client is None:
            ha = get_ha_config_sync(session, settings)
            if not ha.ha_url or not ha.ha_token:
                _warn_once("home assistant is not configured")
                return None
            client = HaClient(ha.ha_url, ha.ha_token)
        try:
            state = client.state(entity)
        except HaUnreachable:
            _warn_once("home assistant is unreachable")
            return None
        finally:
            if owns:
                client.close()
    except Exception:
        logger.exception("energy price sampling failed")
        return None
    try:
        price = float((state or {}).get("state"))
    except (TypeError, ValueError):
        _warn_once(f"energy price sensor {entity} is unavailable")
        return None
    if price != price or price < 0:
        _warn_once(f"energy price sensor {entity} is unavailable")
        return None
    _cache[entity] = (time.monotonic(), price)
    return price
