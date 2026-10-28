"""Named MakerWorld folder beats the uid aggregate ("All collected models"):
sync ordering, aggregate-walk skipping, provenance re-homing, pending re-home
and auto-follow of cached named folders."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.importers.base import SearchResult
from app.models.collections import (
    AutofollowDismissed,
    FollowedCollection,
    PendingImport,
    RemoteCollectionCache,
    RemoteCollectionItem,
)
from app.models.enums import CollectionSyncMode, ImportSite, ImportState
from app.models.library import Model
from app.models.system import Import
from app.services import collections as svc
from app.services import jobs
from app.tasks import base
from app.tasks import sync_collections as sc

SITE = ImportSite.MAKERWORLD
AGG = "3054026541"
NAMED = "1684993"


def _fc(list_id, title, *, aggregate=False, mode=CollectionSyncMode.REVIEW):
    return FollowedCollection(
        site=SITE,
        list_id=list_id,
        kind="collection",
        title=title,
        mode=mode,
        is_aggregate=aggregate,
    )


def _item(i):
    return SearchResult(
        site=SITE, external_id=str(i), title=f"M{i}", url=f"https://makerworld.com/en/models/{i}"
    )


def _job() -> str:
    with base.sync_session() as s:
        job = jobs.create_job_sync(
            s, id=uuid.uuid4(), type="sync_collections", subject_type=None, subject_id=None
        )
        return str(job.id)


@pytest.fixture
def stub_lists(monkeypatch):
    """Registers a MAKERWORLD stub importer; returns ``(lists, walked)`` where
    ``lists`` maps list_id -> items and ``walked`` records the walk order."""
    from app.importers.registry import IMPORTER_REGISTRY

    lists: dict[str, list] = {}
    walked: list[str] = []

    class Stub:
        site = SITE

        def list_list_items(self, list_id, page=1):
            walked.append(list_id)
            return lists.get(list_id, []) if page == 1 else []

    monkeypatch.setitem(IMPORTER_REGISTRY, SITE, Stub())
    return lists, walked


def _own(s, list_id, ids):
    for pos, i in enumerate(ids):
        s.add(
            RemoteCollectionItem(
                site=SITE,
                list_id=list_id,
                external_id=str(i),
                title=f"M{i}",
                url=f"https://makerworld.com/en/models/{i}",
                position=pos,
            )
        )
    s.commit()


@pytest.mark.usefixtures("db_session")
def test_named_walked_before_aggregate_and_aggregate_skips_owned(stub_lists):
    lists, walked = stub_lists
    with base.sync_session() as s:
        s.add_all([_fc(AGG, "All collected models", aggregate=True), _fc(NAMED, "Things to print")])
        s.commit()
        _own(s, NAMED, [1])
    lists[AGG] = [_item(1), _item(2)]
    lists[NAMED] = [_item(1)]
    sc.sync_all.apply(args=[_job()]).get()
    assert walked == [NAMED, AGG]
    with base.sync_session() as s:
        pend = {p.external_id: p.collection_id for p in s.scalars(select(PendingImport))}
        named = s.scalar(select(FollowedCollection).where(FollowedCollection.list_id == NAMED))
        agg = s.scalar(select(FollowedCollection).where(FollowedCollection.list_id == AGG))
        assert pend == {"1": named.id, "2": agg.id}


@pytest.mark.usefixtures("db_session")
def test_pending_under_aggregate_is_rehomed_to_named(stub_lists):
    lists, _ = stub_lists
    with base.sync_session() as s:
        agg = _fc(AGG, "All collected models", aggregate=True)
        named = _fc(NAMED, "Things to print")
        s.add_all([agg, named])
        s.commit()
        _own(s, NAMED, [1])
        s.add(
            PendingImport(
                collection_id=agg.id, site=SITE, external_id="1", title="M1", url="https://x/1"
            )
        )
        s.commit()
        named_id = named.id
    lists[AGG] = [_item(1)]
    lists[NAMED] = [_item(1)]
    sc.sync_all.apply(args=[_job()]).get()
    with base.sync_session() as s:
        rows = list(s.scalars(select(PendingImport)))
        assert [(r.external_id, r.collection_id) for r in rows] == [("1", named_id)]


def _imported(s, ext_id, collection):
    model = Model(
        name=f"M{ext_id}",
        slug=f"m{ext_id}",
        source_collection_id=collection.id,
        source_collection_title=collection.title,
    )
    s.add(model)
    s.commit()
    s.add(
        Import(
            url=f"https://makerworld.com/en/models/{ext_id}",
            site=SITE,
            external_id=str(ext_id),
            state=ImportState.DONE,
            model_id=model.id,
            collection_id=collection.id,
        )
    )
    s.commit()
    return model.id


@pytest.mark.usefixtures("db_session")
def test_backfill_rehomes_import_and_model_from_aggregate_to_named(stub_lists):
    lists, _ = stub_lists
    with base.sync_session() as s:
        agg = _fc(AGG, "All collected models", aggregate=True)
        named = _fc(NAMED, "Things to print")
        s.add_all([agg, named])
        s.commit()
        _own(s, NAMED, [1])
        mid = _imported(s, 1, agg)
        named_id = named.id
    lists[AGG] = [_item(1)]
    lists[NAMED] = [_item(1)]
    sc.sync_all.apply(args=[_job()]).get()
    with base.sync_session() as s:
        imp = s.scalar(select(Import).where(Import.external_id == "1"))
        model = s.get(Model, mid)
        assert imp.collection_id == named_id
        assert model.source_collection_id == named_id
        assert model.source_collection_title == "Things to print"


@pytest.mark.usefixtures("db_session")
def test_backfill_does_not_override_a_named_stamp(stub_lists):
    lists, _ = stub_lists
    with base.sync_session() as s:
        a = _fc("111", "First")
        b = _fc("222", "Second")
        s.add_all([a, b])
        s.commit()
        mid = _imported(s, 1, a)
        aid = a.id
    lists["111"] = [_item(1)]
    lists["222"] = [_item(1)]
    sc.sync_all.apply(args=[_job()]).get()
    with base.sync_session() as s:
        assert s.get(Model, mid).source_collection_id == aid


@pytest.mark.usefixtures("db_session")
def test_autofollow_named_is_idempotent_and_inherits_mode(stub_lists):
    with base.sync_session() as s:
        s.add(_fc(AGG, "All collected models", aggregate=True, mode=CollectionSyncMode.AUTO))
        s.add_all(
            [
                RemoteCollectionCache(site=SITE, list_id=NAMED, title="Things to print"),
                RemoteCollectionCache(site=SITE, list_id="2", title="ESP32"),
            ]
        )
        s.commit()
        assert svc.autofollow_named_sync(s) == 2
        assert svc.autofollow_named_sync(s) == 0
        rows = {r.list_id: r for r in s.scalars(select(FollowedCollection))}
        assert set(rows) == {AGG, NAMED, "2"}
        assert rows[NAMED].mode == CollectionSyncMode.AUTO
        assert rows[NAMED].title == "Things to print" and not rows[NAMED].is_aggregate


@pytest.mark.usefixtures("db_session")
def test_autofollow_noop_without_a_followed_aggregate():
    with base.sync_session() as s:
        s.add(RemoteCollectionCache(site=SITE, list_id=NAMED, title="X"))
        s.commit()
        assert svc.autofollow_named_sync(s) == 0


async def test_follow_marks_makerworld_all_collected_models_as_aggregate(db_session):
    row = await svc.follow(
        db_session, site=SITE, list_id=AGG, kind="collection", title="All collected models"
    )
    other = await svc.follow(db_session, site=SITE, list_id="9", kind="collection", title="Named")
    assert row.is_aggregate is True and other.is_aggregate is False


async def test_unfollowed_named_folder_stays_unfollowed_until_explicitly_refollowed(db_session):
    agg = await svc.follow(
        db_session, site=SITE, list_id=AGG, kind="collection", title="All collected models"
    )
    db_session.add(RemoteCollectionCache(site=SITE, list_id=NAMED, title="Things to print"))
    await db_session.commit()
    assert await svc.autofollow_named(db_session) == 1
    named = (
        await db_session.execute(
            select(FollowedCollection).where(FollowedCollection.list_id == NAMED)
        )
    ).scalar_one()

    await svc.unfollow(db_session, named.id)
    assert await svc.autofollow_named(db_session) == 0
    assert await db_session.run_sync(lambda s: svc.autofollow_named_sync(s)) == 0  # sync twin
    left = (await db_session.execute(select(FollowedCollection))).scalars().all()
    assert [r.list_id for r in left] == [agg.list_id]

    # explicit re-follow clears the tombstone: it's followed again, and a later
    # unfollow re-writes it
    again = await svc.follow(db_session, site=SITE, list_id=NAMED, kind="collection", title="T")
    dismissed = (await db_session.execute(select(AutofollowDismissed))).scalars().all()
    assert dismissed == []
    await svc.unfollow(db_session, again.id)
    assert await svc.autofollow_named(db_session) == 0
