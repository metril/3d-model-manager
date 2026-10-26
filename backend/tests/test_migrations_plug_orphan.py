import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "d8e2a4c6f1b3"
_NEW = "e3b7d1f5a9c2"

_INS = (
    "INSERT INTO smart_plugs (name, kind, config_enc, controls_printer_power, auto_off, "
    "schedule_enabled, auto_on_before_print, auto_off_after_drying) "
    "VALUES ($1, 'tasmota', 'e', $2, $3, false, false, false)"
)


async def test_orphan_backfill_matches_delete_signature_only(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(_INS, "orphan-like", False, False)  # all-off signature
        await conn.execute(_INS, "standalone-live", True, False)  # controls power
        await conn.execute(_INS, "standalone-auto", False, True)  # automation on
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _NEW)

    conn = await asyncpg.connect(dsn)
    try:
        rows = {
            r["name"]: r["orphaned_at"]
            for r in await conn.fetch("SELECT name, orphaned_at FROM smart_plugs")
        }
    finally:
        await conn.close()
    assert rows["orphan-like"] is not None
    assert rows["standalone-live"] is None and rows["standalone-auto"] is None
