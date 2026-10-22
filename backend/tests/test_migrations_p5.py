import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "142d0b67c477"
_P5 = "e6b1d4a8c2f7"


async def test_p5_upgrade_creates_spool_tables_and_tray_snapshot(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    await _run_alembic(sa_url, command.upgrade, _P5)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('spools')") is not None
        assert await conn.fetchval("select to_regclass('spool_slots')") is not None
        assert await conn.fetchval("select to_regclass('spool_usage')") is not None
        assert (
            await conn.fetchval(
                "select column_name from information_schema.columns "
                "where table_name = 'print_jobs' and column_name = 'tray_snapshot'"
            )
            == "tray_snapshot"
        )

        # printer_id, ams_unit, slot is unique.
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('p', 'bambu_lan', 'h', 's', 'x')"
        )
        await conn.execute("INSERT INTO spools (material_type) VALUES ('pla'), ('pla')")
        await conn.execute(
            "INSERT INTO spool_slots (printer_id, ams_unit, slot, spool_id) "
            "SELECT p.id, 0, 0, s.id FROM printers p, spools s ORDER BY s.id LIMIT 1"
        )
        with_dupe_slot_failed = False
        try:
            await conn.execute(
                "INSERT INTO spool_slots (printer_id, ams_unit, slot, spool_id) "
                "SELECT p.id, 0, 0, s.id FROM printers p, spools s ORDER BY s.id DESC LIMIT 1"
            )
        except asyncpg.UniqueViolationError:
            with_dupe_slot_failed = True
        assert with_dupe_slot_failed

        # tray_uuid is unique.
        await conn.execute("INSERT INTO spools (material_type, tray_uuid) VALUES ('pla', 'u1')")
        dupe_tray_failed = False
        try:
            await conn.execute("INSERT INTO spools (material_type, tray_uuid) VALUES ('pla', 'u1')")
        except asyncpg.UniqueViolationError:
            dupe_tray_failed = True
        assert dupe_tray_failed
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('spools')") is None
        assert await conn.fetchval("select to_regclass('spool_slots')") is None
        assert await conn.fetchval("select to_regclass('spool_usage')") is None
        assert (
            await conn.fetchval(
                "select column_name from information_schema.columns "
                "where table_name = 'print_jobs' and column_name = 'tray_snapshot'"
            )
            is None
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _P5)
