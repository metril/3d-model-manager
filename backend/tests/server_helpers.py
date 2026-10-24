"""Test helper: merge fields into the DB-backed ``server`` settings row
(bypasses ServerConfig validation on purpose, e.g. tiny caps)."""

import json

from sqlalchemy import text

from app.db import get_sessionmaker


async def set_server(**fields) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(
            text(
                "INSERT INTO settings (key, value) VALUES ('server', CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO UPDATE SET value = settings.value || EXCLUDED.value"
            ),
            {"v": json.dumps(fields)},
        )
        await s.commit()
