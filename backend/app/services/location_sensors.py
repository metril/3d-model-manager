"""Poll the Home Assistant entities attached to storage locations (T5):
refresh readings, evaluate alert thresholds, and notify once per transition
into the alerting state."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import LocationHaSensor, StorageLocation
from app.services.ha_client import HaClient, HaUnreachable
from app.services.ha_config import HaConfig
from app.services.notify import events

log = logging.getLogger(__name__)
_NO_VALUE = {"unknown", "unavailable", "", None}


def _parse_ts(v: object) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def evaluate(value: float, above: float | None, below: float | None) -> tuple[str, float] | None:
    """The breached ``(direction, threshold)``, or None when within range."""
    if above is not None and value > above:
        return "above", above
    if below is not None and value < below:
        return "below", below
    return None


def poll_once(
    session: Session,
    cfg: HaConfig,
    client_factory: Callable[[str, str], HaClient] = HaClient,
    emit: Callable[..., bool] = events.emit,
) -> dict[str, int] | None:
    """One poll tick. Returns counts, or None when HA is not configured."""
    if not cfg.ha_url or not cfg.ha_token:
        return None
    rows = session.execute(
        select(LocationHaSensor, StorageLocation.name)
        .join(StorageLocation, StorageLocation.id == LocationHaSensor.location_id)
        .order_by(LocationHaSensor.id)
    ).all()
    if not rows:
        return {"sensors": 0, "alerts": 0}
    client = client_factory(cfg.ha_url, cfg.ha_token)
    pending: list[dict] = []
    try:
        for sensor, loc_name in rows:
            try:
                st = client.state(sensor.entity_id)
            except HaUnreachable:
                break  # HA down: leave readings as-is, retry next tick
            if st is None:
                sensor.last_state = "unavailable"
                continue
            state = st.get("state")
            sensor.last_state = None if state is None else str(state)
            attrs = st.get("attributes") or {}
            sensor.last_changed_at = _parse_ts(st.get("last_changed")) or datetime.now(UTC)
            if sensor.unit is None and attrs.get("unit_of_measurement"):
                sensor.unit = attrs["unit_of_measurement"]
            if sensor.device_class is None and attrs.get("device_class"):
                sensor.device_class = attrs["device_class"]
            if state in _NO_VALUE:
                continue
            try:
                value = float(state)
            except (TypeError, ValueError):
                continue
            breach = evaluate(
                value,
                float(sensor.alert_above) if sensor.alert_above is not None else None,
                float(sensor.alert_below) if sensor.alert_below is not None else None,
            )
            now_alerting = breach is not None
            if now_alerting and not sensor.alerting and sensor.notify_on_alert:
                pending.append(
                    {
                        "location": loc_name,
                        "sensor": sensor.name,
                        "entity_id": sensor.entity_id,
                        "state": sensor.last_state,
                        "unit": sensor.unit or "",
                        "direction": breach[0],
                        "threshold": breach[1],
                    }
                )
            sensor.alerting = now_alerting
        session.commit()
    finally:
        client.close()
    for payload in pending:
        emit("location_sensor_alert", None, payload)
    return {"sensors": len(rows), "alerts": len(pending)}
