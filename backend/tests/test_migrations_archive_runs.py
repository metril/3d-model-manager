import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "e2f6a8b1d4c5"
_REV = "f3a7b9c2d5e1"


async def test_archive_runs_backfill_round_trip(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('p','bambu_lan','h','s','x')"
        )
        pid = await conn.fetchval("SELECT id FROM printers")
        for _ in range(2):
            await conn.execute(
                "INSERT INTO print_jobs (printer_id, state, started_at, finished_at) "
                "VALUES ($1, 'finished', now() - interval '1 hour', now())",
                pid,
            )
        jobs = [r["id"] for r in await conn.fetch("SELECT id FROM print_jobs ORDER BY id")]
        for j in jobs:
            await conn.execute(
                "INSERT INTO print_archives (print_job_id, result, duration_s) "
                "VALUES ($1, 'failed', 3600)",
                j,
            )
        # first archive already has its auto run; the second is a legacy archive without one
        await conn.execute(
            "INSERT INTO prints (print_job_id, source, result) VALUES ($1, 'auto', 'fail')",
            jobs[0],
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _REV)
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT p.print_job_id, p.archive_id, p.printer_id, p.started_at, p.result::text AS r,"
            " p.duration_min, a.run_count FROM prints p JOIN print_archives a ON a.id=p.archive_id"
            " ORDER BY p.print_job_id"
        )
        assert [r["print_job_id"] for r in rows] == jobs
        assert all(r["printer_id"] == pid and r["started_at"] is not None for r in rows)
        assert all(r["r"] == "fail" and r["run_count"] == 1 for r in rows)
        assert rows[1]["duration_min"] == 60
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM print_archives") == 2
        assert (
            await conn.fetchval(
                "select count(*) from information_schema.columns"
                " where table_name='prints' and column_name='archive_id'"
            )
            == 0
        )
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _REV)
