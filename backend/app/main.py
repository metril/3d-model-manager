"""FastAPI application factory and ASGI entrypoint (``app.main:app``)."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api import api_router
from app.config import get_settings
from app.db import get_sessionmaker
from app.logging_config import LevelPoller, apply_level, configure_logging
from app.services import spool
from app.services.app_config import get_app_config, seed_app_config, warn_ignored_env
from app.services.bootstrap import ensure_admin_user
from app.services.obico import seed_obico_config
from app.services.secrets_at_rest import reencrypt_secrets_at_rest
from app.services.server_config import (
    get_server_config,
    seed_server_config,
    warn_ignored_server_env,
)
from app.static import mount_spa

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run first-run bootstrap (SPEC requirement 1), create the upload spool
    directory (Task 6), seed the DB-backed runtime feature settings from env
    (Round 10; a no-op once seeded) and warn about any now-ignored deprecated
    env var, and eagerly re-encrypt any legacy plaintext secret (M6 A1) --
    all once on startup.
    """
    spool.ensure_spool_dir(get_settings())
    async with get_sessionmaker()() as session:
        await ensure_admin_user(session)
        settings = get_settings()
        if await seed_app_config(session, settings):
            config = await get_app_config(session, settings)
            logger.info(
                "Seeded app settings from env: printer_enabled=%s scan_interval_s=%s "
                "collection_sync_interval_s=%s watch_interval_s=%s watch_stable_s=%s",
                config.printer_enabled,
                config.scan_interval_s,
                config.collection_sync_interval_s,
                config.watch_interval_s,
                config.watch_stable_s,
            )
        warn_ignored_env(logger)
        if await seed_server_config(session, settings):
            logger.info("Seeded server settings (env / legacy app row / defaults)")
        await seed_obico_config(session, settings)
        server_cfg = await get_server_config(session, settings)
        warn_ignored_server_env(settings, server_cfg)
        apply_level(server_cfg.log_level)
        await reencrypt_secrets_at_rest(session, settings)
    poll_task = asyncio.create_task(_poll_log_level())
    try:
        yield
    finally:
        poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poll_task


_LEVEL_POLL_INTERVAL_S = 15.0


async def _poll_log_level() -> None:
    """Pick up `server.log_level` edits made through another api replica."""
    poller = LevelPoller(ttl_s=0)
    while True:
        await asyncio.sleep(_LEVEL_POLL_INTERVAL_S)
        await poller.maybe_apply(get_sessionmaker())


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    configure_logging()
    settings = get_settings()
    # `version` is the APP_VERSION baked in at image build time (see
    # docker/Dockerfile); it surfaces in /api/openapi.json's `info.version`
    # and in the /docs page header.
    app = FastAPI(title="3D Model Manager", version=settings.app_version, lifespan=lifespan)
    app.include_router(api_router, prefix="/api")

    # SPA static serving (Task 9): only when STATIC_DIR is set AND
    # actually has a built frontend in it. Registered last so its catch-all
    # route never shadows an api_router route.
    if settings.static_dir is not None:
        static_dir = Path(settings.static_dir)
        if (static_dir / "index.html").is_file():
            mount_spa(app, static_dir)
        else:
            logger.warning("STATIC_DIR=%s has no index.html; SPA serving disabled", static_dir)

    return app


app = create_app()
