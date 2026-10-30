"""Failed-login throttle: Redis counters per username and per client IP.

Checked before the (expensive) password verification; only failures count and
a success clears the username counter. Fails open if Redis is unreachable so
the throttle can never lock everyone out.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from redis.asyncio import Redis

from app.config import get_settings

USER_LIMIT = 10
IP_LIMIT = 50
WINDOW_S = 900


def _user_key(username: str) -> str:
    return f"tdmm:login:fail:u:{username.strip().lower()}"


def _ip_key(ip: str) -> str:
    return f"tdmm:login:fail:ip:{ip}"


def _client() -> Redis:
    return Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)


async def guard(username: str, ip: str) -> None:
    """429 + ``Retry-After`` once either counter has reached its limit."""
    r = _client()
    try:
        for key, limit in ((_user_key(username), USER_LIMIT), (_ip_key(ip), IP_LIMIT)):
            if int(await r.get(key) or 0) >= limit:
                ttl = await r.ttl(key)
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "too many failed logins; try again later",
                    headers={"Retry-After": str(ttl if ttl > 0 else WINDOW_S)},
                )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 -- fail open
        return
    finally:
        await r.aclose()


async def note_failure(username: str, ip: str) -> None:
    r = _client()
    try:
        for key in (_user_key(username), _ip_key(ip)):
            if await r.incr(key) == 1:
                await r.expire(key, WINDOW_S)
    except Exception:  # noqa: BLE001 -- fail open
        return
    finally:
        await r.aclose()


async def clear(username: str) -> None:
    r = _client()
    try:
        await r.delete(_user_key(username))
    except Exception:  # noqa: BLE001 -- fail open
        return
    finally:
        await r.aclose()
