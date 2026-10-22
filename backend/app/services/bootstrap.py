"""First-run admin bootstrap (SPEC requirement 1: single admin account).

Called from the app lifespan (``app.main``) so it runs once per process
startup. Lives here rather than inline in ``main.py`` so the same logic can
be reused by the Celery worker process added in a later task, which needs
an equivalent async-context entry point but no FastAPI app around it.
"""

import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User
from app.models.enums import UserRole
from app.security import hash_password

logger = logging.getLogger(__name__)

# token_urlsafe(24) always yields exactly 32 base64url characters (24 is a
# multiple of 3, so there's no padding to strip), comfortably over the
# "24+ char" floor.
_GENERATED_PASSWORD_BYTES = 24


async def ensure_admin_user(session: AsyncSession) -> None:
    """Create the admin user when no user exists; otherwise re-promote it.

    Idempotent: with users present, the account named ``admin_username`` (if
    any) is forced to ``ADMIN`` + active (lockout recovery) and its password
    is left alone; no second account is ever created.
    """
    settings = get_settings()
    user_count = await session.scalar(select(func.count()).select_from(User))
    if user_count:
        existing = await session.scalar(
            select(User).where(User.username == settings.admin_username)
        )
        if existing is not None and (existing.role != UserRole.ADMIN or not existing.is_active):
            existing.role = UserRole.ADMIN
            existing.is_active = True
            await session.commit()
        return

    configured = settings.admin_password
    generated = configured is None
    password = (
        secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)
        if generated
        else configured.get_secret_value()
    )

    user = User(
        username=settings.admin_username,
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
    )
    session.add(user)
    await session.commit()

    if generated:
        logger.warning(
            "\n"
            + "=" * 72
            + "\nNo ADMIN_PASSWORD set - generated a random admin password.\n"
            + f"    username: {settings.admin_username}\n"
            + f"    password: {password}\n"
            + "This password is shown ONLY this once and is not recoverable; save it now.\n"
            + "=" * 72
        )
