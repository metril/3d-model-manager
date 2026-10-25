"""SSE event publishing (Global Constraints: Redis pub/sub channel
``tdmm:events``, JSON event shape ``{"type": "job.updated", "job_id": ...,
"job_type": ..., "state": ..., "subject_type": ..., "subject_id": ...}``).

Two publish functions mirror the API(async)/worker(sync) split documented in
``app.tasks.base``: ``publish_job_event`` for async API-side code (used
directly by tests exercising the SSE endpoint; the jobs API itself doesn't
currently need to publish), ``publish_job_event_sync`` for Celery task
bodies (``app.services.jobs``'s ``mark_running``/``mark_done``/
``mark_failed``, called from ``app.tasks.ingest``). Each publish opens a
short-lived Redis connection rather than holding one open -- job events are
infrequent, so the extra connect cost is not worth the added lifecycle
complexity of a shared client, especially in Celery's prefork worker model.
"""

from __future__ import annotations

import json
import uuid

import redis
import redis.asyncio as aioredis

CHANNEL = "tdmm:events"


def job_event_payload(
    *,
    job_id: uuid.UUID | str,
    job_type: str,
    state: str,
    subject_type: str | None,
    subject_id: int | None,
) -> dict[str, object]:
    """Build the Global-Constraints-shaped event body for a job update."""
    return {
        "type": "job.updated",
        "job_id": str(job_id),
        "job_type": job_type,
        "state": state,
        "subject_type": subject_type,
        "subject_id": subject_id,
    }


def publish_job_event_sync(redis_url: str, **kwargs: object) -> None:
    """Publish from Celery task bodies (sync world; see ``app.tasks.base``)."""
    client = redis.Redis.from_url(redis_url)
    try:
        client.publish(CHANNEL, json.dumps(job_event_payload(**kwargs)))
    finally:
        client.close()


async def publish_job_event(redis_url: str, **kwargs: object) -> None:
    """Publish from async API-side code."""
    client = aioredis.Redis.from_url(redis_url)
    try:
        await client.publish(CHANNEL, json.dumps(job_event_payload(**kwargs)))
    finally:
        await client.aclose()


def print_job_event_payload(*, print_job_id: int, printer_id: int, state: str) -> dict:
    """Coarse print-job lifecycle event (M4). A NEW SSE type distinct from
    ``job.updated`` -- high-frequency printer telemetry is POLLED from
    ``GET /printers/{id}/status`` instead of streamed here (Global
    Constraints live-status decision)."""
    return {
        "type": "print_job.updated",
        "print_job_id": print_job_id,
        "printer_id": printer_id,
        "state": state,
    }


def publish_print_job_event_sync(
    redis_url: str, *, print_job_id: int, printer_id: int, state: str
) -> None:
    client = redis.Redis.from_url(redis_url)
    try:
        client.publish(
            CHANNEL,
            json.dumps(
                print_job_event_payload(
                    print_job_id=print_job_id, printer_id=printer_id, state=state
                )
            ),
        )
    finally:
        client.close()


def publish_scan_event_sync(redis_url: str, scan_run_id: int, state: str) -> None:
    """Publish a scan run's state as a ``job.updated`` event (Task 5 brief:
    "No new SSE event type" -- reuses ``job_event_payload``'s existing shape
    with ``job_type="scan_library"``, ``subject_type="scan_run"``, so the
    frontend's existing ``job.updated`` handler already invalidates
    ``["models"]``/``["revisions"]`` (adopted models show up) without any
    protocol change; Task 8 adds one branch keyed on ``job_type ==
    "scan_library"`` to also invalidate ``["scan"]``.
    """
    publish_job_event_sync(
        redis_url,
        job_id=str(scan_run_id),
        job_type="scan_library",
        state=state,
        subject_type="scan_run",
        subject_id=scan_run_id,
    )


def publish_import_event_sync(redis_url: str, import_id: int, state: str) -> None:
    """Publish a gallery import's state as a ``job.updated`` event (M5;
    mirrors ``publish_scan_event_sync``). ``job_type="import_from_url"``,
    ``subject_type="import"`` -- the frontend's ``job.updated`` handler adds
    one branch keyed on that job_type to invalidate ``["imports"]``/
    ``["models"]``; no new SSE event type."""
    publish_job_event_sync(
        redis_url,
        job_id=str(import_id),
        job_type="import_from_url",
        state=state,
        subject_type="import",
        subject_id=import_id,
    )


def slice_event_payload(
    *,
    state: str,
    slice_job_id: str | None = None,
    queue_id: int | None = None,
    file_id: int | None = None,
    sliced_file_id: int | None = None,
) -> dict:
    """Slice lifecycle event (`slice.updated`): a standalone slice job
    (``slice_job_id``) or a queue entry's slice (``queue_id``)."""
    return {
        "type": "slice.updated",
        "state": state,
        "slice_job_id": slice_job_id,
        "queue_id": queue_id,
        "file_id": file_id,
        "sliced_file_id": sliced_file_id,
    }


def publish_slice_event_sync(redis_url: str, **kwargs: object) -> None:
    """Best-effort: a Redis blip never fails the slice that was committed."""
    try:
        client = redis.Redis.from_url(redis_url, socket_timeout=2, socket_connect_timeout=2)
        try:
            client.publish(CHANNEL, json.dumps(slice_event_payload(**kwargs)))
        finally:
            client.close()
    except redis.RedisError:
        pass
