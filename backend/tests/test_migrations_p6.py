import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "e6b1d4a8c2f7"
_P6 = "b4d7e2a9c6f1"


async def test_p6_upgrade_adds_tables_and_queue_columns(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO models (slug, name) VALUES ('m1', 'Model 1'), ('m2', 'Model 2')"
        )
        model_id = await conn.fetchval("SELECT id FROM models WHERE slug = 'm1' LIMIT 1")
        await conn.execute("INSERT INTO print_queue (model_id, position) VALUES ($1, 1)", model_id)
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _P6)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('slicer_profiles')") is not None
        assert await conn.fetchval("select to_regclass('print_recipes')") is not None

        # the pre-existing bare row survives with state='waiting', file_id NULL.
        row = await conn.fetchrow(
            "SELECT state, file_id, copies, attempts FROM print_queue WHERE model_id = $1",
            model_id,
        )
        assert row["state"] == "waiting"
        assert row["file_id"] is None
        assert row["copies"] == 1
        assert row["attempts"] == 0

        # two bare rows (file_id NULL) for the same model are rejected...
        dupe_bare_failed = False
        try:
            await conn.execute(
                "INSERT INTO print_queue (model_id, position) VALUES ($1, 2)", model_id
            )
        except asyncpg.UniqueViolationError:
            dupe_bare_failed = True
        assert dupe_bare_failed

        # ...but two rows with the same model_id are allowed once file_id is set.
        await conn.execute(
            "INSERT INTO models (slug, name) VALUES ('m3', 'Model 3')",
        )
        await conn.execute(
            "INSERT INTO revisions (model_id, number, dir_name) VALUES ($1, 1, 'r1')", model_id
        )
        revision_id = await conn.fetchval(
            "SELECT id FROM revisions WHERE model_id = $1 LIMIT 1", model_id
        )
        await conn.execute(
            "INSERT INTO blobs (hash, size, kind, format) VALUES "
            "(repeat('a', 64), 1, 'mesh', 'stl')"
        )
        await conn.execute(
            "INSERT INTO files (revision_id, blob_hash, rel_path, storage_path) VALUES "
            "($1, repeat('a', 64), 'a.stl', '/a.stl'), "
            "($1, repeat('a', 64), 'b.stl', '/b.stl')",
            revision_id,
        )
        file_ids = [
            r["id"]
            for r in await conn.fetch(
                "SELECT id FROM files WHERE revision_id = $1 ORDER BY id", revision_id
            )
        ]
        assert len(file_ids) == 2
        for file_id in file_ids:
            await conn.execute(
                "INSERT INTO print_queue (model_id, position, file_id) VALUES ($1, $2, $3)",
                model_id,
                3,
                file_id,
            )

        # SlicerProfile (kind, name) uniqueness check.
        await conn.execute(
            "INSERT INTO slicer_profiles (name, kind, data) VALUES "
            "('A1 mini', 'printer', '{}'::jsonb)"
        )
        dupe_profile_failed = False
        try:
            await conn.execute(
                "INSERT INTO slicer_profiles (name, kind, data) VALUES "
                "('A1 mini', 'printer', '{}'::jsonb)"
            )
        except asyncpg.UniqueViolationError:
            dupe_profile_failed = True
        assert dupe_profile_failed
        # same name, different kind is fine.
        await conn.execute(
            "INSERT INTO slicer_profiles (name, kind, data) VALUES "
            "('A1 mini', 'process', '{}'::jsonb)"
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('slicer_profiles')") is None
        assert await conn.fetchval("select to_regclass('print_recipes')") is None
        assert (
            await conn.fetchval(
                "select column_name from information_schema.columns "
                "where table_name = 'print_queue' and column_name = 'state'"
            )
            is None
        )
        # non-bare rows were deleted; the bare row survives and model_id
        # UNIQUE is restored.
        remaining = await conn.fetch("SELECT model_id FROM print_queue")
        assert [r["model_id"] for r in remaining] == [model_id]
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _P6)
