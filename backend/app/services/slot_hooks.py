"""Seams between AMS slot configuration and other phases."""

from __future__ import annotations


async def kprofile_after_slot_configure(
    db, settings, ctx, printer, ams_id: int, tray_id: int, spool_id: int | None
) -> None:
    """Chain a K-profile selection (``extrusion_cali_sel``) after a successful
    ``ams_slot_configure`` when the assigned spool has a stored profile.

    TODO(T2): fill in -- look up ``spool_k_profiles`` for ``spool_id`` on this
    printer and, when the adapter has ``kprofile_select`` (the table/command
    land in T2), run it with the stored ``cali_idx``. No-op until then.
    """
    return None
