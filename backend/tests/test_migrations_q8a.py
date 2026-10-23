import asyncpg
import pytest

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "a7c3e9d1b5f2"
_Q8A = "a8e2d4c6b1f3"


async def test_q8a_upgrade_downgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    await _run_alembic(sa_url, command.upgrade, _Q8A)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO external_links (name, url) VALUES ('Grafana', 'https://g')")
        row = await conn.fetchrow("SELECT sort_order, open_mode FROM external_links")
        assert (row["sort_order"], row["open_mode"]) == (0, "iframe")
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO external_links (name, url, open_mode) "
                "VALUES ('x', 'https://x', 'popup')"
            )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('external_links')") is None
    finally:
        await conn.close()
