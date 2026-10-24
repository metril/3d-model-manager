import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "c6a1f4d8b2e7"
_Q10 = "c9d4e6f8b2a1"


async def test_q10_upgrade_creates_failure_detections_and_downgrades(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('failure_detections')") is None
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('p', 'bambu_lan', 'h', 'S1', 'enc')"
        )
        pid = await conn.fetchval("SELECT id FROM printers")
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _Q10)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO failure_detections (printer_id, score, smoothed, action) "
            "VALUES ($1, 0.9, 0.8, 'pause')",
            pid,
        )
        assert await conn.fetchval("SELECT created_at IS NOT NULL FROM failure_detections")
        assert (
            await conn.fetchval(
                "select indexname from pg_indexes where "
                "indexname = 'ix_failure_detections_printer_id_created_at'"
            )
            is not None
        )
        await conn.execute("DELETE FROM printers WHERE id = $1", pid)
        assert await conn.fetchval("SELECT count(*) FROM failure_detections") == 0
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('failure_detections')") is None
    finally:
        await conn.close()
