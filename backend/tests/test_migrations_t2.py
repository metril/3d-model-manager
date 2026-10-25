import asyncpg
import pytest

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "f4b2d8a6c1e3"
_T2 = "f2c8a4e6b1d9"


async def test_t2_upgrade_downgrade_upgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO users (username) VALUES ('alice')")
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('real', 'bambu_lan', 'host', 'SER1', 'enc')"
        )
        await conn.execute("INSERT INTO spools (material_type) VALUES ('PLA')")
        assert await conn.fetchval("select to_regclass('spool_k_profiles')") is None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T2)

    conn = await asyncpg.connect(dsn)
    try:
        alice = await conn.fetchval("SELECT id FROM users WHERE username = 'alice'")
        pid = await conn.fetchval("SELECT id FROM printers")
        sid = await conn.fetchval("SELECT id FROM spools")
        ins = (
            "INSERT INTO spool_k_profiles (spool_id, printer_id, nozzle_diameter, k_value, "
            "created_by_user_id) VALUES ($1, $2, 0.4, 0.02, $3)"
        )
        await conn.execute(ins, sid, pid, alice)
        row = await conn.fetchrow("SELECT * FROM spool_k_profiles")
        assert row["extruder"] == 0 and row["created_at"] is not None
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(ins, sid, pid, alice)
        await conn.execute(
            "INSERT INTO kprofile_notes (printer_id, setting_id, note) VALUES ($1, 'PF1', 'x')",
            pid,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO kprofile_notes (printer_id, setting_id, note) VALUES ($1, 'PF1', 'y')",
                pid,
            )
        await conn.execute("DELETE FROM users WHERE id = $1", alice)
        assert await conn.fetchval("SELECT created_by_user_id FROM spool_k_profiles") is None
        await conn.execute("DELETE FROM spools WHERE id = $1", sid)  # cascade
        assert await conn.fetchval("SELECT count(*) FROM spool_k_profiles") == 0
        await conn.execute("DELETE FROM printers WHERE id = $1", pid)  # cascade
        assert await conn.fetchval("SELECT count(*) FROM kprofile_notes") == 0
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('spool_k_profiles')") is None
        assert await conn.fetchval("select to_regclass('kprofile_notes')") is None
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _T2)
