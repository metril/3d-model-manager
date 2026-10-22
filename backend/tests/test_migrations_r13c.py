"""R13c migration (``f3a1c9d2e8b7``) round-trip safety: downgrade's narrowed
CHECK constraints against rows already widened into ``doc``/pdf|md|txt|docx,
and upgrade's one-time backfill of ``printers.build_volume_mm``.

Fully isolated from the rest of the suite: a scratch database is created in
the SAME testcontainer the session-scoped fixtures use (so no extra
container startup cost), migrated from scratch, exercised, and dropped --
never touching the shared ``migrated_db``/``db_session`` connections. Doing
alembic DDL against a database other tests hold open connections to
deadlocks (ALTER TABLE needs ACCESS EXCLUSIVE); a private database sidesteps
that entirely.
"""

from __future__ import annotations

import json

import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_DOWN_REVISION = "2490bc11a7f5"


async def test_downgrade_and_upgrade_round_trip(scratch_db: tuple[str, str]) -> None:
    sa_url, dsn = scratch_db

    await _run_alembic(sa_url, command.upgrade, "head")

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO blobs (hash, size, kind, format) VALUES ($1, 10, 'doc', 'pdf')",
            "a" * 64,
        )
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc, model) "
            "VALUES ('mini', 'bambu_lan', '1.2.3.4', 'ABCDEF0309ABC', 'enc', "
            "'Bambu Lab A1 mini')"
        )
        await conn.execute(
            "INSERT INTO printers (name, kind, host, serial, access_code_enc, model) "
            "VALUES ('unknown', 'bambu_lan', '1.2.3.5', 'ABCDEF0309ABD', 'enc', "
            "'Some Unknown Thing')"
        )
    finally:
        await conn.close()

    # Downgrade must collapse the doc blob to other/other BEFORE narrowing
    # the CHECK constraints -- if it didn't, this raises a constraint
    # violation instead of completing.
    await _run_alembic(sa_url, command.downgrade, _DOWN_REVISION)

    conn = await asyncpg.connect(dsn)
    try:
        kind, format_ = await conn.fetchrow(
            "SELECT kind, format FROM blobs WHERE hash = $1", "a" * 64
        )
        assert (kind, format_) == ("other", "other")
    finally:
        await conn.close()

    # Upgrading back must re-add build_volume_mm AND backfill it from each
    # row's existing `model` text (the column was just dropped by the
    # downgrade above, so this is exactly the pre-existing-row backfill
    # path, not the create-time seed).
    await _run_alembic(sa_url, command.upgrade, "head")

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT model, build_volume_mm FROM printers")
    finally:
        await conn.close()

    by_model = {
        r["model"]: (json.loads(r["build_volume_mm"]) if r["build_volume_mm"] else None)
        for r in rows
    }
    assert by_model["Bambu Lab A1 mini"] == {"x": 180, "y": 180, "z": 180}
    assert by_model["Some Unknown Thing"] is None
