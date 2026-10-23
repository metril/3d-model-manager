import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "c5d8a1e7f3b9"
_Q11 = "a7c3e9d1b5f2"


async def test_q11_upgrade_creates_virtual_printers_and_downgrades(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('virtual_printers')") is None
        await conn.execute("INSERT INTO users (username) VALUES ('alice')")
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('real', 'bambu_lan', 'host', 'SER1', 'enc')"
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _Q11)

    conn = await asyncpg.connect(dsn)
    try:
        alice = await conn.fetchval("SELECT id FROM users WHERE username = 'alice'")
        printer = await conn.fetchval("SELECT id FROM printers WHERE serial = 'SER1'")
        await conn.execute(
            "INSERT INTO virtual_printers (name, model, serial, access_code_enc, mode, "
            "target_printer_id, created_by_user_id) VALUES "
            "('vp', 'N1', '03000A000000001', 'enc', 'proxy', $1, $2)",
            printer,
            alice,
        )
        row = await conn.fetchrow("SELECT enabled, created_at FROM virtual_printers")
        assert row["enabled"] is True and row["created_at"] is not None

        # serial is unique, mode is CHECK-constrained
        for sql in (
            "INSERT INTO virtual_printers (name, model, serial, access_code_enc, mode) "
            "VALUES ('dup', 'N1', '03000A000000001', 'e', 'queue')",
            "INSERT INTO virtual_printers (name, model, serial, access_code_enc, mode) "
            "VALUES ('bad', 'N1', '03000A000000002', 'e', 'bogus')",
        ):
            try:
                await conn.execute(sql)
            except (asyncpg.UniqueViolationError, asyncpg.CheckViolationError):
                pass
            else:
                raise AssertionError(f"expected a constraint violation: {sql}")

        # deleting the target printer / creator keeps the virtual printer
        await conn.execute("DELETE FROM printers WHERE id = $1", printer)
        await conn.execute("DELETE FROM users WHERE id = $1", alice)
        row = await conn.fetchrow(
            "SELECT target_printer_id, created_by_user_id FROM virtual_printers"
        )
        assert row["target_printer_id"] is None and row["created_by_user_id"] is None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('virtual_printers')") is None
    finally:
        await conn.close()
