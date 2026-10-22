import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "d2a7c4e9f1b3"
_P8 = "e8b3f1a6c2d4"


async def test_p8_upgrade_adds_ha_links_and_energy_columns(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO users (username) VALUES ('alice')")
        alice_id = await conn.fetchval("SELECT id FROM users WHERE username = 'alice'")

        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc) "
            "VALUES ('p1', 'bambu_lan', 'host', 'SER1', 'enc'), "
            "('p2', 'bambu_lan', 'host', 'SER2', 'enc')"
        )
        printer_id = await conn.fetchval("SELECT id FROM printers WHERE serial = 'SER1'")
        cascade_printer_id = await conn.fetchval("SELECT id FROM printers WHERE serial = 'SER2'")

        await conn.execute(
            "INSERT INTO print_jobs (printer_id, state) VALUES ($1, 'queued')", printer_id
        )
        job_id = await conn.fetchval("SELECT id FROM print_jobs WHERE printer_id = $1", printer_id)
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _P8)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('ha_links')") is not None

        # pre-existing print_jobs row reads NULL for both new energy columns.
        row = await conn.fetchrow(
            "SELECT energy_start_kwh, energy_end_kwh FROM print_jobs WHERE id = $1", job_id
        )
        assert row["energy_start_kwh"] is None
        assert row["energy_end_kwh"] is None

        # a NULL printer_id link ("all printers") is accepted.
        await conn.execute(
            "INSERT INTO ha_links (printer_id, entity_id, role, created_by_user_id) "
            "VALUES (NULL, 'binary_sensor.door', 'door', $1)",
            alice_id,
        )
        # a printer-scoped energy link is accepted.
        await conn.execute(
            "INSERT INTO ha_links (printer_id, entity_id, role) "
            "VALUES ($1, 'sensor.plug_energy', 'energy_kwh')",
            cascade_printer_id,
        )
        link_count = await conn.fetchval("SELECT count(*) FROM ha_links")
        assert link_count == 2

        # deleting the printer cascades its printer-scoped link but leaves
        # the NULL-printer (all-printers) link untouched.
        await conn.execute("DELETE FROM printers WHERE id = $1", cascade_printer_id)
        remaining = await conn.fetch("SELECT entity_id FROM ha_links")
        assert [r["entity_id"] for r in remaining] == ["binary_sensor.door"]

        # an unknown role is rejected by the CHECK constraint.
        bad_role_rejected = False
        try:
            await conn.execute(
                "INSERT INTO ha_links (printer_id, entity_id, role) "
                "VALUES (NULL, 'sensor.x', 'bogus_role')"
            )
        except asyncpg.CheckViolationError:
            bad_role_rejected = True
        assert bad_role_rejected
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('ha_links')") is None
        assert (
            await conn.fetchval(
                "select column_name from information_schema.columns "
                "where table_name = 'print_jobs' and column_name = 'energy_start_kwh'"
            )
            is None
        )
        row = await conn.fetchrow("SELECT id FROM print_jobs WHERE id = $1", job_id)
        assert row["id"] == job_id
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _P8)
