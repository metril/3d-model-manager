import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "d3a7c1e5f9b2"
_T1 = "e1b5d7a9c3f2"


async def test_t1_upgrade_downgrade_upgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO users (username) VALUES ('alice')")
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('real', 'bambu_lan', 'host', 'SER1', 'enc')"
        )
        await conn.execute(
            "INSERT INTO smart_plugs (printer_id, kind, config_enc) "
            "SELECT id, 'rest', 'enc' FROM printers"
        )
        assert await conn.fetchval("select to_regclass('scheduled_dryings')") is None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T1)

    conn = await asyncpg.connect(dsn)
    try:
        # existing plugs backfill to false
        assert await conn.fetchval("SELECT auto_off_after_drying FROM smart_plugs") is False
        alice = await conn.fetchval("SELECT id FROM users WHERE username = 'alice'")
        pid = await conn.fetchval("SELECT id FROM printers")
        await conn.execute(
            "INSERT INTO scheduled_dryings (printer_id, ams_id, temp, duration_hours, "
            "created_by_user_id) VALUES ($1, 0, 65, 12, $2)",
            pid,
            alice,
        )
        row = await conn.fetchrow("SELECT * FROM scheduled_dryings")
        assert (row["status"], row["source"], row["filament"]) == ("pending", "manual", "")
        assert row["rotate_tray"] is False and row["created_at"] is not None
        await conn.execute("DELETE FROM users WHERE id = $1", alice)
        assert await conn.fetchval("SELECT created_by_user_id FROM scheduled_dryings") is None
        await conn.execute("DELETE FROM printers WHERE id = $1", pid)  # cascade
        assert await conn.fetchval("SELECT count(*) FROM scheduled_dryings") == 0
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('scheduled_dryings')") is None
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'smart_plugs'"
        )
        assert "auto_off_after_drying" not in {c["column_name"] for c in cols}
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T1)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('scheduled_dryings')") is not None
    finally:
        await conn.close()
