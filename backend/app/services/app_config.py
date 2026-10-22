"""DB-backed runtime feature settings (Round 10 "Settings" UI). Five knobs
that used to be env/`.env`-only -- `printer_enabled`, `scan_interval_s`,
`collection_sync_interval_s`, `watch_interval_s`, `watch_stable_s` -- move to
a single row in the `settings` table under key `"app"`, editable via
`GET`/`PUT /settings/app` (`app.api.settings`) without a container restart.
No crypto here: none of these five fields is a secret. Mirrors the async/
sync twin + fresh-dict upsert conventions already established by
`app.services.storage_config`/`app.services.import_tokens`, minus the
Fernet layer.

Resolution is PER-FIELD (`_resolve`): each of the five keys falls back
independently to its `Settings` env/default value when that key is absent
from the row's JSONB (or the row itself is absent), rather than requiring a
fully-populated row. This keeps the whole pre-existing test suite -- which
sets e.g. `PRINTER_ENABLED`/`SCAN_INTERVAL` via env/monkeypatch with no DB
row involved at all -- green untouched, and lets a row seeded before a
future sixth field is added degrade to that field's env/default instead of
a validation error.

`seed_app_config` runs once from the app lifespan (`app.main`) AFTER
`ensure_admin_user`: row absent -> insert the five env values (so a fresh
install's DB reflects its `.env` immediately, before any UI edit); row
present -> never overwrite (an operator's saved edit always wins, including
across a restart against a stale `.env`). `warn_ignored_env` then logs,
once per startup, that each deprecated env var still explicitly SET in the
environment is now ignored -- it only ever seeded the DB once; further
changes go through `PUT /settings/app` (the Settings UI) instead.
"""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Setting

SETTINGS_KEY = "app"

# Env names Round 10 deprecated in favor of the DB-backed "app" settings row
# -- each one still bridges into `Settings` (see app.config) so a fresh
# install's first seed reflects them, but is otherwise ignored once seeded.
DEPRECATED_ENV_NAMES = (
    "PRINTER_ENABLED",
    "SCAN_INTERVAL",
    "COLLECTION_SYNC_INTERVAL",
    "WATCH_INTERVAL",
    "WATCH_STABLE",
)


class AppConfig(BaseModel):
    printer_enabled: bool
    scan_interval_s: int
    collection_sync_interval_s: int
    watch_interval_s: int
    watch_stable_s: float
    # R11-B item 14 (print cost estimate): currency-agnostic per-kg/per-hour
    # rates the frontend multiplies against a print's filament_g/duration_s
    # (app.lib.printCost's estimatePrintCost, web/src/lib/printCost.ts) --
    # never used server-side, just stored/exposed like the five fields
    # above. No env backing (unlike those five): a fresh row always seeds
    # these two literal defaults.
    filament_cost_per_kg: float = 20.0
    machine_cost_per_hour: float = 0.0
    # P6: base URL of the slicer sidecar, straight off `Settings.slicer_url`
    # (`TDMM_SLICER_URL`) via `_env_config` -- not yet exposed on the
    # Settings UI/API schema (`AppSettingsIn`/`Out` in app.schemas.settings
    # deliberately omit it), so this field only ever carries the env/default
    # value today. `app.services.slicer_client.client_from_config` reads it.
    slicer_url: str | None = None
    # P6 final-review fix wave I1: how long a slice call waits for the
    # sidecar before giving up, straight off `Settings.slice_timeout_s`
    # (`TDMM_SLICE_TIMEOUT_S`) -- same not-yet-on-the-Settings-UI posture as
    # `slicer_url` above.
    slice_timeout_s: int = 600


def _env_config(settings: Settings) -> AppConfig:
    """The five fields' current env/default values, straight off `Settings`."""
    return AppConfig(
        printer_enabled=settings.printer_enabled,
        scan_interval_s=settings.scan_interval_s,
        collection_sync_interval_s=settings.collection_sync_interval_s,
        watch_interval_s=settings.watch_interval_s,
        watch_stable_s=settings.watch_stable_s,
        slicer_url=settings.slicer_url,
        slice_timeout_s=settings.slice_timeout_s,
    )


def _resolve(settings: Settings, value: dict | None) -> AppConfig:
    """Per-field precedence: start from the env/default value of each of the
    five fields, then overlay any of those keys present in the row's JSONB
    (see module docstring) -- a missing row, or a row missing some of the
    keys, degrades that field to env/default rather than erroring."""
    data = _env_config(settings).model_dump()
    if value:
        for field in data:
            if field in value:
                data[field] = value[field]
    return AppConfig(**data)


async def get_app_config(db: AsyncSession, settings: Settings) -> AppConfig:
    row = await db.get(Setting, SETTINGS_KEY)
    return _resolve(settings, row.value if row else None)


def get_app_config_sync(session: Session, settings: Settings) -> AppConfig:
    row = session.get(Setting, SETTINGS_KEY)
    return _resolve(settings, row.value if row else None)


async def set_app_config(db: AsyncSession, settings: Settings, config: AppConfig) -> AppConfig:
    row = await db.get(Setting, SETTINGS_KEY)
    value = config.model_dump()
    if row is None:
        db.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        # A FRESH dict, never an in-place mutation of `row.value` -- mirrors
        # `import_tokens.set_import_tokens`'s assignment so the ORM's JSONB
        # change-tracking actually sees the write.
        row.value = value
    await db.commit()
    return _resolve(settings, value)


def set_app_config_sync(session: Session, settings: Settings, config: AppConfig) -> AppConfig:
    row = session.get(Setting, SETTINGS_KEY)
    value = config.model_dump()
    if row is None:
        session.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    session.commit()
    return _resolve(settings, value)


async def seed_app_config(db: AsyncSession, settings: Settings) -> bool:
    """One-time env -> DB seed, called from the app lifespan. Row absent ->
    insert the five current env values and return `True`; row present ->
    return `False` WITHOUT ever touching it (an operator's saved edit, or a
    previous seed, always wins over whatever `.env` says now).

    M4: two api replicas booting concurrently could both miss the `db.get`
    above (neither has committed yet) and both attempt the `"app"` PK
    INSERT -- the loser's `IntegrityError` is caught, rolled back, and
    treated as "already seeded" (same race-loser convention as
    `app.services.library.store_imported_file_sync`'s Blob-insert guard and
    `app.services.derivatives.upsert_derivative`'s), rather than failing
    that replica's lifespan.
    """
    row = await db.get(Setting, SETTINGS_KEY)
    if row is not None:
        return False
    db.add(Setting(key=SETTINGS_KEY, value=_env_config(settings).model_dump()))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    return True


def warn_ignored_env(log: logging.Logger) -> None:
    """Log once, at startup, for each Round-10-deprecated env var that's
    still explicitly set in the environment -- it only seeded the DB once
    (`seed_app_config`); further changes now go through `PUT /settings/app`
    (the Settings UI), not `.env`."""
    for name in DEPRECATED_ENV_NAMES:
        if name in os.environ:
            log.warning(
                "%s is set in the environment but is now ignored -- it seeded the "
                "database once at first startup; change it via the Settings UI "
                "(PUT /settings/app) instead.",
                name,
            )
