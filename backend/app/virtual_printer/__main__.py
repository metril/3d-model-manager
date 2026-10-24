"""``python -m app.virtual_printer``: serve the lowest-id enabled virtual
printer; re-read the DB every 10 s and restart on any change.

With no enabled row the process idles and binds nothing. Network settings
(``vp_*`` in the ``server`` config) hot-reload. Port conflicts surface as an
``error`` status, never an exit. Status goes to Redis ``tdmm:vp:status``."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import redis
from sqlalchemy import select

from app.config import Settings, get_settings
from app.crypto import decrypt_secret
from app.models import VirtualPrinter
from app.tasks import base
from app.virtual_printer.intake import VpConfig, VpNetwork, load_network
from app.virtual_printer.service import VirtualPrinterService, preflight

log = logging.getLogger("virtual_printer")
POLL_S = 10.0
STATUS_KEY = "tdmm:vp:status"
STATUS_TTL_S = 30


def load_wanted(settings: Settings) -> tuple[VpConfig | None, VpNetwork]:
    return load_active(settings), load_network(settings)


def build_status(
    state: str,
    vp: VpConfig | None,
    net: VpNetwork | None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "vp_id": vp.id if vp else None,
        "bind": net.bind if net else None,
        "ports": (
            {"ssdp": net.port_ssdp, "mqtt": net.port_mqtt, "ftp": net.port_ftp} if net else None
        ),
        "pasv": [net.pasv_min, net.pasv_max] if net else None,
        "errors": errors or [],
        "ts": datetime.now(UTC).isoformat(),
    }


def publish_status(settings: Settings, status: dict[str, Any]) -> None:
    """Best effort; when Redis is unreachable nothing is written (UI: offline)."""
    try:
        client = redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        try:
            client.set(STATUS_KEY, json.dumps(status), ex=STATUS_TTL_S)
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not publish virtual printer status: %s", exc)


def apply_log_level(poller: Any) -> None:
    try:
        poller.maybe_apply_sync(base.get_sync_sessionmaker())
    except Exception:
        log.debug("log level poll failed", exc_info=True)


def load_active(settings: Settings) -> VpConfig | None:
    with base.sync_session() as s:
        row = s.scalars(
            select(VirtualPrinter)
            .where(VirtualPrinter.enabled.is_(True))
            .order_by(VirtualPrinter.id)
            .limit(1)
        ).first()
        if row is None:
            return None
        return VpConfig(
            id=row.id,
            name=row.name,
            model=row.model,
            serial=row.serial,
            access_code=decrypt_secret(settings, row.access_code_enc),
            mode=row.mode,
            target_printer_id=row.target_printer_id,
            created_by_user_id=row.created_by_user_id,
        )


async def _safe_stop(svc: VirtualPrinterService) -> None:
    try:
        await svc.stop()
    except Exception:
        log.exception("virtual printer stop failed")


async def run(
    settings: Settings,
    *,
    load: Callable[[Settings], tuple[VpConfig | None, VpNetwork]] = load_wanted,
    publish: Callable[[Settings, dict[str, Any]], None] = publish_status,
    poll_s: float = POLL_S,
    stop: asyncio.Event | None = None,
    on_poll: Callable[[], None] | None = None,
    seed: Callable[[], None] | None = None,
    make_service: Callable[..., VirtualPrinterService] = VirtualPrinterService,
) -> int:
    if seed:
        await asyncio.to_thread(seed)
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    svc: VirtualPrinterService | None = None
    current: tuple[VpConfig | None, VpNetwork] | None = None
    errors: list[str] = []
    while not stop.is_set():
        if on_poll is not None:
            await asyncio.to_thread(on_poll)
        wanted: tuple[VpConfig | None, VpNetwork] | None = None
        try:
            wanted = await asyncio.to_thread(load, settings)
        except Exception as exc:
            log.exception("could not read virtual printer config")
            await asyncio.to_thread(
                publish, settings, build_status("error", None, None, [f"config read failed: {exc}"])
            )
        if wanted is not None:
            if wanted != current:
                if svc is not None:
                    await _safe_stop(svc)
                    svc = None
                current, errors = wanted, []
                vp, net = wanted
                if vp is None:
                    log.info("no enabled virtual printer; idle")
                else:
                    errors = preflight(net)
                    if not errors:
                        try:
                            svc = make_service(settings, vp, net)
                            await svc.start()
                        except Exception as exc:
                            log.exception("virtual printer failed to start; retrying next poll")
                            errors = [f"start failed: {exc}"]
                            if svc is not None:
                                await _safe_stop(svc)
                            svc = None
                    if errors:
                        log.error("virtual printer cannot start: %s", "; ".join(errors))
                        current = None  # retry on the next poll
            vp, net = wanted
            if vp is None:
                status = build_status("idle", None, net)
            elif svc is not None:
                status = build_status("running", vp, net)
            else:
                status = build_status("error", vp, net, errors)
            await asyncio.to_thread(publish, settings, status)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), poll_s)
    if svc is not None:
        await _safe_stop(svc)
    return 0


def main() -> None:
    from app.logging_config import LevelPoller, configure_logging
    from app.services.server_config import seed_all_sync_safe

    configure_logging()
    settings = get_settings()
    poller = LevelPoller(ttl_s=30)
    sys.exit(
        asyncio.run(run(settings, seed=seed_all_sync_safe, on_poll=lambda: apply_log_level(poller)))
    )


if __name__ == "__main__":
    main()
