"""Starting an import (M8 H extraction).

``POST /imports`` and "approve a queued review item" must behave identically:
detect the site, canonicalize, refuse to duplicate a model already in the
library (``app.services.import_dedup``), otherwise create the ``Import`` row and
dispatch the pipeline. Sharing one function keeps the dedup guard from drifting
between the two call sites.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.importers.registry import build_importer_for_url, deferred_site_for_url
from app.models.collections import FollowedCollection
from app.models.enums import ImportState
from app.models.system import Import
from app.schemas.imports import ImportCreate
from app.services.import_dedup import find_live_import
from app.tasks.importing import import_from_url


def _enqueue(imp: Import) -> None:
    """Dispatch ``import_from_url`` for ``imp`` -- the one place that picks
    the task_id idiom, so ``start_import`` and a retry can't drift apart.
    ``import-{id}`` is reused verbatim on a retry rather than suffixed per
    attempt: Celery only needs a task_id to be unique among CONCURRENTLY
    in-flight tasks (it keys the result backend and, with ``task_acks_late``,
    redelivery dedup), and a retry is only ever dispatched once the prior
    attempt has already reached its terminal ``failed`` state, so the two
    never overlap. ``app.services.jobs.retry_job`` already relies on the same
    reuse-on-retry idiom for `jobs.id`, so this isn't a new precedent."""
    import_from_url.apply_async(args=[imp.id], task_id=f"import-{imp.id}")


async def start_import(
    db: AsyncSession,
    url: str,
    collection: FollowedCollection | None = None,
    created_by_user_id: int | None = None,
    extra_tags: list[str] | None = None,
) -> tuple[Import, bool]:
    """Returns ``(import_row, created)``. ``created`` is False when the model was
    already in the library -- the caller then hands back the EXISTING import
    (and should signal "nothing was created", e.g. HTTP 200 instead of 201).

    ``collection`` is the followed collection this import came from (approving
    a queued review item), if any -- recorded on the new ``Import`` row so the
    worker can stamp provenance on the resulting model (Branch 3 Task 1). A
    manual ``POST /imports``/``POST /ext/imports`` passes none, leaving it NULL.
    """
    importer = build_importer_for_url(url)
    if importer is None:
        deferred = deferred_site_for_url(url)
        if deferred is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{deferred.value} import isn't available yet.",
            )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Unsupported URL -- paste a Thingiverse, Printables, or MakerWorld model link.",
        )

    external_id = importer.canonicalize(url)
    existing = await find_live_import(db, importer.site, external_id)
    if existing is not None:
        return existing, False

    imp = Import(
        url=url,
        site=importer.site,
        external_id=external_id,
        state=ImportState.PENDING,
        collection_id=collection.id if collection else None,
        created_by_user_id=created_by_user_id,
        extra_tags=extra_tags or None,
    )
    db.add(imp)
    await db.commit()
    await db.refresh(imp)

    _enqueue(imp)

    # Under eager Celery (tests) the line above ran the whole import inline
    # through its own SYNC session, driving the row to done/failed -- refresh so
    # this async session hands back the terminal state, not the stale "pending"
    # snapshot (same reasoning as app.api.settings.migrate).
    await db.refresh(imp)
    return imp, True


async def retry_failed_import(db: AsyncSession, import_id: int) -> Import:
    """Re-enqueue a ``failed`` import (T2, import-health branch) -- the UI's
    "retry" action once a dead Bambu session or other transient cause has
    been fixed. 404 unknown id, 409 unless the row is currently ``failed``.

    Only a ``failed`` row is eligible, so this can never collide with
    ``find_live_import``'s dedup guard: that guard only matches a ``done``
    row that still points at a live Model (see ``app.services.import_dedup``),
    and a ``failed`` row by construction has neither (the pipeline's "IMPORTS
    ATOMIC" invariant -- ``app.tasks.importing`` -- guarantees ``model_id`` is
    NULL on every failure path). Resetting to ``pending`` and re-dispatching
    therefore can't ever produce a second live import for the same remote
    model.

    The eligibility check and the state flip are ONE atomic conditional
    ``UPDATE`` (post-review fix M2), not a SELECT followed by a separate
    UPDATE: two near-simultaneous retries of the same failed import could
    otherwise both pass a plain "is it failed?" read before either write
    landed, and both go on to enqueue the same ``task_id``. The UPDATE's
    ``WHERE ... AND state='failed'`` only ever matches for the FIRST caller
    to reach it -- the loser's statement matches zero rows, so only one
    caller ever proceeds to ``_enqueue``.
    """
    result = await db.execute(
        update(Import)
        .where(Import.id == import_id, Import.state == ImportState.FAILED)
        .values(state=ImportState.PENDING, error=None)
    )
    if result.rowcount == 0:
        # Either no such import, or one that exists but wasn't `failed` (a
        # lost race, or just the wrong state) -- this extra read is OUTSIDE
        # the atomic UPDATE above and only used to pick the right error, so
        # it can't itself change which caller "wins".
        imp = await db.get(Import, import_id)
        if imp is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"import {import_id} not found")
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"import {import_id} is not in a failed state"
        )
    await db.commit()

    imp = await db.get(Import, import_id)

    _enqueue(imp)

    # Same eager-Celery reasoning as `start_import` above: the dispatch just
    # ran the whole import inline through its own sync session under tests,
    # so refresh to hand back the real terminal state rather than the stale
    # "pending" snapshot.
    await db.refresh(imp)
    return imp


async def start_import_with_options(
    db: AsyncSession, payload: ImportCreate, created_by_user_id: int | None
) -> tuple[Import, bool]:
    """``start_import`` plus the extension's save options: ``payload.collection_id``
    must name a followed collection (422 otherwise) and ``payload.tags`` are
    applied to the model when the import completes."""
    collection = None
    if payload.collection_id is not None:
        collection = await db.get(FollowedCollection, payload.collection_id)
        if collection is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"collection {payload.collection_id} not found",
            )
    return await start_import(
        db,
        payload.url,
        collection=collection,
        created_by_user_id=created_by_user_id,
        extra_tags=payload.tags or None,
    )
