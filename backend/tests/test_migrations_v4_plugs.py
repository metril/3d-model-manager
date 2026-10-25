import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "b7d3e1a9c4f2"
_V4 = "c5d7f9a1b3e8"


async def test_v4_plugs_upgrade_downgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('real', 'bambu_lan', 'host', 'SER1', 'enc')"
        )
        await conn.execute(
            "INSERT INTO smart_plugs (printer_id, kind, config_enc, auto_off_after_min) "
            "SELECT id, 'rest', 'enc', 10 FROM printers"
        )
        await conn.execute(
            "INSERT INTO smart_plugs (printer_id, kind, config_enc) "
            "SELECT id, 'rest', 'enc' FROM printers LIMIT 0"
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _V4)
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow("SELECT * FROM smart_plugs")
        assert row["auto_off"] is True and row["controls_printer_power"] is True
        assert row["name"].startswith("Plug ") and row["off_delay_mode"] == "time"
        pid = row["printer_id"]
        # many plugs per printer + standalone plugs are now allowed
        await conn.execute(
            "INSERT INTO smart_plugs (printer_id, kind, config_enc) VALUES ($1, 'rest', 'e')", pid
        )
        await conn.execute("INSERT INTO smart_plugs (kind, config_enc) VALUES ('rest', 'e')")
        assert await conn.fetchval("SELECT power_off_when_drained FROM printers") is False
        await conn.execute("DELETE FROM printers WHERE id = $1", pid)  # SET NULL, plugs survive
        assert await conn.fetchval("SELECT count(*) FROM smart_plugs") == 3
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM smart_plugs WHERE printer_id IS NULL") == 0
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _V4)
