import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "f2c8a4e6b1d9"
_T8A = "e5b8c3a91f47"


async def test_t8a_upgrade_downgrade_upgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('user_email_preferences')") is None
        await conn.execute("INSERT INTO users (username) VALUES ('alice')")
        alice = await conn.fetchval("SELECT id FROM users")
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T8A)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO user_email_preferences (user_id) VALUES ($1)", alice)
        row = await conn.fetchrow("SELECT * FROM user_email_preferences")
        assert not any(
            row[f]
            for f in (
                "notify_print_start",
                "notify_print_complete",
                "notify_print_failed",
                "notify_print_stopped",
            )
        )
        assert row["created_at"] is not None
        try:
            await conn.execute("INSERT INTO user_email_preferences (user_id) VALUES ($1)", alice)
        except asyncpg.UniqueViolationError:
            pass
        else:
            raise AssertionError("user_id must be unique")
        await conn.execute("DELETE FROM users WHERE id = $1", alice)
        assert await conn.fetchval("SELECT count(*) FROM user_email_preferences") == 0
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('user_email_preferences')") is None
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _T8A)
