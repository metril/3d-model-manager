import asyncpg
import pytest

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "a8e2d4c6b1f3"
_Q4 = "b6d1e8a3c5f2"


async def test_q4_upgrade_downgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('overlay_tokens')") is None
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('p', 'bambu_lan', 'h', 'S1', 'e')"
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _Q4)
    conn = await asyncpg.connect(dsn)
    try:
        pid = await conn.fetchval("SELECT id FROM printers")
        await conn.execute(
            "INSERT INTO overlay_tokens (printer_id, token_hash, label) VALUES ($1, 'h1', 'obs')",
            pid,
        )
        row = await conn.fetchrow("SELECT created_at, revoked_at FROM overlay_tokens")
        assert row["created_at"] is not None and row["revoked_at"] is None
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO overlay_tokens (printer_id, token_hash) VALUES ($1, 'h1')", pid
            )
        await conn.execute("DELETE FROM printers WHERE id = $1", pid)
        assert await conn.fetchval("SELECT count(*) FROM overlay_tokens") == 0
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('overlay_tokens')") is None
    finally:
        await conn.close()
