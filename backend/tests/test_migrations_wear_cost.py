import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "d1e5f7a9c3b2"
_REV = "e2f6a8b1d4c5"

_COLS = (
    ("printers", "wear_cost_per_hour"),
    ("prints", "wear_cost"),
    ("print_jobs", "energy_price_start"),
    ("print_jobs", "energy_price_end"),
)


async def _present(dsn: str) -> list[bool]:
    conn = await asyncpg.connect(dsn)
    try:
        return [
            bool(
                await conn.fetchval(
                    "select count(*) from information_schema.columns"
                    " where table_name=$1 and column_name=$2",
                    t,
                    c,
                )
            )
            for t, c in _COLS
        ]
    finally:
        await conn.close()


async def test_wear_cost_upgrade_downgrade_upgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    assert not any(await _present(dsn))
    await _run_alembic(sa_url, command.upgrade, _REV)
    assert all(await _present(dsn))
    await _run_alembic(sa_url, command.downgrade, _PREV)
    assert not any(await _present(dsn))
    await _run_alembic(sa_url, command.upgrade, _REV)
    assert all(await _present(dsn))
