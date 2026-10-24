import asyncpg

from alembic import command
from tests.migration_helpers import _run_alembic

_PREV = "c9d4e6f8b2a1"
_T6 = "d2e7a1c5b903"


async def test_t6_upgrade_downgrade_upgrade(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('user_totp')") is None
        await conn.execute("INSERT INTO users (username) VALUES ('alice')")
        await conn.execute(
            "INSERT INTO oidc_providers (name, slug, discovery_url, client_id, client_secret_enc) "
            "VALUES ('p', 'p', 'https://x', 'c', 'e')"
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T6)

    conn = await asyncpg.connect(dsn)
    try:
        # backfill defaults on existing rows
        assert await conn.fetchval("SELECT email_otp_enabled FROM users") is False
        assert await conn.fetchval("SELECT trusts_idp_mfa FROM oidc_providers") is False
        alice = await conn.fetchval("SELECT id FROM users")

        await conn.execute(
            "INSERT INTO user_totp (user_id, secret_enc, created_by_user_id) VALUES ($1, 'e', $1)",
            alice,
        )
        row = await conn.fetchrow(
            "SELECT is_enabled, backup_codes_json, last_totp_counter, created_at FROM user_totp"
        )
        assert row["is_enabled"] is False and row["last_totp_counter"] == 0
        assert row["backup_codes_json"] == "[]" and row["created_at"] is not None

        await conn.execute(
            "INSERT INTO user_otp_codes (user_id, code_hash, expires_at) VALUES ($1, 'h', now())",
            alice,
        )
        assert await conn.fetchval("SELECT attempts FROM user_otp_codes") == 0
        await conn.execute(
            "INSERT INTO auth_ephemeral_tokens (token_hash, token_type, user_id, expires_at) "
            "VALUES ('h1', 'pre_auth', $1, now())",
            alice,
        )

        for sql in (
            "INSERT INTO user_totp (user_id, secret_enc) VALUES ($1, 'dup')",
            "INSERT INTO auth_ephemeral_tokens (token_hash, token_type, expires_at) "
            "VALUES ('h1', 'pre_auth', now())",
            "INSERT INTO auth_ephemeral_tokens (token_hash, token_type, expires_at) "
            "VALUES ('h2', 'bogus', now())",
        ):
            args = (alice,) if "$1" in sql else ()
            try:
                await conn.execute(sql, *args)
            except (asyncpg.UniqueViolationError, asyncpg.CheckViolationError):
                pass
            else:
                raise AssertionError(f"expected a constraint violation: {sql}")

        # deleting the user cascades; created_by SET NULL is covered by the FK def
        await conn.execute("DELETE FROM users WHERE id = $1", alice)
        for table in ("user_totp", "user_otp_codes", "auth_ephemeral_tokens"):
            assert await conn.fetchval(f"SELECT count(*) FROM {table}") == 0
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        for table in ("user_totp", "user_otp_codes", "auth_ephemeral_tokens"):
            assert await conn.fetchval(f"select to_regclass('{table}')") is None
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE column_name IN ('email_otp_enabled', 'trusts_idp_mfa')"
        )
        assert cols == []
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _T6)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("select to_regclass('user_totp')") is not None
    finally:
        await conn.close()
