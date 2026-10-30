"""Failed-login throttle: Redis counters per username and per client IP.

The per-username counter is checked before the (expensive) password
verification. The per-IP counter is NOT a pre-check: it only turns a *wrong*
password into a 429 once the IP has racked up ``IP_LIMIT`` failures, so a
correct password always gets through. That matters behind a reverse proxy:
unless uvicorn trusts the proxy's ``X-Forwarded-For`` every request carries
the proxy's IP, and a pre-check would let anyone lock every user out. Set
``FORWARDED_ALLOW_IPS`` (uvicorn; e.g. the proxy container's address or
``*`` on a private network) so ``request.client.host`` is the real client.

Only failures count and a success clears the username counter. Fails open if
Redis is unreachable so the throttle can never lock everyone out.
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


def _too_many(ttl: int) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "too many failed logins; try again later",
        headers={"Retry-After": str(ttl if ttl > 0 else WINDOW_S)},
    )


async def guard(username: str) -> None:
    """429 + ``Retry-After`` once the username counter has reached its limit."""
    r = _client()
    try:
        key = _user_key(username)
        if int(await r.get(key) or 0) >= USER_LIMIT:
            raise _too_many(await r.ttl(key))
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 -- fail open
        return
    finally:
        await r.aclose()


async def note_failure(username: str, ip: str) -> None:
    """Count a failed login; raise 429 instead of 401 once the IP is over limit."""
    r = _client()
    ip_count = 0
    try:
        for key in (_user_key(username), _ip_key(ip)):
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, WINDOW_S)
            ip_count = n
        if ip_count > IP_LIMIT:
            raise _too_many(await r.ttl(_ip_key(ip)))
    except HTTPException:
        raise
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
