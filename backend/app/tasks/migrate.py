"""``migrate_storage`` Celery task (Task 6 brief): copies the whole library
tree from the currently active backend onto a candidate TARGET backend,
verifying each file's blake3 hash as it streams across, and only cuts the
active ``storage`` setting over to the target once every file has verified.

Never deletes the source (Global Constraints: the migrated library is left
in place for the operator to remove by hand once they've confirmed the
cutover). Never touches derivatives (Global Constraints "DERIVATIVES ALWAYS
STAY LOCAL"): they live under ``{settings.data_dir}/derivatives/...`` on
local disk regardless of the active library backend, entirely outside the
``StorageBackend`` abstraction this task walks.

Runs entirely in the worker's SYNC world -- see ``app.tasks.base``.
"""

from __future__ import annotations

import logging

import blake3

from app.config import get_settings
from app.services import jobs, storage_config
from app.services.storage_config import resolve_backend_sync, set_active_config_sync
from app.storage.config import parse_storage_config
from app.storage.registry import get_backend
from app.tasks import base
from app.tasks.celery_app import LONG_SOFT_TIME_LIMIT_S, LONG_TIME_LIMIT_S, celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.migrate.migrate_storage",
    soft_time_limit=LONG_SOFT_TIME_LIMIT_S,
    time_limit=LONG_TIME_LIMIT_S,
)
def migrate_storage(job_id: str, target: dict) -> None:
    settings = get_settings()
    # `target` travels over the internal Celery broker encrypted (M6 A1.5.2
    # -- see app.api.settings.migrate_storage_settings); decrypt it here,
    # right where it's used, same posture as the M4 printer access code.
    data, _ = storage_config.decrypt_config_row(settings, dict(target))
    target_cfg = parse_storage_config(data)
    with base.sync_session() as s:
        jobs.mark_running(s, job_id)
        source = resolve_backend_sync(s, settings)
    dest = get_backend(settings, target_cfg)
    try:
        for entry in source.walk(""):
            # One streaming pass: hash the source bytes WHILE feeding them to
            # dest.write, then assert dest agrees. dest.write returns the
            # blake3 of what it actually wrote, so a single read verifies the
            # copy end-to-end (no second source read). `entry`/`src_hash`
            # are bound as default args so each closure captures THIS
            # iteration's values, not whatever the loop variable holds by
            # the time `dest.write` gets around to calling it (it doesn't
            # here -- write consumes the generator immediately -- but ruff's
            # B023 can't tell that statically).
            src_hash = blake3.blake3()

            def _chunks(entry=entry, src_hash=src_hash):
                for chunk in source.read(entry.key):
                    src_hash.update(chunk)
                    yield chunk

            result = dest.write(entry.key, _chunks())
            if result.hash != src_hash.hexdigest():
                raise RuntimeError(f"hash mismatch migrating {entry.key}")
        with base.sync_session() as s:
            # cutover only after every file verified; encrypts on write --
            # point of no return: the config is switched here.
            set_active_config_sync(s, settings, target_cfg)
    except Exception as exc:
        with base.sync_session() as s:
            jobs.mark_failed(s, job_id, str(exc))
        raise

    # Cutover succeeded: mark done in its OWN try (M3-deferred minor, folded
    # into Task 8) so a post-cutover publish/commit hiccup can't misreport a
    # done migration as failed -- the migration genuinely succeeded (config
    # already switched, source intact), so there's nothing left to roll back
    # and no reason to lie to the operator about it.
    try:
        with base.sync_session() as s:
            jobs.mark_done(s, job_id)
    except Exception:
        logger.warning(
            "migrate %s: cutover succeeded but marking done failed", job_id, exc_info=True
        )
