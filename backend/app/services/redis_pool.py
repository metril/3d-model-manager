"""Shared pooled Redis clients.

Call sites keep the ``client = get_*_redis(url); try: ... finally: aclose()``
shape, but every client wraps one process-wide ``ConnectionPool`` so a call no
longer opens (and tears down) its own TCP connection. A client built on an
external pool never disconnects that pool on ``close()``/``aclose()``.

Async pools are bound to the running event loop, so they are keyed by loop;
sync pools are keyed by pid (fork-safe under Celery prefork).
"""

from __future__ import annotations

import asyncio
import os

import redis
import redis.asyncio as aioredis

_async_pools: dict[tuple, aioredis.ConnectionPool] = {}
_async_loops: dict[int, asyncio.AbstractEventLoop] = {}
_sync_pools: dict[tuple, redis.ConnectionPool] = {}


def _drop_loop(lid: int) -> None:
    _async_loops.pop(lid, None)
    for key in [k for k in _async_pools if k[0] == lid]:
        pool = _async_pools.pop(key)
        try:  # the loop is gone/closed: drop sockets without awaiting
            for conn in list(getattr(pool, "_available_connections", [])) + list(
                getattr(pool, "_in_use_connections", [])
            ):
                transport = getattr(getattr(conn, "_writer", None), "transport", None)
                if transport is not None:
                    transport.abort()
        except Exception:  # noqa: BLE001 -- best effort
            pass


def _prune_closed_loops() -> None:
    for lid, loop in list(_async_loops.items()):
        if loop.is_closed():
            _drop_loop(lid)


def reset_pools() -> None:
    """Forget every pool (test isolation)."""
    for lid in list(_async_loops):
        _drop_loop(lid)
    _async_pools.clear()
    for pool in _sync_pools.values():
        pool.disconnect()
    _sync_pools.clear()


def get_async_redis(url: str, **kwargs) -> aioredis.Redis:
    loop = asyncio.get_running_loop()
    lid = id(loop)
    if _async_loops.get(lid) is not loop:  # new loop, or a dead one's recycled id
        if lid in _async_loops:
            _drop_loop(lid)
        _prune_closed_loops()
        _async_loops[lid] = loop
    key = (lid, url, tuple(sorted(kwargs.items())))
    pool = _async_pools.get(key)
    if pool is None:
        pool = aioredis.ConnectionPool.from_url(url, **kwargs)
        _async_pools[key] = pool
    return aioredis.Redis(connection_pool=pool)


def get_sync_redis(url: str, **kwargs) -> redis.Redis:
    key = (os.getpid(), url, tuple(sorted(kwargs.items())))
    pool = _sync_pools.get(key)
    if pool is None:
        pool = redis.ConnectionPool.from_url(url, **kwargs)
        _sync_pools[key] = pool
    return redis.Redis(connection_pool=pool)
