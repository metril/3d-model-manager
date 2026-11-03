"""Sync retries an item's failed import in place (no duplicate rows), keeps a
dismissal, and the migration helper collapses historical duplicates."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.importers.base import SearchResult
from app.models.collections import FollowedCollection
from app.models.enums import CollectionSyncMode, ImportSite, ImportState
from app.models.system import Import
from app.services import jobs
from app.services.import_dedup import collapse_duplicate_failed_imports
from app.tasks import base
from app.tasks import sync_collections as sc

SITE = ImportSite.MAKERWORLD


def _failed(ext="7", *, dismissed=None, error="boom"):
    return Import(
        url=f"https://makerworld.com/en/models/{ext}",
        site=SITE,
        external_id=ext,
        state=ImportState.FAILED,
        error=error,
        dismissed_at=dismissed,
    )


@pytest.fixture
def auto_collection(monkeypatch):
    from app.importers.registry import IMPORTER_REGISTRY

    class Stub:
        site = SITE

        def list_list_items(self, list_id, page=1):
            return (
                [
                    SearchResult(
                        site=SITE,
                        external_id="7",
                        title="M7",
                        url="https://makerworld.com/en/models/7",
                    )
                ]
                if page == 1
                else []
            )

    monkeypatch.setitem(IMPORTER_REGISTRY, SITE, Stub())
    # Don't run the pipeline: only the row bookkeeping is under test.
    monkeypatch.setattr(sc.import_from_url, "apply_async", lambda *a, **k: None)
    with base.sync_session() as s:
        s.add(
            FollowedCollection(
                site=SITE,
                list_id="L1",
                kind="collection",
                title="t",
                mode=CollectionSyncMode.AUTO,
                is_aggregate=False,
            )
        )
        s.commit()


def _run_sync():
    with base.sync_session() as s:
        job = jobs.create_job_sync(
            s, id=uuid.uuid4(), type="sync_collections", subject_type=None, subject_id=None
        )
        job_id = str(job.id)
    sc.sync_all.apply(args=[job_id]).get()


@pytest.mark.usefixtures("db_session")
def test_resync_reuses_failed_row_and_keeps_dismissal(auto_collection):
    when = datetime(2026, 1, 1, tzinfo=UTC)
    with base.sync_session() as s:
        s.add(_failed(dismissed=when))
        s.commit()
    _run_sync()
    with base.sync_session() as s:
        rows = list(s.scalars(select(Import)))
        assert len(rows) == 1
        assert rows[0].state == ImportState.PENDING
        assert rows[0].error is None
        assert rows[0].dismissed_at == when


@pytest.mark.usefixtures("db_session")
def test_resync_without_prior_failure_creates_row(auto_collection):
    _run_sync()
    with base.sync_session() as s:
        rows = list(s.scalars(select(Import)))
        assert len(rows) == 1 and rows[0].state == ImportState.PENDING


@pytest.mark.usefixtures("db_session")
def test_collapse_keeps_newest_and_inherits_dismissal():
    when = datetime(2026, 1, 1, tzinfo=UTC)
    with base.sync_session() as s:
        old_dismissed, mid, newest = _failed(dismissed=when), _failed(), _failed()
        other = _failed("8")
        done = Import(url="u", site=SITE, external_id="7", state=ImportState.DONE)
        s.add_all([old_dismissed, mid, newest, other, done])
        s.commit()
        ids = (newest.id, other.id, done.id)
        deleted = collapse_duplicate_failed_imports(s.connection())
        s.commit()
        s.expire_all()
        assert deleted == 2
        remaining = {i.id: i for i in s.scalars(select(Import))}
        assert set(remaining) == set(ids)
        assert remaining[newest.id].dismissed_at == when
        assert remaining[other.id].dismissed_at is None
