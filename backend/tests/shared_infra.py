"""One Postgres container per pytest session, shared by xdist workers.

The xdist *controller* starts Postgres before spawning workers and stops it at
exit; each worker only creates its own database (``tdmm_<worker>``). Redis is
NOT shared: pub/sub channels ignore the db index and printer ids repeat across
worker databases, so every worker keeps its own (non-persistent) Redis.
Without xdist (or if startup fails) ``conftest.py`` starts its own Postgres.

Postgres runs with durability off on tmpfs; Redis with persistence off.
"""

from __future__ import annotations

import contextlib
from typing import Any

import psycopg
from sqlalchemy.engine import make_url
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

PG_IMAGE = "postgres:16-alpine"
REDIS_IMAGE = "redis:7-alpine"

PG_FAST_ARGS = (
    "postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=500"
)
REDIS_FAST_ARGS = 'redis-server --save "" --appendonly no'


def make_postgres() -> PostgresContainer:
    return (
        PostgresContainer(PG_IMAGE)
        .with_command(PG_FAST_ARGS)
        .with_kwargs(tmpfs={"/var/lib/postgresql/data": "rw"})
    )


def make_redis() -> RedisContainer:
    return RedisContainer(REDIS_IMAGE).with_command(REDIS_FAST_ARGS)


_containers: list[Any] = []


def start_shared() -> dict[str, Any] | None:
    """Controller only: start the shared Postgres. None on failure."""
    try:
        pg = make_postgres()
        pg.start()
    except Exception:  # noqa: BLE001
        return None
    _containers.append(pg)
    return {
        "pg_admin_dsn": (
            f"postgresql://{pg.username}:{pg.password}@{pg.get_container_host_ip()}"
            f":{pg.get_exposed_port(pg.port)}/{pg.dbname}"
        ),
    }


def stop_shared() -> None:
    while _containers:
        with contextlib.suppress(Exception):
            _containers.pop().stop()


def worker_postgres_url(info: dict[str, Any], worker_id: str) -> str:
    """Create this worker's database; return its asyncpg URL."""
    name = f"tdmm_{worker_id}"
    with psycopg.connect(info["pg_admin_dsn"], autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    url = make_url(info["pg_admin_dsn"]).set(drivername="postgresql+asyncpg", database=name)
    return url.render_as_string(hide_password=False)
