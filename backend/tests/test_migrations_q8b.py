import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "a8e2d4c6b1f3"
_Q8B = "b8c3d5e7a1f2"


async def test_q8b_upgrade_creates_sensor_samples_and_downgrades(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('printer_sensor_samples')") is None
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('p', 'bambu_lan', 'h', 'S1', 'enc')"
        )
        pid = await conn.fetchval("SELECT id FROM printers")
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _Q8B)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO printer_sensor_samples (printer_id, nozzle, ams) "
            'VALUES ($1, 210.5, \'[{"id": 0, "humidity": 3, "temp": 24.0}]\')',
            pid,
        )
        assert await conn.fetchval("SELECT ts IS NOT NULL FROM printer_sensor_samples")
        assert (
            await conn.fetchval(
                "select indexname from pg_indexes where "
                "indexname = 'ix_printer_sensor_samples_printer_id_ts'"
            )
            is not None
        )
        await conn.execute("DELETE FROM printers WHERE id = $1", pid)
        assert await conn.fetchval("SELECT count(*) FROM printer_sensor_samples") == 0
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('printer_sensor_samples')") is None
    finally:
        await conn.close()
