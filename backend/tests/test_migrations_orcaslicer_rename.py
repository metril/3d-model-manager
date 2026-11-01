import json

import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "b5c9d2e7f3a4"
_REV = "c7d1e3a5b9f2"

_OLD = {
    "public_url": "https://x",
    "studio_enabled": True,
    "studio_url": "http://studio:3000",
    "studio_launcher_url": "http://custom:3010",
    "studio_launcher_token": "enc-token",
    "_seeded_from_env": ["studio_enabled", "public_url"],
}
_NEW = {
    "public_url": "https://x",
    "orcaslicer_enabled": True,
    "orcaslicer_url": "http://orcaslicer:3000",
    "orcaslicer_launcher_url": "http://custom:3010",
    "orcaslicer_launcher_token": "enc-token",
    "_seeded_from_env": ["orcaslicer_enabled", "public_url"],
}


async def _get(dsn: str):
    conn = await asyncpg.connect(dsn)
    try:
        raw = await conn.fetchval("select value from settings where key='server'")
        return json.loads(raw) if isinstance(raw, str) else raw
    finally:
        await conn.close()


async def test_orcaslicer_rename_round_trip(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "insert into settings (key, value) values ('server', $1::jsonb)", json.dumps(_OLD)
        )
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _REV)
    assert await _get(dsn) == _NEW
    await _run_alembic(sa_url, command.downgrade, _PREV)
    assert await _get(dsn) == _OLD
