import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "f3a7b9c2d5e1"
_REV = "b5c9d2e7f3a4"


async def _cols(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "select count(*) from information_schema.columns where table_name='print_archives'"
            " and column_name in ('timelapse_frames','timelapse_layers')"
        )
    finally:
        await conn.close()


async def test_layer_timelapse_round_trip(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    assert await _cols(dsn) == 0
    await _run_alembic(sa_url, command.upgrade, _REV)
    assert await _cols(dsn) == 2
    await _run_alembic(sa_url, command.downgrade, _PREV)
    assert await _cols(dsn) == 0
    await _run_alembic(sa_url, command.upgrade, _REV)
