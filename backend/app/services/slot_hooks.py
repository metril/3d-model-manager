"""Seams between AMS slot configuration and other phases."""

from __future__ import annotations

import logging

import redis.asyncio as aioredis
from sqlalchemy import select

from app.models.kprofiles import SpoolKProfile
from app.printers.capabilities import capabilities_for
from app.services import kprofiles
from app.services.printer_state import read_state_async

log = logging.getLogger(__name__)


async def kprofile_after_slot_configure(
    db,
    settings,
    ctx,
    printer,
    ams_id: int,
    tray_id: int,
    spool_id: int | None,
    tray_info_idx: str | None = None,
) -> None:
    """Chain a K-profile selection (``extrusion_cali_sel``) after a successful
    ``ams_slot_configure`` when the assigned spool has a stored profile for
    this printer and nozzle. Best effort: never fails the slot assignment."""
    try:  # slot (re)configured: forget the negative cache / deliberate default
        flags = aioredis.Redis.from_url(settings.redis_url)
        try:
            await kprofiles.clear_tray_flags_async(flags, printer.id, ams_id, tray_id)
        finally:
            await flags.aclose()
    except Exception as e:  # noqa: BLE001
        log.warning("kprofile flag reset failed: %s", type(e).__name__)
    if spool_id is None or not capabilities_for(printer.model, printer.serial).kprofiles:
        return
    try:
        rows = list(
            (
                await db.execute(
                    select(SpoolKProfile).where(
                        SpoolKProfile.spool_id == spool_id, SpoolKProfile.printer_id == printer.id
                    )
                )
            ).scalars()
        )
        if not rows:
            return
        client = aioredis.Redis.from_url(settings.redis_url)
        try:
            state = await read_state_async(client, printer.id) or {}
            dual = capabilities_for(printer.model, printer.serial).dual_nozzle
            row = kprofiles.match_profile(rows, state.get("nozzle_mm"), dual)
            if row is None:
                return
            nozzle = kprofiles.norm_nozzle(row.nozzle_diameter) or "0.4"
            cali = row.cali_idx
            if row.setting_id:
                hit = kprofiles.find_in_cache(
                    await kprofiles.read_cache_async(client, printer.id, nozzle), row.setting_id
                )
                if hit is not None:
                    cali = int(hit["cali_idx"])
        finally:
            await client.aclose()
        filament = tray_info_idx or next(
            (
                t.get("tray_info_idx")
                for t in state.get("trays") or []
                if t.get("ams_unit") == ams_id and t.get("tray") == tray_id
            ),
            None,
        )
        if cali is None or cali < 0 or not filament:
            return
        from app.api.printer_control import run_command

        await run_command(
            db,
            settings,
            ctx,
            printer,
            "kprofile_select",
            {
                "ams_id": ams_id,
                "tray_id": tray_id,
                "cali_idx": cali,
                "filament_id": filament,
                "nozzle_diameter": nozzle,
            },
            audit_extra={"source": "slot_configure", "spool_id": spool_id},
        )
    except Exception as e:  # noqa: BLE001 -- type only; the assignment already succeeded
        log.warning("kprofile select after slot configure failed: %s", type(e).__name__)
