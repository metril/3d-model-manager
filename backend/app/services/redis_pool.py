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


def _prune_closed_loops() -> None:
    for lid, loop in list(_async_loops.items()):
        if loop.is_closed():
            _async_loops.pop(lid, None)
            for key in [k for k in _async_pools if k[0] == lid]:
                _async_pools.pop(key, None)  # loop is gone; connections die with it


def get_async_redis(url: str, **kwargs) -> aioredis.Redis:
    loop = asyncio.get_running_loop()
    lid = id(loop)
    if lid not in _async_loops:
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
