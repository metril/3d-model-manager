"""Q5 firmware tasks: daily latest-version refresh (beat) and the SD-card
stage upload. The upload NEVER triggers the flash -- the user does that on
the printer. (Audit-table entries land with Q2; the task log is the record.)"""

from __future__ import annotations

import json
import logging

import redis as redis_sync
from sqlalchemy import select

from app.config import get_settings
from app.models import Printer, Setting
from app.printers.connection import connection_from_printer
from app.printers.registry import build_adapter
from app.services import firmware
from app.services.printer_state import read_state_sync
from app.tasks import base
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)
CHECK_INTERVAL_S = 24 * 3600


@celery_app.task(name="app.tasks.firmware.check_firmware", soft_time_limit=300, time_limit=330)
def check_firmware() -> dict:
    with base.sync_session() as s, firmware.make_client() as client:
        models = list(s.scalars(select(Printer.model)))
        cache = firmware.refresh_cache(s, models, client)
    return {"models": sorted(cache)}


def _set_status(r, printer_id: int, **fields) -> None:
    r.set(firmware.upload_status_key(printer_id), json.dumps(fields), ex=24 * 3600)


@celery_app.task(name="app.tasks.firmware.upload_firmware", soft_time_limit=1800, time_limit=1900)
def upload_firmware(printer_id: int, actor: str | None = None) -> dict:
    settings = get_settings()
    r = redis_sync.Redis.from_url(settings.redis_url)
    try:
        with base.sync_session() as s:
            printer = s.get(Printer, printer_id)
            if printer is None:
                return {"status": "error", "error": "printer not found"}
            conn = connection_from_printer(settings, printer)
            row = s.get(Setting, firmware.CACHE_KEY)
            cached = row.value if row else None
        key = firmware.api_key_for(conn.model)
        info = (cached or {}).get(key or "")
        current = ((read_state_sync(r, printer_id) or {}).get("firmware") or {}).get("ota")
        if not info or not info.get("download_url"):
            _set_status(r, printer_id, status="error", error="no firmware image available")
            return {"status": "error", "error": "no firmware image available"}
        if not firmware.update_available(current, info["version"]):
            _set_status(r, printer_id, status="error", error="printer is up to date")
            return {"status": "error", "error": "printer is up to date"}
        try:
            _set_status(r, printer_id, status="downloading", version=info["version"])
            with firmware.make_client() as client:
                path = firmware.download_image(
                    client, info["download_url"], settings.data_dir / "firmware"
                )
            _set_status(r, printer_id, status="uploading", version=info["version"])
            build_adapter(printer.kind, conn).upload_file(f"/{path.name}", path)
        except Exception as exc:  # noqa: BLE001 -- surfaced via status, never the access code
            log.error("firmware stage failed printer=%s actor=%s: %s", printer_id, actor, exc)
            _set_status(r, printer_id, status="error", error=str(exc))
            return {"status": "error", "error": str(exc)}
        log.info(
            "firmware staged printer=%s actor=%s version=%s file=%s",
            printer_id, actor, info["version"], path.name,
        )  # fmt: skip
        _set_status(r, printer_id, status="staged", version=info["version"], filename=path.name)
        return {"status": "staged", "version": info["version"], "filename": path.name}
    finally:
        r.close()
