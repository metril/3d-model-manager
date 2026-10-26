"""Beat: emit reorder / stock-break notifications for filament SKUs."""

from __future__ import annotations

from app.services import stock_forecast_db
from app.tasks import base
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.stock_forecast.check_alerts", soft_time_limit=120, time_limit=150)
def check_alerts() -> int:
    with base.sync_session() as s:
        return len(stock_forecast_db.check_alerts(s))
