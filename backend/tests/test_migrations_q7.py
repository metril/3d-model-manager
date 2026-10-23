import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "b6d1e8a3c5f2"
_Q7 = "b7e1c4a9d3f2"


async def test_q7_upgrade_and_downgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('p', 'bambu_lan', 'h', 's', 'x')"
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _Q7)
    conn = await asyncpg.connect(dsn)
    try:
        pid = await conn.fetchval("SELECT id FROM printers")
        assert await conn.fetchval("SELECT maintenance_mode FROM printers") is False
        tid = await conn.fetchval(
            "INSERT INTO maintenance_tasks (printer_id, name, interval_hours) "
            "VALUES ($1, 'Lube', 100) RETURNING id",
            pid,
        )
        await conn.execute("INSERT INTO maintenance_logs (task_id, hours_at) VALUES ($1, 3)", tid)
        await conn.execute(
            "INSERT INTO smart_plugs (printer_id, kind, config_enc) VALUES ($1, 'rest', 'x')", pid
        )
        bad = False
        try:
            await conn.execute(
                "INSERT INTO smart_plugs (printer_id, kind, config_enc) VALUES ($1, 'zigbee', 'x')",
                pid,
            )
        except (asyncpg.CheckViolationError, asyncpg.UniqueViolationError):
            bad = True
        assert bad
        await conn.execute("INSERT INTO backup_runs (kind, status) VALUES ('manual', 'ok')")
        await conn.execute("DELETE FROM printers WHERE id = $1", pid)
        assert await conn.fetchval("SELECT count(*) FROM maintenance_logs") == 0
        assert await conn.fetchval("SELECT count(*) FROM smart_plugs") == 0
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        for t in ("maintenance_tasks", "maintenance_logs", "smart_plugs", "backup_runs"):
            assert await conn.fetchval("select to_regclass($1)", t) is None
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _Q7)
