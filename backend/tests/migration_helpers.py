"""Alembic-driven migration test helpers: ``_run_alembic`` and a private
``scratch_db`` (a throwaway database in the shared Postgres container, so
alembic DDL never contends with the session-scoped connections). Registered
as a pytest plugin in ``conftest.pytest_plugins``."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import anyio
import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


async def _run_alembic(url: str, fn, revision: str) -> None:
    """Point ``app.config.get_settings().database_url`` (what
    ``alembic/env.py`` actually reads -- it ignores the Config object's own
    ``sqlalchemy.url``) at ``url`` for the duration of one alembic command,
    then restore it. ``fn`` runs in a worker thread since alembic's own
    ``env.py`` does ``asyncio.run(...)`` internally, which can't nest inside
    this (already-running) async test's event loop.
    """
    original = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        await anyio.to_thread.run_sync(fn, _config(), revision)
    finally:
        if original is not None:
            os.environ["DATABASE_URL"] = original
        else:
            os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()


@pytest.fixture
async def scratch_db(postgres_url: str):
    """A throwaway, unmigrated database in the same Postgres container,
    dropped again on teardown. Yields its asyncpg SQLAlchemy URL (for
    pointing alembic at) alongside a plain DSN (for direct asyncpg use).
    """
    base = make_url(postgres_url)
    name = f"r13c_mig_{uuid.uuid4().hex[:12]}"
    admin_dsn = base.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )

    admin_conn = await asyncpg.connect(admin_dsn)
    try:
        await admin_conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin_conn.close()

    sa_url = base.set(database=name).render_as_string(hide_password=False)
    dsn = base.set(drivername="postgresql", database=name).render_as_string(hide_password=False)
    try:
        yield sa_url, dsn
    finally:
        admin_conn = await asyncpg.connect(admin_dsn)
        try:
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin_conn.close()
