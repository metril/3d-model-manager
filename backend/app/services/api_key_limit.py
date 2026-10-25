"""Per-key fixed-window rate limit (requests/minute) on Redis. Fails open if
Redis is unreachable -- the limiter must never take the API down."""

from __future__ import annotations

import time

import redis.asyncio as aioredis

WINDOW_S = 60


async def hit(redis_url: str, key_id: int, limit: int) -> tuple[bool, int, int]:
    """Count one request. Returns ``(allowed, remaining, retry_after_s)``."""
    now = int(time.time())
    window = now // WINDOW_S
    retry = WINDOW_S - now % WINDOW_S
    client = aioredis.Redis.from_url(redis_url, socket_timeout=2, socket_connect_timeout=2)
    try:
        k = f"apikey:rl:{key_id}:{window}"
        count = await client.incr(k)
        if count == 1:
            await client.expire(k, WINDOW_S + 5)
    except Exception:  # noqa: BLE001 -- fail open
        return True, limit, 0
    finally:
        await client.aclose()
    return count <= limit, max(0, limit - count), retry
