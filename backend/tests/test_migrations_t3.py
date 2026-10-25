import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "c9d4e6f8b2a1"
_T3 = "d3a7c1e5f9b2"


async def test_t3_upgrade_downgrade_upgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO users (username) VALUES ('alice')")
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('real', 'bambu_lan', 'host', 'SER1', 'enc')"
        )
        assert await conn.fetchval("select to_regclass('ams_labels')") is None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T3)

    conn = await asyncpg.connect(dsn)
    try:
        # existing printers backfill to false
        assert await conn.fetchval("SELECT awaiting_plate_clear FROM printers") is False
        alice = await conn.fetchval("SELECT id FROM users WHERE username = 'alice'")
        await conn.execute(
            "INSERT INTO ams_labels (ams_serial, ams_id, label, created_by_user_id) "
            "VALUES ('SN1', 0, 'Shelf', $1)",
            alice,
        )
        assert await conn.fetchval("SELECT created_at FROM ams_labels") is not None
        try:
            await conn.execute(
                "INSERT INTO ams_labels (ams_serial, ams_id, label) VALUES ('SN1', 1, 'x')"
            )
        except asyncpg.UniqueViolationError:
            pass
        else:
            raise AssertionError("ams_serial must be unique")
        await conn.execute("DELETE FROM users WHERE id = $1", alice)
        assert await conn.fetchval("SELECT created_by_user_id FROM ams_labels") is None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('ams_labels')") is None
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'printers'"
        )
        assert "awaiting_plate_clear" not in {c["column_name"] for c in cols}
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T3)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('ams_labels')") is not None
    finally:
        await conn.close()
