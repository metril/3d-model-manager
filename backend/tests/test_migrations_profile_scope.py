import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "a9d3e5f7b1c2"
_HEAD = "b7e1c4a9d2f6"


async def test_profile_scope_upgrade_downgrade_upgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    await _run_alembic(sa_url, command.upgrade, _HEAD)
    conn = await asyncpg.connect(dsn)
    try:
        cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name IN ('slicer_profiles', 'printers')"
            )
        }
        assert {"printer_model_key", "compat", "layer_height_mm", "nozzle_mm"} <= cols
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'slicer_profiles' AND column_name = 'compat'"
            )
            == 0
        )
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _HEAD)
