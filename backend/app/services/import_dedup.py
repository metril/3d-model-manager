"""Cross-import de-duplication (M8 H).

Before M8 the ONLY idempotency in the import path was per-``Import``-row
(``tasks.importing``'s Redis lock + orphan-detect entry guard), which protects
against Celery redelivery of the SAME import. Nothing stopped a SECOND import
of the same remote model from creating a second Model -- ``create_import``
always inserted a fresh row and ``create_imported_model_sync`` never looked for
an existing source. That is harmless for a one-off manual click, but a periodic
collection sync (H) re-walks the same lists forever, so it would mint a
duplicate model on every single run.

The identity of a remote model is ``(site, external_id)`` -- both already
stored on ``imports``. A model is considered ALREADY IN THE LIBRARY when a
``done`` import for that pair still points at a live Model. ``model_id`` is
``ON DELETE SET NULL``, so a ``done`` row whose model was later deleted has
``model_id IS NULL`` and must NOT block a re-import -- which is exactly why the
guard checks ``model_id IS NOT NULL`` rather than merely ``state = 'done'``.

NOTE (deliberate): no UNIQUE index enforces this. An install that predates the
guard may already hold duplicate ``done`` rows for one pair, and a unique index
would make the migration fail on their data. The lookup index added alongside
this module keeps the check cheap; promoting it to UNIQUE is safe only after a
one-time de-dup pass.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from app.models.enums import ImportSite, ImportState
from app.models.library import File, Model, Revision
from app.models.system import Import


def _live_import_stmt(site: ImportSite, external_id: str):
    return (
        select(Import)
        .join(Model, Model.id == Import.model_id)
        .where(
            Import.site == site,
            Import.external_id == external_id,
            Import.state == ImportState.DONE,
            Import.model_id.is_not(None),
            Model.deleted_at.is_(None),
        )
        .order_by(Import.id)
        .limit(1)
    )


async def find_live_import(
    db: AsyncSession, site: ImportSite, external_id: str | None
) -> Import | None:
    """The existing ``done`` import whose Model is still in the library, or
    None. ``external_id`` None (a site whose canonicalize found no id) can
    never be matched, so it always reads as "not imported"."""
    if not external_id:
        return None
    return (await db.execute(_live_import_stmt(site, external_id))).scalars().first()


def find_live_import_sync(
    session: SyncSession, site: ImportSite, external_id: str | None
) -> Import | None:
    """Worker-side twin of :func:`find_live_import` (the collection sync task
    runs entirely in the sync world -- see ``app.tasks.base``)."""
    if not external_id:
        return None
    return session.execute(_live_import_stmt(site, external_id)).scalars().first()


# States an import passes through before it either produces a Model or fails.
_ACTIVE_STATES = (ImportState.PENDING, ImportState.FETCHING, ImportState.DOWNLOADING)


def find_active_import_sync(
    session: SyncSession, site: ImportSite, external_id: str | None
) -> Import | None:
    """An import of this pair that is still IN FLIGHT.

    ``find_live_import_sync`` only matches a ``done`` import, which is enough
    for the API (it commits its row before answering). The periodic sync is
    different: under real (non-eager) Celery the import it just dispatched is
    still ``pending`` when the next followed list is walked, so a model that
    appears in TWO followed lists would be dispatched -- and imported -- twice.
    Skipping anything already in flight closes that. A ``failed`` import is not
    active, so it stays retryable on the next run.
    """
    if not external_id:
        return None
    stmt = (
        select(Import)
        .where(
            Import.site == site,
            Import.external_id == external_id,
            Import.state.in_(_ACTIVE_STATES),
        )
        .order_by(Import.id)
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


# ---------------------------------------------------------------------------
# R11-C item 18: PUT /uploads' content-identity dedup guard. Different
# identity than the import guards above (content hash, not (site,
# external_id)), but the same "already in the library" shape -- kept here so
# any future hash-based dedup check has one canonical lookup, not a second
# copy of this join.
# ---------------------------------------------------------------------------


async def find_model_by_blob_hash(
    db: AsyncSession, blob_hash: str, *, exclude_model_id: int | None = None
) -> Model | None:
    """The first (lowest-id) model that already has a file with this blob
    hash, excluding ``exclude_model_id`` (the model the current upload is
    targeting -- re-uploading identical bytes onto the SAME model is not a
    duplicate). ``None`` if the content is genuinely new to the library.
    """
    stmt = (
        select(Model)
        .join(Revision, Revision.model_id == Model.id)
        .join(File, File.revision_id == Revision.id)
        .where(File.blob_hash == blob_hash, Model.deleted_at.is_(None))
        .order_by(Model.id)
        .limit(1)
    )
    if exclude_model_id is not None:
        stmt = stmt.where(Model.id != exclude_model_id)
    return (await db.execute(stmt)).scalars().first()
