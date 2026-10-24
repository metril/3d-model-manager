"""The Celery application (SPEC "Processing pipeline"; Task 6 interface
decisions).

Broker + result backend are both ``REDIS_URL``. ``task_acks_late=True``
so a worker that dies mid-task redelivers the message rather than losing it.
Queue ``io`` carries M1's ``store_to_backend`` plus everything else under
``app.tasks``; ``cpu`` (memory-recycled worker pool, per SPEC "Architecture")
is routed by name ahead of time for the CPU-heavy extraction/render tasks
``app.tasks.pipeline`` registers -- Task 2 adds the driver/runner but no real
step bodies yet, so nothing dispatches to ``cpu`` in practice until Tasks 3-6
land.

Worker entrypoint: ``celery -A app.tasks.celery_app worker``.

Tests never hit the broker at all: a session-scoped autouse fixture
(``tests/conftest.py::_celery_eager_mode``) flips ``task_always_eager`` on
this same module-level ``celery_app`` object before any test runs, so
``.delay()``/``.apply_async()`` just run the task body inline.
"""

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

from app.config import get_settings

_settings = get_settings()

celery_app = Celery("tdmm", broker=_settings.redis_url, backend=_settings.redis_url)

celery_app.conf.update(
    task_acks_late=True,
    task_default_queue="io",
    task_queues=(Queue("io"), Queue("cpu")),
    # Dict order matters: Celery's router checks patterns in insertion order
    # and takes the first match, so the more specific `pipeline.*` entry
    # (M2's CPU-heavy extraction/render tasks) must come before the general
    # `app.tasks.*` catch-all or every task -- pipeline included -- would
    # always match the catch-all first and land on `io`.
    task_routes={
        "app.tasks.pipeline.*": {"queue": "cpu"},
        # P4 camera plan Task 7: ffmpeg transcode is CPU-heavy like pipeline's
        # extraction/render steps, same reason it needs its own entry ahead
        # of the general catch-all below.
        "app.tasks.media.*": {"queue": "cpu"},
        # P6 Task 5: slicing is CPU-heavy like the two entries above, same
        # reason it needs its own entry ahead of the general catch-all.
        "app.tasks.slicing.*": {"queue": "cpu"},
        "app.tasks.*": {"queue": "io"},
    },
    imports=(
        "app.tasks.ingest",
        "app.tasks.pipeline",
        "app.tasks.scan",
        "app.tasks.migrate",
        "app.tasks.relocate",
        "app.tasks.printing",
        "app.tasks.archive",
        "app.tasks.media",
        "app.tasks.importing",
        "app.tasks.sync_collections",
        "app.tasks.slicer_watch",
        "app.tasks.scheduler",
        "app.tasks.slicing",
        "app.tasks.dispatch",
        "app.tasks.billing",
        "app.tasks.home_assistant",
        "app.tasks.spoolman",
        "app.tasks.sensors",
        "app.tasks.firmware",
        "app.tasks.ops",
        "app.tasks.notify",
        "app.tasks.obico",
        "app.tasks.location_sensors",
        "app.tasks.mail",
        "app.tasks.auth",
    ),
)

# Round 10 Task 2: scheduled work used to be three independent, OPT-IN beat
# entries -- one per feature -- each gated on its own interval setting being
# read from `Settings` (env) exactly once, here, at process import time.
# Flipping an interval via the new `PUT /settings/app` (DB-backed
# `app.services.app_config`) had no effect until every celery process --
# api, worker, AND beat -- was restarted, since beat's schedule was baked in
# at import.
#
# A single UNCONDITIONAL entry replaces all three: `dispatch_scheduled`
# ticks every `scheduler._CADENCE_S` seconds and, on each tick, re-reads the
# DB-backed `AppConfig` fresh and decides itself which of scan/sync/watch is
# actually due (see `app.tasks.scheduler` for the full arm-and-skip
# rationale). This import must follow `celery_app`'s own creation above --
# `scheduler`'s `@celery_app.task` decorator needs it to already exist.
from app.tasks import scheduler  # noqa: E402

celery_app.conf.beat_schedule = {
    "dispatch-scheduled": {
        "task": "app.tasks.scheduler.dispatch_scheduled",
        "schedule": scheduler._CADENCE_S,
    }
}

# Additive: recovers terminal jobs whose archive enqueue was lost (P2).
celery_app.conf.beat_schedule["archive-sweep"] = {
    "task": "app.tasks.archive.archive_sweep",
    "schedule": 300,
}

# Additive: P6 Task 7's slice-and-dispatch queue, ticked independently of
# the scan/sync/watch cadence above (`dispatch.dispatch_queue` is its own
# no-op-when-disabled + lock-guarded task, same posture as `scheduler
# .dispatch_scheduled`).
from app.tasks import dispatch  # noqa: E402

celery_app.conf.beat_schedule["dispatch-queue"] = {
    "task": "app.tasks.dispatch.dispatch_queue",
    "schedule": dispatch.DISPATCH_INTERVAL_S,
}

# Additive: P7 Task 7's nightly balance recompute -- reconciles
# cost_centers.balance against the ledger and retries any over_budget
# hold (the monthly-cap rollover unhold).
celery_app.conf.beat_schedule["billing-recompute"] = {
    "task": "app.tasks.billing.recompute_balances",
    "schedule": crontab(hour=0, minute=5),
}

# Additive: P8 Task 4's HA REST poller -- writes `ha:alert:*`/`ha:energy:*`
# Redis keys every tick (no-op when HA isn't configured/no links exist).
from app.tasks import home_assistant as _home_assistant  # noqa: E402

celery_app.conf.beat_schedule["ha-poll"] = {
    "task": "app.tasks.home_assistant.poll_ha",
    "schedule": _home_assistant.POLL_INTERVAL_S,
}

# Additive: Q9 Spoolman reconcile (the task itself skips when mode=off).
from app.tasks import spoolman as _spoolman  # noqa: E402

celery_app.conf.beat_schedule["spoolman-reconcile"] = {
    "task": "app.tasks.spoolman.reconcile",
    "schedule": _spoolman.RECONCILE_INTERVAL_S,
}

# Additive: Q8b sensor history -- daily prune of printer_sensor_samples.
from app.tasks import sensors as _sensors  # noqa: E402,F401

celery_app.conf.beat_schedule["sensors-prune"] = {
    "task": "app.tasks.sensors.prune",
    "schedule": crontab(hour=3, minute=15),
}

# Additive: Q5 daily Bambu firmware feed refresh.
from app.tasks import firmware as _firmware  # noqa: E402

celery_app.conf.beat_schedule["firmware-check"] = {
    "task": "app.tasks.firmware.check_firmware",
    "schedule": _firmware.CHECK_INTERVAL_S,
}

# Additive: Q6 notifications -- drain the event queue and flush digests.
from app.tasks import notify as _notify  # noqa: E402

celery_app.conf.beat_schedule["notify-deliver"] = {
    "task": "app.tasks.notify.deliver_pending",
    "schedule": _notify.DRAIN_INTERVAL_S,
}
celery_app.conf.beat_schedule["notify-digest"] = {
    "task": "app.tasks.notify.digest",
    "schedule": 60,
}

# Additive: Q7 ops -- plug poll/auto-off, scheduled-backup gate (the task
# re-reads settings each tick), daily update check.
from app.tasks import ops as _ops  # noqa: E402

celery_app.conf.beat_schedule["plugs-tick"] = {
    "task": "app.tasks.plugs.tick",
    "schedule": _ops.PLUG_INTERVAL_S,
}
celery_app.conf.beat_schedule["backup-scheduled"] = {
    "task": "app.tasks.backup.scheduled",
    "schedule": _ops.BACKUP_GATE_INTERVAL_S,
}
celery_app.conf.beat_schedule["updates-check"] = {
    "task": "app.tasks.updates.check",
    "schedule": crontab(hour=4, minute=17),
}

# Additive: Q10 Obico -- daily prune of failure_detections (30 days).
from app.tasks import obico as _obico  # noqa: E402,F401

celery_app.conf.beat_schedule["obico-prune"] = {
    "task": "app.tasks.obico.prune",
    "schedule": crontab(hour=3, minute=30),
}

# Additive: T7 archive retention purge (03:30) + hourly confirm-default sweep.
celery_app.conf.beat_schedule["archive-purge"] = {
    "task": "app.tasks.archive.purge",
    "schedule": crontab(hour=3, minute=30),
}
celery_app.conf.beat_schedule["archive-confirm-defaults"] = {
    "task": "app.tasks.archive.confirm_defaults",
    "schedule": crontab(minute=7),
}

# Additive: T6 hourly sweep of expired 2FA ephemeral tokens / email OTP codes.
from app.tasks import auth as _auth  # noqa: E402,F401

celery_app.conf.beat_schedule["auth-sweep-ephemeral"] = {
    "task": "app.tasks.auth.sweep_ephemeral",
    "schedule": 3600,
}


# Server settings (spec "Seed / precedence"): every long-lived process seeds
# the DB-backed `server` + `obico` rows at start (idempotent, ON CONFLICT DO
# NOTHING) and follows `server.log_level` at runtime. Imports are lazy: this
# module is imported by the api process too.
from celery.signals import beat_init, task_prerun, worker_process_init  # noqa: E402

_level_poller = None


def _apply_log_level_now() -> None:
    from app.logging_config import LevelPoller
    from app.tasks import base

    global _level_poller
    _level_poller = LevelPoller(ttl_s=30)
    _level_poller.maybe_apply_sync(base.sync_session)


@worker_process_init.connect
def _on_worker_process_init(**_: object) -> None:
    from app.services.server_config import seed_all_sync_safe

    seed_all_sync_safe()
    _apply_log_level_now()


@beat_init.connect
def _on_beat_init(**_: object) -> None:
    # Beat logs little; the level is applied once at start (documented).
    from app.services.server_config import seed_all_sync_safe

    seed_all_sync_safe()
    _apply_log_level_now()


@task_prerun.connect
def _on_task_prerun(**_: object) -> None:
    from app.logging_config import LevelPoller
    from app.tasks import base

    global _level_poller
    if _level_poller is None:
        _level_poller = LevelPoller(ttl_s=30)
    _level_poller.maybe_apply_sync(base.sync_session)


# Additive: T5 storage-location HA sensor poll (no-op without HA/sensors).
from app.tasks import location_sensors as _location_sensors  # noqa: E402

celery_app.conf.beat_schedule["location-sensors-poll"] = {
    "task": "location_sensors.poll",
    "schedule": _location_sensors.POLL_INTERVAL_S,
}
