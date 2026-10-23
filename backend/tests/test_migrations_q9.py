import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "c5d8a1e7f3b9"
_Q9 = "a9c4e7d2b6f1"


async def test_q9_upgrade_adds_spoolman_columns_and_table(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO spools (material_type) VALUES ('PLA')")
        spool_id = await conn.fetchval("SELECT id FROM spools")
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _Q9)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT spoolman_id FROM spools WHERE id=$1", spool_id) is None
        await conn.execute("UPDATE spools SET spoolman_id = 5 WHERE id = $1", spool_id)
        await conn.execute("INSERT INTO spools (material_type) VALUES ('PETG')")
        other = await conn.fetchval("SELECT id FROM spools WHERE material_type = 'PETG'")
        dup = False
        try:
            await conn.execute("UPDATE spools SET spoolman_id = 5 WHERE id = $1", other)
        except asyncpg.UniqueViolationError:
            dup = True
        assert dup
        await conn.execute(
            "INSERT INTO spoolman_sync_state (spool_id, last_pulled_weight) VALUES ($1, 42.5)",
            spool_id,
        )
        assert await conn.fetchval("select to_regclass('spoolman_pushes')") is not None
        await conn.execute("DELETE FROM spools WHERE id = $1", spool_id)
        assert await conn.fetchval("SELECT count(*) FROM spoolman_sync_state") == 0
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('spoolman_sync_state')") is None
        assert await conn.fetchval("select to_regclass('spoolman_pushes')") is None
        assert (
            await conn.fetchval(
                "select column_name from information_schema.columns "
                "where table_name = 'spools' and column_name = 'spoolman_id'"
            )
            is None
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _Q9)
