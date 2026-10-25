import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "e1b5d7a9c3f2"
_T4 = "f4b2d8a6c1e3"


async def test_t4_upgrade_downgrade_upgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO users (username) VALUES ('alice')")
        await conn.execute("INSERT INTO models (slug, name) VALUES ('m', 'm')")
        await conn.execute(
            "INSERT INTO print_queue (model_id, position, state) VALUES (1, 1, 'waiting')"
        )
        assert await conn.fetchval("select to_regclass('print_batches')") is None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T4)

    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT manual_start, require_previous_success, auto_off_after, scheduled_at, stage "
            "FROM print_queue"
        )
        assert row["manual_start"] is False and row["scheduled_at"] is None
        alice = await conn.fetchval("SELECT id FROM users WHERE username = 'alice'")
        batch = await conn.fetchval(
            "INSERT INTO print_batches (name, quantity, created_by_user_id) "
            "VALUES ('b', 3, $1) RETURNING id",
            alice,
        )
        assert await conn.fetchval("SELECT status FROM print_batches") == "draft"
        await conn.execute("UPDATE print_queue SET batch_id = $1, state = 'skipped'", batch)
        # batch delete nulls the FK
        await conn.execute("DELETE FROM print_batches")
        assert await conn.fetchval("SELECT batch_id FROM print_queue") is None
        await conn.execute("DELETE FROM users WHERE id = $1", alice)
        try:
            await conn.execute("UPDATE print_queue SET state = 'bogus'")
        except asyncpg.CheckViolationError:
            pass
        else:
            raise AssertionError("state CHECK must still reject unknown values")
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT state FROM print_queue") == "held"
        assert await conn.fetchval("select to_regclass('print_batches')") is None
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'print_queue'"
        )
        assert "batch_id" not in {c["column_name"] for c in cols}
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T4)
