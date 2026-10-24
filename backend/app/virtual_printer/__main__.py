"""``python -m app.virtual_printer``: serve the lowest-id enabled virtual
printer; re-read the DB every 10 s and restart on any change."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from sqlalchemy import select

from app.config import Settings, get_settings
from app.crypto import decrypt_secret
from app.models import VirtualPrinter
from app.tasks import base
from app.virtual_printer.intake import VpConfig, VpNetwork, load_network
from app.virtual_printer.service import VirtualPrinterService, preflight

log = logging.getLogger("virtual_printer")
POLL_S = 10.0


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


async def run(settings: Settings) -> int:
    net = await asyncio.to_thread(load_network, settings)
    problems = preflight(net)
    if problems:
        for p in problems:
            print(f"virtual-printer: cannot start: {p}", file=sys.stderr)
        print(
            "virtual-printer: free those ports (or override TDMM_VP_PORT_SSDP/MQTT/FTP) and retry.",
            file=sys.stderr,
        )
        return 2
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    svc: VirtualPrinterService | None = None
    current: VpConfig | None = None
    current_net: VpNetwork = net
    while not stop.is_set():
        try:
            wanted = await asyncio.to_thread(load_active, settings)
            wanted_net = await asyncio.to_thread(load_network, settings)
        except Exception:
            log.exception("could not read virtual printer config")
            wanted, wanted_net = current, current_net
        if wanted != current or (wanted is not None and wanted_net != current_net):
            if svc is not None:
                await svc.stop()
                svc = None
            current, current_net = wanted, wanted_net
            if wanted is not None:
                try:
                    svc = VirtualPrinterService(settings, wanted, wanted_net)
                    await svc.start()
                except Exception:
                    log.exception("virtual printer failed to start; retrying next poll")
                    if svc is not None:
                        with contextlib.suppress(Exception):
                            await svc.stop()
                    svc = None
                    current = None
            else:
                log.info("no enabled virtual printer; idle")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), POLL_S)
    if svc is not None:
        await svc.stop()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(asyncio.run(run(get_settings())))


if __name__ == "__main__":
    main()
