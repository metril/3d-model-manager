"""One Postgres + one Redis container per pytest session, shared by xdist workers.

The xdist *controller* starts both containers (in parallel) before spawning
workers and stops them at exit, so each worker only creates its own database
(``tdmm_<worker>``) and uses its own Redis db index instead of paying ~5s for
its own pair of containers. Without xdist (or if startup fails) the per-process
fixtures in ``conftest.py`` start their own containers as before.

Postgres runs with durability off on tmpfs; Redis with persistence off.
"""

from __future__ import annotations

import threading
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
REDIS_FAST_ARGS = 'redis-server --save "" --appendonly no --databases 1024'


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
    """Controller only: start both containers concurrently. None on failure."""
    results: dict[str, Any] = {}
    errors: list[BaseException] = []

    def run(name: str, make: Any) -> None:
        try:
            c = make()
            c.start()
            _containers.append(c)
            results[name] = c
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("pg", make_postgres)),
        threading.Thread(target=run, args=("redis", make_redis)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        stop_shared()
        return None
    pg, rd = results["pg"], results["redis"]
    return {
        "pg_admin_dsn": (
            f"postgresql://{pg.username}:{pg.password}@{pg.get_container_host_ip()}"
            f":{pg.get_exposed_port(pg.port)}/{pg.dbname}"
        ),
        "redis_base": f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(rd.port)}",
    }


def stop_shared() -> None:
    while _containers:
        try:
            _containers.pop().stop()
        except Exception:  # noqa: BLE001
            pass


def worker_postgres_url(info: dict[str, Any], worker_id: str) -> str:
    """Create this worker's database; return its asyncpg URL."""
    name = f"tdmm_{worker_id}"
    with psycopg.connect(info["pg_admin_dsn"], autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    url = make_url(info["pg_admin_dsn"]).set(drivername="postgresql+asyncpg", database=name)
    return url.render_as_string(hide_password=False)


def worker_redis_url(info: dict[str, Any], worker_id: str) -> str:
    digits = "".join(ch for ch in worker_id if ch.isdigit()) or "0"
    return f"{info['redis_base']}/{int(digits) % 1024}"
