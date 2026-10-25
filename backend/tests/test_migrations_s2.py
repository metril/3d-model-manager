import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "e5b8c3a91f47"
_S2 = "a4c6e8b1d3f5"


async def test_s2_upgrade_downgrade_upgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('slice_jobs')") is None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _S2)
    conn = await asyncpg.connect(dsn)
    try:
        cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name='print_queue'"
            )
        }
        assert {"last_log", "slice_task_id"} <= cols
        assert await conn.fetchval("select to_regclass('slice_jobs')") is not None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('slice_jobs')") is None
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name='print_queue' AND column_name='last_log'"
            )
            == 0
        )
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _S2)
