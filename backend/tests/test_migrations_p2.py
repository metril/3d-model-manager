import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "c5e2a8d4b7f1"
_P2 = "d8b3f2a6c4e9"


async def _seed_prev(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO models (slug, name) VALUES ('m', 'M')")
        await conn.execute("INSERT INTO prints (model_id) SELECT id FROM models WHERE slug = 'm'")
    finally:
        await conn.close()


async def test_p2_round_trip_keeps_manual_prints(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    await _seed_prev(dsn)
    await _run_alembic(sa_url, command.upgrade, _P2)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select source from prints") == "manual"
        assert await conn.fetchval("select to_regclass('print_archives')") is not None
        assert await conn.fetchval("select count(*) from print_jobs where origin <> 'app'") == 0
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('print_archives')") is None
        assert await conn.fetchval("select count(*) from prints") == 1
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _P2)


async def test_p2_downgrade_drops_external_jobs_and_null_model_prints(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _P2)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('p', 'bambu_lan', 'h', 's', 'x')"
        )
        await conn.execute(
            "INSERT INTO print_jobs (printer_id, file_id, state, origin) "
            "SELECT id, NULL, 'printing', 'external' FROM printers"
        )
        await conn.execute("INSERT INTO prints (model_id, source) VALUES (NULL, 'auto')")
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select count(*) from print_jobs") == 0
        assert await conn.fetchval("select count(*) from prints") == 0
    finally:
        await conn.close()
