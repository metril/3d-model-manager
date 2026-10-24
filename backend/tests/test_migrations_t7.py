import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "c9d4e6f8b2a1"
_T7 = "d7a3c5e9f1b4"


async def test_t7_up_down_up_keeps_archives(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('p','bambu_lan','h','s','x')"
        )
        pid = await conn.fetchval("SELECT id FROM printers")
        await conn.execute(
            "INSERT INTO print_jobs (printer_id, state) VALUES ($1, 'finished')", pid
        )
        jid = await conn.fetchval("SELECT id FROM print_jobs")
        await conn.execute(
            "INSERT INTO print_archives (print_job_id, result) VALUES ($1, 'completed')", jid
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T7)
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT tags, photos::text AS photos, confirm_requested, result_override, "
            "user_verdict, confirm_token FROM print_archives"
        )
        assert row["tags"] == [] and row["photos"] == "[]"
        assert row["confirm_requested"] is False
        assert row["result_override"] is None and row["user_verdict"] is None
        await conn.execute("UPDATE print_archives SET result_override='failed', user_verdict='bad'")
        bad = False
        try:
            await conn.execute("UPDATE print_archives SET user_verdict='meh'")
        except asyncpg.CheckViolationError:
            bad = True
        assert bad
        await conn.execute("UPDATE print_archives SET confirm_token='abc'")
        await conn.execute("UPDATE print_archives SET result_override=NULL, user_verdict=NULL")
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM print_archives") == 1
        assert (
            await conn.fetchval(
                "select column_name from information_schema.columns "
                "where table_name='print_archives' and column_name='photos'"
            )
            is None
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T7)
