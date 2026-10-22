"""P1 migration (``c5e2a8d4b7f1``): users/roles, groups, printer access, OIDC
providers, token ownership and ``created_by_user_id`` backfill."""

from __future__ import annotations

import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "f3a1c9d2e8b7"
_P1 = "c5e2a8d4b7f1"


async def test_p1_upgrade_backfills_admin_tokens_and_owners(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        admin_id = await conn.fetchval(
            "INSERT INTO users (username, password_hash) VALUES ('admin', 'h') RETURNING id"
        )
        await conn.execute("INSERT INTO api_tokens (token_hash, label) VALUES ('t', 'x')")
        await conn.execute(
            "INSERT INTO imports (url, site, state) VALUES ('u', 'thingiverse', 'pending')"
        )
        await conn.close()
        await _run_alembic(sa_url, command.upgrade, _P1)
        conn = await asyncpg.connect(dsn)
        assert await conn.fetchval("select role from users where username='admin'") == "admin"
        assert await conn.fetchval("select is_active from users") is True
        assert await conn.fetchval("select user_id from api_tokens") == admin_id
        assert await conn.fetchval("select created_by_user_id from imports") == admin_id
    finally:
        await conn.close()


async def test_p1_round_trip_with_passwordless_user(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, "head")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES ('oidc-only', NULL, 'member')"
        )
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval("select password_hash from users where username='oidc-only'") == "!"
        )
    finally:
        await conn.close()
    await _run_alembic(sa_url, command.upgrade, _P1)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select count(*) from groups") == 0
    finally:
        await conn.close()


async def test_p1_upgrade_on_empty_db_succeeds(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, "head")
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select count(*) from users") == 0
    finally:
        await conn.close()
