import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "b8c3d5e7a1f2"
_Q2 = "b2f7c1a9d4e3"


async def test_q2_upgrade_adds_audit_log_and_downgrades(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    await _run_alembic(sa_url, command.upgrade, _Q2)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO audit_log (action, payload) VALUES ('gcode', '{\"g\": \"G28\"}')"
        )
        row = await conn.fetchrow("SELECT action, payload, created_at FROM audit_log")
        assert row["action"] == "gcode" and row["created_at"] is not None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('audit_log')") is None
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _Q2)
