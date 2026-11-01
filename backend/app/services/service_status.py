"""Unified admin service-health probe (``GET /admin/status``).

Every probe returns a ``ServiceStatus``; ``state`` is ``ok`` / ``warn`` /
``down`` / ``off`` (``off`` = deliberately unused, never counted as a problem).
Probes run concurrently, each under a hard timeout, and the whole result is
cached in Redis for ``CACHE_TTL_S`` so a polling UI cannot hammer the sidecars.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Literal

import anyio
import httpx
import redis.asyncio as aioredis
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import get_sessionmaker
from app.services import obico, spoolman
from app.services.app_config import get_app_config
from app.services.ha_config import get_ha_config, parse_mqtt_url
from app.services.mqtt_relay_config import STATUS_KEY as RELAY_STATUS_KEY
from app.services.mqtt_relay_config import get_relay_config
from app.services.server_config import ServerConfig, get_server_config

BEAT_KEY = "tdmm:hb:beat"
PRINTERD_KEY = "tdmm:hb:printerd"
VP_STATUS_KEY = "tdmm:vp:status"
CACHE_KEY = "tdmm:admin:status"
CACHE_TTL_S = 15
PROBE_TIMEOUT_S = 4.0
BEAT_STALE_S = 90.0
PRINTERD_STALE_S = 30.0

State = Literal["ok", "warn", "down", "off"]
ORDER = (
    "db",
    "redis",
    "worker_io",
    "worker_cpu",
    "beat",
    "printerd",
    "slicer",
    "orcaslicer",
    "obico",
    "virtual_printer",
    "spoolman",
    "home_assistant",
    "mqtt_relay",
    "smtp",
)


class ServiceStatus(BaseModel):
    ok: bool
    state: State
    detail: str = ""
    version: str | None = None
    checked_at: float = 0.0


class AdminStatusOut(BaseModel):
    overall: Literal["ok", "warn", "down"]
    checked_at: float
    services: dict[str, ServiceStatus]


def _st(state: State, detail: str = "", version: str | None = None) -> ServiceStatus:
    return ServiceStatus(ok=state == "ok", state=state, detail=detail, version=version)


async def _http_ok(url: str, timeout: float = 3.0) -> tuple[bool, str, httpx.Response | None]:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(url)
    except httpx.TimeoutException:
        return False, "timed out", None
    except httpx.HTTPError as exc:
        return False, type(exc).__name__, None
    if resp.status_code == 200:
        return True, "ok", resp
    return False, f"HTTP {resp.status_code}", resp


async def _tcp(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except TimeoutError:
        return False, "timed out"
    except OSError as exc:
        return False, type(exc).__name__
    w.close()
    with contextlib.suppress(Exception):
        await w.wait_closed()
    return True, f"{host}:{port} reachable"


def _age(raw: bytes | str | None) -> tuple[float | None, dict]:
    """(seconds since the heartbeat's ``ts``, body) -- (None, {}) when absent/garbled."""
    if not raw:
        return None, {}
    try:
        body = json.loads(raw)
        return max(0.0, time.time() - float(body["ts"])), body
    except (ValueError, KeyError, TypeError):
        return None, {}


# -- individual probes ----------------------------------------------------


async def _db() -> dict[str, ServiceStatus]:
    async with get_sessionmaker()() as s:
        await s.execute(text("select 1"))
        ver = (await s.execute(text("show server_version"))).scalar()
    return {"db": _st("ok", "ok", str(ver).split(" ")[0] if ver else None)}


async def _redis(r: aioredis.Redis) -> dict[str, ServiceStatus]:
    await r.ping()
    info = await r.info("server")
    return {"redis": _st("ok", "ok", str(info.get("redis_version") or "") or None)}


def _inspect_workers() -> dict[str, set[str]]:
    from app.tasks.celery_app import celery_app

    insp = celery_app.control.inspect(timeout=1.0)
    pong = insp.ping() or {}
    queues = insp.active_queues() or {}
    return {name: {q.get("name", "") for q in (queues.get(name) or [])} for name in pong}


async def _workers() -> dict[str, ServiceStatus]:
    workers = await anyio.to_thread.run_sync(_inspect_workers)
    out: dict[str, ServiceStatus] = {}
    for key, queue in (("worker_io", "io"), ("worker_cpu", "cpu")):
        n = sum(1 for qs in workers.values() if queue in qs)
        out[key] = (
            _st("ok", f"{n} worker(s) consuming '{queue}'")
            if n
            else _st("down", f"no worker answered for queue '{queue}'")
        )
    return out


async def _beat(r: aioredis.Redis) -> dict[str, ServiceStatus]:
    age, _ = _age(await r.get(BEAT_KEY))
    if age is None:
        return {"beat": _st("down", "no heartbeat recorded")}
    if age > BEAT_STALE_S:
        return {"beat": _st("down", f"last tick {int(age)}s ago")}
    return {"beat": _st("ok", f"last tick {int(age)}s ago")}


async def _printerd(r: aioredis.Redis, printers_on: bool) -> dict[str, ServiceStatus]:
    age, body = _age(await r.get(PRINTERD_KEY))
    if age is None or age > PRINTERD_STALE_S:
        if not printers_on:
            return {"printerd": _st("off", "printer support disabled")}
        return {
            "printerd": _st(
                "down", "no heartbeat" if age is None else f"last heartbeat {int(age)}s ago"
            )
        }
    return {"printerd": _st("ok", f"{body.get('workers', 0)} printer worker(s)")}


async def _slicer(cfg: ServerConfig) -> dict[str, ServiceStatus]:
    if not cfg.slicer_enabled:
        return {"slicer": _st("off", "disabled")}
    if not cfg.slicer_url:
        return {"slicer": _st("down", "not configured")}
    ok, detail, resp = await _http_ok(f"{cfg.slicer_url.rstrip('/')}/health")
    ver = None
    if ok and resp is not None:
        with contextlib.suppress(ValueError, AttributeError):
            ver = (resp.json() or {}).get("version")
    return {"slicer": _st("ok" if ok else "down", detail, ver)}


async def _orcaslicer(cfg: ServerConfig) -> dict[str, ServiceStatus]:
    if not cfg.orcaslicer_enabled:
        return {"orcaslicer": _st("off", "disabled")}
    if not cfg.orcaslicer_launcher_url:
        return {"orcaslicer": _st("down", "not configured")}
    headers = {}
    if cfg.orcaslicer_launcher_token is not None:
        headers["X-Launcher-Token"] = cfg.orcaslicer_launcher_token.get_secret_value()
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
            resp = await client.get(
                f"{cfg.orcaslicer_launcher_url.rstrip('/')}/health", headers=headers
            )
    except httpx.TimeoutException:
        return {"orcaslicer": _st("down", "timed out")}
    except httpx.HTTPError as exc:
        return {"orcaslicer": _st("down", type(exc).__name__)}
    if resp.status_code != 200:
        return {"orcaslicer": _st("down", f"HTTP {resp.status_code}")}
    body: dict = {}
    with contextlib.suppress(ValueError):
        parsed = resp.json()
        if isinstance(parsed, dict):
            body = parsed
    if body.get("orcaslicer_running"):
        return {"orcaslicer": _st("ok", "running")}
    return {"orcaslicer": _st("warn", "launcher up, OrcaSlicer not running")}


async def _obico(
    r: aioredis.Redis, cfg: obico.ObicoConfig, ml_version: str
) -> dict[str, ServiceStatus]:
    ver = ml_version or None
    if not cfg.enabled:
        return {"obico": _st("off", "disabled", ver)}
    t0 = time.monotonic()
    ok, detail, _ = await _http_ok(f"{cfg.url}/hc/")
    if not ok:
        return {"obico": _st("down", detail, ver)}
    budget = max(0.3, PROBE_TIMEOUT_S - 0.3 - (time.monotonic() - t0))
    # The service answered; a slow/failed watcher scan must not read as "down".
    bad = 0
    try:
        with anyio.fail_after(budget):
            keys = [k async for k in r.scan_iter(match="printer:*:obico", count=500)]
            raws = await r.mget(keys) if keys else []
    except (TimeoutError, aioredis.RedisError):
        return {"obico": _st("warn", "reachable; watcher status unavailable", ver)}
    for raw in raws:
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            continue
        if isinstance(body, dict) and (body.get("paused") or body.get("last_error")):
            bad += 1
    if bad:
        return {"obico": _st("warn", f"{bad} printer watcher(s) failing", ver)}
    return {"obico": _st("ok", "ok", ver)}


async def _vp(r: aioredis.Redis) -> dict[str, ServiceStatus]:
    try:
        data = json.loads(await r.get(VP_STATUS_KEY) or "null")
    except ValueError:
        data = None
    state = data.get("state") if isinstance(data, dict) else None
    if state in ("idle", "running"):
        return {"virtual_printer": _st("ok", state)}
    if state == "error":
        errs = "; ".join(str(e) for e in (data.get("errors") or [])[:3])
        return {"virtual_printer": _st("down", errs or "error")}
    return {"virtual_printer": _st("down", "no status (service not running)")}


async def _spoolman(cfg: spoolman.SpoolmanConfig) -> dict[str, ServiceStatus]:
    if not cfg.active:
        return {"spoolman": _st("off", "disabled")}
    ok, detail, resp = await _http_ok(f"{cfg.url}/api/v1/info")
    ver = None
    if ok and resp is not None:
        with contextlib.suppress(ValueError, AttributeError):
            ver = (resp.json() or {}).get("version")
    return {"spoolman": _st("ok" if ok else "down", detail, ver)}


async def _ha(ha) -> dict[str, ServiceStatus]:  # noqa: ANN001
    if ha.ha_url and ha.ha_token:
        from app.services.ha_client import HaClient, HaUnreachable

        def _ping() -> str:
            c = HaClient(ha.ha_url, ha.ha_token)
            try:
                return c.ping()
            finally:
                c.close()

        try:
            msg = await anyio.to_thread.run_sync(_ping)
        except HaUnreachable:
            return {"home_assistant": _st("down", "unreachable")}
        except Exception as exc:  # noqa: BLE001 -- type only
            return {"home_assistant": _st("down", type(exc).__name__)}
        return {"home_assistant": _st("ok", msg or "ok")}
    if ha.ha_mqtt_url:
        try:
            host, port, _tls = parse_mqtt_url(ha.ha_mqtt_url)
        except ValueError:
            return {"home_assistant": _st("down", "invalid MQTT URL")}
        ok, detail = await _tcp(host, port)
        return {"home_assistant": _st("ok" if ok else "down", f"MQTT {detail}")}
    return {"home_assistant": _st("off", "not configured")}


async def _relay(r: aioredis.Redis, cfg) -> dict[str, ServiceStatus]:  # noqa: ANN001
    if not cfg.active:
        return {"mqtt_relay": _st("off", "not configured" if not cfg.host else "disabled")}
    try:
        body = json.loads(await r.get(RELAY_STATUS_KEY) or "null")
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return {"mqtt_relay": _st("down", "no status (printerd not running)")}
    where = f"{cfg.host}:{cfg.port}"
    if not body.get("connected"):
        return {"mqtt_relay": _st("down", str(body.get("error") or "not connected"))}
    if cfg.commands_enabled and not body.get("actor_ok", True):
        return {"mqtt_relay": _st("warn", f"{where}; {body.get('actor_error') or 'service user'}")}
    return {"mqtt_relay": _st("ok", f"connected to {where}")}


async def _smtp(cfg: ServerConfig) -> dict[str, ServiceStatus]:
    smtp = cfg.smtp
    if not smtp.configured:
        return {"smtp": _st("off", "not configured")}
    ok, detail = await _tcp(smtp.host, smtp.port)
    return {"smtp": _st("ok" if ok else "down", detail)}


# -- orchestration --------------------------------------------------------


async def _guard(
    names: tuple[str, ...], fn: Callable[[], Awaitable[dict[str, ServiceStatus]]]
) -> dict[str, ServiceStatus]:
    try:
        with anyio.fail_after(PROBE_TIMEOUT_S):
            return await fn()
    except TimeoutError:
        return {n: _st("down", "probe timed out") for n in names}
    except Exception as exc:  # noqa: BLE001 -- type only: never echo URLs/secrets
        return {n: _st("down", type(exc).__name__) for n in names}


async def collect(db: AsyncSession, r: aioredis.Redis, settings: Settings) -> AdminStatusOut:
    try:
        with anyio.fail_after(PROBE_TIMEOUT_S):
            server = await get_server_config(db)
            ocfg = await obico.get_config(db, settings)
            sp = await spoolman.get_config(db)
            ha = await get_ha_config(db, settings)
            app_cfg = await get_app_config(db, settings)
            relay_cfg = await get_relay_config(db, settings)
    except TimeoutError:
        now = time.time()
        down = _st("down", "config load timed out")
        down.checked_at = now
        return AdminStatusOut(overall="down", checked_at=now, services={"db": down})
    ml = settings.obico_ml_version.strip()
    jobs: list[tuple[tuple[str, ...], Callable[[], Awaitable[dict[str, ServiceStatus]]]]] = [
        (("db",), _db),
        (("redis",), lambda: _redis(r)),
        (("worker_io", "worker_cpu"), _workers),
        (("beat",), lambda: _beat(r)),
        (("printerd",), lambda: _printerd(r, app_cfg.printer_enabled)),
        (("slicer",), lambda: _slicer(server)),
        (("orcaslicer",), lambda: _orcaslicer(server)),
        (("obico",), lambda: _obico(r, ocfg, ml)),
        (("virtual_printer",), lambda: _vp(r)),
        (("spoolman",), lambda: _spoolman(sp)),
        (("home_assistant",), lambda: _ha(ha)),
        (("mqtt_relay",), lambda: _relay(r, relay_cfg)),
        (("smtp",), lambda: _smtp(server)),
    ]
    results = await asyncio.gather(*(_guard(n, f) for n, f in jobs))
    now = time.time()
    merged: dict[str, ServiceStatus] = {}
    for part in results:
        merged.update(part)
    for s in merged.values():
        s.checked_at = now
    services = {k: merged[k] for k in ORDER if k in merged}
    states = {s.state for s in services.values()}
    overall = "down" if "down" in states else "warn" if "warn" in states else "ok"
    return AdminStatusOut(overall=overall, checked_at=now, services=services)


async def cached_collect(
    db: AsyncSession, r: aioredis.Redis, settings: Settings, *, refresh: bool = False
) -> AdminStatusOut:
    if not refresh:
        with contextlib.suppress(aioredis.RedisError, ValueError):
            raw = await r.get(CACHE_KEY)
            if raw:
                return AdminStatusOut.model_validate_json(raw)
    out = await collect(db, r, settings)
    with contextlib.suppress(aioredis.RedisError):
        await r.set(CACHE_KEY, out.model_dump_json(), ex=CACHE_TTL_S)
    return out
