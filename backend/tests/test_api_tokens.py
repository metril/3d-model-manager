"""Service-level coverage for ``app.services.api_tokens`` (M10 Workstream
A): mint/verify/list/revoke against the ``api_tokens`` table. The API-level
contracts (``/ext/*`` bearer auth, session-gated management endpoints) are
covered separately in ``test_ext_api.py``/``test_api_tokens_settings_api.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import ApiToken
from app.services import api_tokens


async def test_mint_returns_usable_plaintext_and_persists_only_a_hash(db_session, admin_user):
    token, row = await api_tokens.mint(db_session, user_id=admin_user.id, label="Chrome extension")

    assert isinstance(token, str) and len(token) > 20
    assert row.label == "Chrome extension"
    assert row.token_hash != token  # never the plaintext at rest
    assert row.token_hash == api_tokens._hash(token)


async def test_verify_accepts_the_real_token(db_session, admin_user):
    token, row = await api_tokens.mint(db_session, user_id=admin_user.id, label="ext")

    found = await api_tokens.verify(db_session, token)

    assert found is not None and found.id == row.id


async def test_verify_rejects_a_wrong_token(db_session, admin_user):
    await api_tokens.mint(db_session, user_id=admin_user.id, label="ext")

    assert await api_tokens.verify(db_session, "not-the-real-token") is None


async def test_verify_sets_last_used_at(db_session, admin_user):
    token, row = await api_tokens.mint(db_session, user_id=admin_user.id, label="ext")
    assert row.last_used_at is None

    await api_tokens.verify(db_session, token)

    await db_session.refresh(row)
    assert row.last_used_at is not None


async def test_verify_throttles_last_used_at_bump(db_session, admin_user):
    """A second verify within LAST_USED_THROTTLE must not move
    ``last_used_at`` again -- mirrors ``app.api.deps.LAST_SEEN_THROTTLE``."""
    token, row = await api_tokens.mint(db_session, user_id=admin_user.id, label="ext")
    await api_tokens.verify(db_session, token)
    await db_session.refresh(row)
    first_seen = row.last_used_at

    await api_tokens.verify(db_session, token)
    await db_session.refresh(row)

    assert row.last_used_at == first_seen


async def test_verify_bumps_last_used_at_again_once_stale(db_session, admin_user):
    token, row = await api_tokens.mint(db_session, user_id=admin_user.id, label="ext")
    await api_tokens.verify(db_session, token)
    await db_session.refresh(row)

    # Simulate the throttle window having elapsed.
    row.last_used_at = datetime.now(UTC) - timedelta(seconds=120)
    await db_session.commit()

    await api_tokens.verify(db_session, token)
    await db_session.refresh(row)

    assert row.last_used_at > datetime.now(UTC) - timedelta(seconds=5)


async def test_list_tokens_orders_by_created_at_desc(db_session, admin_user):
    _, first = await api_tokens.mint(db_session, user_id=admin_user.id, label="first")
    _, second = await api_tokens.mint(db_session, user_id=admin_user.id, label="second")

    rows = await api_tokens.list_tokens(db_session)

    assert [r.id for r in rows] == [second.id, first.id]


async def test_revoke_removes_the_token(db_session, admin_user):
    token, row = await api_tokens.mint(db_session, user_id=admin_user.id, label="ext")

    deleted = await api_tokens.revoke(db_session, row.id)

    assert deleted is True
    assert await api_tokens.verify(db_session, token) is None
    assert await db_session.get(ApiToken, row.id) is None


async def test_revoke_unknown_id_returns_false(db_session):
    assert await api_tokens.revoke(db_session, 999999) is False
