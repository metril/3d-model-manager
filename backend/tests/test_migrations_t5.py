import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "d7a3c5e9f1b4"
_T5 = "d1e5a7c3b9f2"


async def test_t5_backfills_locations_and_round_trips(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        for loc in ("Shelf A", " shelf a ", "SHELF A", "Dry Box", None, "   "):
            await conn.execute(
                "INSERT INTO spools (material_type, location) VALUES ('PLA', $1)", loc
            )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T5)

    conn = await asyncpg.connect(dsn)
    try:
        names = sorted(r["name"] for r in await conn.fetch("SELECT name FROM storage_locations"))
        assert names == ["Dry Box", "Shelf A"]  # first-seen name wins, deduped
        rows = await conn.fetch(
            "SELECT s.id, l.name FROM spools s "
            "LEFT JOIN storage_locations l ON l.id = s.location_id "
            "ORDER BY s.id"
        )
        assert [r["name"] for r in rows] == ["Shelf A", "Shelf A", "Shelf A", "Dry Box", None, None]
        assert await conn.fetchval("SELECT count(*) FROM color_catalog WHERE is_default") > 500
        assert (
            await conn.fetchval(
                "select count(*) from information_schema.columns "
                "where table_name='spools' and column_name='location'"
            )
            == 0
        )
        # SET NULL on location delete
        await conn.execute("DELETE FROM storage_locations WHERE name = 'Dry Box'")
        assert await conn.fetchval("SELECT count(*) FROM spools WHERE location_id IS NULL") == 3
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        locs = [r["location"] for r in await conn.fetch("SELECT location FROM spools ORDER BY id")]
        assert locs == ["Shelf A", "Shelf A", "Shelf A", None, None, None]
        assert await conn.fetchval("select to_regclass('suppliers')") is None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T5)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM storage_locations") == 1
    finally:
        await conn.close()
