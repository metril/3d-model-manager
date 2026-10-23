"""Q8b sensor history: ``record`` persists one snapshot printerd enqueued
(printerd never touches the DB for this -- it only enqueues); ``prune`` is the
daily retention sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.config import get_settings
from app.models.sensors import PrinterSensorSample
from app.services.app_config import get_app_config_sync
from app.tasks import base
from app.tasks.celery_app import celery_app

SAMPLE_INTERVAL_S = 60


def _ams(raw: object) -> list[dict]:
    out: list[dict] = []
    for u in raw if isinstance(raw, list) else []:
        if isinstance(u, dict) and u.get("id") is not None:
            out.append({"id": u["id"], "humidity": u.get("humidity"), "temp": u.get("temp")})
    return out


@celery_app.task(name="app.tasks.sensors.record")
def record(printer_id: int, snapshot: dict) -> int | None:
    from app.models.printing import Printer

    ts = snapshot.get("ts")
    with base.sync_session() as s:
        if s.get(Printer, printer_id) is None:
            return None
        row = PrinterSensorSample(
            printer_id=printer_id,
            ts=datetime.fromisoformat(ts) if ts else datetime.now(UTC),
            nozzle=snapshot.get("nozzle"),
            nozzle_target=snapshot.get("nozzle_target"),
            bed=snapshot.get("bed"),
            bed_target=snapshot.get("bed_target"),
            chamber=snapshot.get("chamber"),
            fan_part=snapshot.get("fan_part"),
            ams=_ams(snapshot.get("ams")),
        )
        s.add(row)
        s.commit()
        return row.id


@celery_app.task(name="app.tasks.sensors.prune")
def prune() -> int:
    with base.sync_session() as s:
        days = get_app_config_sync(s, get_settings()).sensor_history_days
        cutoff = datetime.now(UTC) - timedelta(days=days)
        res = s.execute(delete(PrinterSensorSample).where(PrinterSensorSample.ts < cutoff))
        s.commit()
        return res.rowcount or 0
