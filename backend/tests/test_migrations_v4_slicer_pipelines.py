import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "a9c3e5b7d1f4"
_V4 = "d8e2a4c6f1b3"


async def test_v4_slicer_pipelines_upgrade_downgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO slicer_profiles (name, kind, data) VALUES ('p', 'printer', '{}')"
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _V4)
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow("SELECT cloud_source, cloud_id, cloud_hash FROM slicer_profiles")
        assert row["cloud_source"] is None and row["cloud_id"] is None
        # (cloud_source, cloud_id) is unique only for cloud-synced rows
        await conn.execute(
            "INSERT INTO slicer_profiles (name, kind, data, cloud_source, cloud_id) "
            "VALUES ('c1', 'printer', '{}', 'orca', 'x')"
        )
        await conn.execute(
            "INSERT INTO slicer_profiles (name, kind, data) VALUES ('local2', 'printer', '{}')"
        )
        try:
            await conn.execute(
                "INSERT INTO slicer_profiles (name, kind, data, cloud_source, cloud_id) "
                "VALUES ('c2', 'printer', '{}', 'orca', 'x')"
            )
        except asyncpg.UniqueViolationError:
            pass
        else:
            raise AssertionError("duplicate (cloud_source, cloud_id) accepted")
        assert await conn.fetchval("SELECT count(*) FROM slicer_pipelines") == 0
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT to_regclass('slicer_pipelines')") is None
        cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'slicer_profiles'"
            )
        }
        assert "cloud_id" not in cols
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _V4)
