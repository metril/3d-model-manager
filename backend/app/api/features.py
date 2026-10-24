"""Feature-flag probe (M4). Session-gated but NOT printer-gated: the
frontend reads it to decide whether to show the Printer nav / route, so it
must answer even when the printer feature is off.

Round 8 T6 adds the watched-folder slicer fields
(``app.tasks.slicer_watch``, Round 8 T5): ``watch_dir`` surfaces the
container path so Settings can show it (and note it maps to
``WATCH_HOST_DIR`` on the host); ``watch_enabled`` mirrors
the exact condition ``app.tasks.scheduler.dispatch_scheduled`` uses to
actually dispatch the watched-folder scan (dir set AND a positive poll
interval) rather than re-deriving it in the frontend.

Round 10 T3: ``printer_enabled`` and the interval half of ``watch_enabled``
now come from the DB-backed ``AppConfig`` (``app.services.app_config
.get_app_config``) -- the same live read ``require_printer_enabled`` uses --
so flipping either via ``PUT /settings/app`` shows up here immediately.
``watch_dir`` stays env-only (a filesystem path fixed at deploy time, not a
runtime-editable setting), so it's still read straight off ``Settings``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.services.app_config import get_app_config
from app.services.server_config import get_server_config

router = APIRouter(tags=["features"])


@router.get("/features")
async def get_features(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict:
    config = await get_app_config(db, settings)
    server = await get_server_config(db, settings)
    return {
        "printer_enabled": config.printer_enabled,
        "watch_dir": str(settings.watch_dir) if settings.watch_dir else None,
        "watch_enabled": settings.watch_dir is not None and config.watch_interval_s > 0,
        "virtual_printer_enabled": True,  # always on (compat constant)
        "slicer_configured": server.slicer_enabled and bool(server.slicer_url),
    }
