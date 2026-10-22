import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "a2b3c4d5e6f7"
_P7 = "d2a7c4e9f1b3"


async def test_p7_upgrade_adds_tables_and_queue_columns(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO users (username) VALUES ('alice'), ('bob')")
        alice_id = await conn.fetchval("SELECT id FROM users WHERE username = 'alice'")
        bob_id = await conn.fetchval("SELECT id FROM users WHERE username = 'bob'")

        await conn.execute("INSERT INTO models (slug, name) VALUES ('m1', 'Model 1')")
        model_id = await conn.fetchval("SELECT id FROM models WHERE slug = 'm1'")
        await conn.execute("INSERT INTO print_queue (model_id, position) VALUES ($1, 1)", model_id)
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _P7)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('cost_centers')") is not None
        assert await conn.fetchval("select to_regclass('ledger_entries')") is not None

        # the pre-existing queue row reads review_state='none' with NULL
        # billing columns.
        row = await conn.fetchrow(
            "SELECT review_state, cost_center_id, reserved_cost, hold_reason "
            "FROM print_queue WHERE model_id = $1",
            model_id,
        )
        assert row["review_state"] == "none"
        assert row["cost_center_id"] is None
        assert row["reserved_cost"] is None
        assert row["hold_reason"] is None

        # the CHECK rejects a center with both owner_user_id and group_id set...
        await conn.execute("INSERT INTO groups (name) VALUES ('g1')")
        group_id = await conn.fetchval("SELECT id FROM groups WHERE name = 'g1'")
        both_set_failed = False
        try:
            await conn.execute(
                "INSERT INTO cost_centers (name, owner_user_id, group_id) VALUES ('both', $1, $2)",
                alice_id,
                group_id,
            )
        except asyncpg.CheckViolationError:
            both_set_failed = True
        assert both_set_failed

        # ...and a center with neither set.
        neither_set_failed = False
        try:
            await conn.execute("INSERT INTO cost_centers (name) VALUES ('neither')")
        except asyncpg.CheckViolationError:
            neither_set_failed = True
        assert neither_set_failed

        # a valid owner-only center is accepted.
        await conn.execute(
            "INSERT INTO cost_centers (name, owner_user_id) VALUES ('alice-cc', $1)", alice_id
        )

        # a second private center for the same owner is rejected.
        dupe_owner_failed = False
        try:
            await conn.execute(
                "INSERT INTO cost_centers (name, owner_user_id) VALUES ('alice-cc-2', $1)",
                alice_id,
            )
        except asyncpg.UniqueViolationError:
            dupe_owner_failed = True
        assert dupe_owner_failed

        # a different owner is fine.
        await conn.execute(
            "INSERT INTO cost_centers (name, owner_user_id) VALUES ('bob-cc', $1)", bob_id
        )

        # a group-only center is also accepted.
        await conn.execute(
            "INSERT INTO cost_centers (name, group_id) VALUES ('g1-cc', $1)", group_id
        )

        alice_cc_id = await conn.fetchval("SELECT id FROM cost_centers WHERE name = 'alice-cc'")

        # uq_ledger_queue_kind rejects a duplicate (queue, 'charge')...
        await conn.execute(
            "INSERT INTO ledger_entries (cost_center_id, kind, amount, print_queue_id) "
            "VALUES ($1, 'charge', -5.00, "
            "(SELECT id FROM print_queue WHERE model_id = $2))",
            alice_cc_id,
            model_id,
        )
        dupe_charge_failed = False
        try:
            await conn.execute(
                "INSERT INTO ledger_entries (cost_center_id, kind, amount, print_queue_id) "
                "VALUES ($1, 'charge', -1.00, "
                "(SELECT id FROM print_queue WHERE model_id = $2))",
                alice_cc_id,
                model_id,
            )
        except asyncpg.UniqueViolationError:
            dupe_charge_failed = True
        assert dupe_charge_failed

        # ...but allows two deposits with NULL queue.
        await conn.execute(
            "INSERT INTO ledger_entries (cost_center_id, kind, amount) VALUES "
            "($1, 'deposit', 10.00), ($1, 'deposit', 20.00)",
            alice_cc_id,
        )
        deposit_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_entries WHERE cost_center_id = $1 AND kind = 'deposit'",
            alice_cc_id,
        )
        assert deposit_count == 2
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('cost_centers')") is None
        assert await conn.fetchval("select to_regclass('ledger_entries')") is None
        assert (
            await conn.fetchval(
                "select column_name from information_schema.columns "
                "where table_name = 'print_queue' and column_name = 'review_state'"
            )
            is None
        )
        row = await conn.fetchrow("SELECT model_id FROM print_queue WHERE model_id = $1", model_id)
        assert row["model_id"] == model_id
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _P7)
