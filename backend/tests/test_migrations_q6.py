import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "b2f7c1a9d4e3"
_Q6 = "c6a1f4d8b2e7"


async def test_q6_upgrade_creates_notification_tables_and_downgrades(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    await _run_alembic(sa_url, command.upgrade, _Q6)

    conn = await asyncpg.connect(dsn)
    try:
        cid = await conn.fetchval(
            "INSERT INTO notification_channels (kind, name, config_enc, events) "
            "VALUES ('ntfy', 'n', 'enc', ARRAY['job_finished']) RETURNING id"
        )
        assert await conn.fetchval("SELECT enabled FROM notification_channels") is True
        await conn.execute(
            "INSERT INTO notification_log (channel_id, event, status) VALUES ($1, 'x', 'sent')",
            cid,
        )
        await conn.execute(
            "INSERT INTO notification_templates (event, title_tpl, body_tpl) VALUES ('e', 't', 'b')"
        )
        await conn.execute("DELETE FROM notification_channels WHERE id = $1", cid)
        assert await conn.fetchval("SELECT channel_id FROM notification_log") is None
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('notification_log')") is None
    finally:
        await conn.close()
