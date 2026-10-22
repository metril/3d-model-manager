"""First-run admin bootstrap (SPEC requirement 1).

``ensure_admin_user`` is exercised directly against the real Postgres
testcontainer (via ``db_session``) rather than through the app lifespan, so
these tests stay fast and focused on the idempotency/generation behavior
itself. ``test_main.py`` separately proves the lifespan actually calls it.
"""

import logging

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User
from app.models.enums import UserRole
from app.security import verify_password
from app.services.bootstrap import ensure_admin_user


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Every test in this module tweaks admin env vars, so isolate them."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_ensure_admin_user_creates_exactly_one_user(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret-startup-pw")
    get_settings.cache_clear()

    await ensure_admin_user(db_session)

    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 1
    user = (await db_session.execute(select(User))).scalar_one()
    assert user.username == "admin"
    assert verify_password("s3cret-startup-pw", user.password_hash) is True


async def test_ensure_admin_user_is_idempotent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret-startup-pw")
    get_settings.cache_clear()

    await ensure_admin_user(db_session)
    await ensure_admin_user(db_session)
    await ensure_admin_user(db_session)

    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 1


async def test_ensure_admin_user_generates_and_logs_password_once(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="app.services.bootstrap"):
        await ensure_admin_user(db_session)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1

    user = (await db_session.execute(select(User))).scalar_one()
    message = warnings[0].getMessage()
    assert "admin" in message

    # Pull the generated password back out of the log line and confirm it's
    # actually the one that was hashed into the user row.
    password_line = next(line for line in message.splitlines() if "password:" in line)
    generated_password = password_line.split("password:", 1)[1].strip()
    assert len(generated_password) >= 24
    assert verify_password(generated_password, user.password_hash) is True


async def test_bootstrap_still_logs_the_real_generated_password(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M6 A2: ``admin_password`` becomes a ``SecretStr``, but when it's
    unset a random one is generated and logged ONCE in cleartext -- the
    only recovery path. ``SecretStr`` must NOT mask THAT log (e.g. by some
    refactor accidentally routing it through ``str(secret_str)``, which
    would log the constant ``"**********"`` instead of a real, usable
    password)."""
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="app.services.bootstrap"):
        await ensure_admin_user(db_session)

    message = next(r for r in caplog.records if r.levelno == logging.WARNING).getMessage()
    assert "**********" not in message  # SecretStr's own repr mask must never leak in here
    password_line = next(line for line in message.splitlines() if "password:" in line)
    generated_password = password_line.split("password:", 1)[1].strip()

    user = (await db_session.execute(select(User))).scalar_one()
    assert verify_password(generated_password, user.password_hash) is True


async def test_ensure_admin_user_does_not_relog_on_restart(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second "startup" against an already-bootstrapped DB must not
    generate (or log) a new password.
    """
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="app.services.bootstrap"):
        await ensure_admin_user(db_session)
        caplog.clear()
        await ensure_admin_user(db_session)

    assert caplog.records == []


async def test_ensure_admin_user_creates_admin_role(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret-startup-pw")
    get_settings.cache_clear()
    await ensure_admin_user(db_session)
    assert (await db_session.scalar(select(User))).role == UserRole.ADMIN


async def test_ensure_admin_user_repromotes_existing_admin_username(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    get_settings.cache_clear()
    db_session.add(User(username="admin", password_hash="x", role=UserRole.VIEWER, is_active=False))
    await db_session.commit()
    await ensure_admin_user(db_session)
    await db_session.commit()
    u = await db_session.scalar(select(User).where(User.username == "admin"))
    await db_session.refresh(u)
    assert (u.role, u.is_active, u.password_hash) == (UserRole.ADMIN, True, "x")
    assert await db_session.scalar(select(func.count()).select_from(User)) == 1
