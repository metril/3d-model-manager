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
