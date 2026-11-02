from datetime import UTC, datetime

import pytest

from app.models.library import Revision
from app.services import library


@pytest.mark.asyncio
async def test_manual_create_model_still_yields_rev_001_initial(db_session, backend):
    model = await library.create_model(db_session, backend, name="Manual Widget", description=None)
    cur = await db_session.get(Revision, model.current_revision_id)
    assert cur.dir_name == "rev-001_initial" and cur.name == "initial"
    assert model.source_site is None and model.imported_at is None


@pytest.mark.asyncio
async def test_create_imported_model_sync_sets_provenance_and_rev_imported(db_session, backend):
    # sync helper uses a SYNC session; drive it against the SAME testcontainer
    # DB via app.tasks.base.sync_session (the worker engine).
    from app.models.library import Model, Revision
    from app.tasks.base import sync_session

    when = datetime(2026, 7, 7, tzinfo=UTC)
    with sync_session() as s:
        model = library.create_imported_model_sync(
            s,
            backend,
            name="Imported Vase",
            description="from a gallery",
            source_url="https://www.thingiverse.com/thing:763622",
            source_site="thingiverse",
            source_author="alice",
            source_license="CC-BY-4.0",
            imported_at=when,
            tags=["vase", "spiral"],
            initial_revision_name="imported",
        )
        mid = model.id
    with sync_session() as s:
        m = s.get(Model, mid)
        rev = s.get(Revision, m.current_revision_id)
        assert rev.dir_name == "rev-001_imported" and rev.name == "imported"
        assert m.source_site == "thingiverse" and m.source_author == "alice"
        assert m.source_license == "CC-BY-4.0" and m.imported_at is not None
        assert {t.name for t in m.tags} == {"vase", "spiral"}


@pytest.mark.asyncio
async def test_create_imported_model_sync_commit_false_defers_visibility_to_caller(backend):
    """M6 Task 4 fix-review defect: the import worker must create the Model
    +Revision AND link ``imports.model_id`` in a SINGLE commit (Global
    Constraints "IMPORTS ATOMIC") -- two separate commits leave a window
    where the Model is durably committed but the link (and the import's
    state) is not, which no redelivery guard can tell apart from a
    legitimate in-flight link. ``commit=False`` lets the caller
    (``app.tasks.importing``) defer the commit until it has also set
    ``imp.model_id`` in the SAME transaction. Proven here via cross-
    connection visibility on the real Postgres testcontainer: with
    ``commit=False`` the row must stay invisible to an independent
    connection until the ORIGINAL caller commits."""
    from app.models.library import Model
    from app.tasks.base import sync_session

    with sync_session() as s1:
        model = library.create_imported_model_sync(
            s1,
            backend,
            name="Deferred Vase",
            description=None,
            source_url=None,
            source_site="thingiverse",
            source_author=None,
            source_license=None,
            imported_at=None,
            tags=[],
            commit=False,
        )
        model_id = model.id
        assert model_id is not None  # flush() already assigned the PK

        # Not yet committed by s1 -- must be invisible from an independent
        # connection (a real second connection against the testcontainer).
        with sync_session() as s2:
            assert s2.get(Model, model_id) is None

        s1.commit()

    # Now that the caller committed, a fresh connection sees it.
    with sync_session() as s3:
        assert s3.get(Model, model_id) is not None


def test_create_imported_model_sync_tags_are_case_insensitive(backend):
    from app.models.library import Model, Tag
    from app.tasks.base import sync_session

    with sync_session() as s:
        s.add(Tag(name="House"))
        s.commit()
    with sync_session() as s:
        model = library.create_imported_model_sync(
            s,
            backend,
            name="Tagged",
            description=None,
            source_url="https://example.com/1",
            source_site="thingiverse",
            source_author=None,
            source_license=None,
            imported_at=datetime(2026, 7, 7, tzinfo=UTC),
            tags=["house", "Lora", "lora ", " "],
            initial_revision_name="imported",
        )
        mid = model.id
    with sync_session() as s:
        m = s.get(Model, mid)
        assert sorted(t.name for t in m.tags) == ["House", "Lora"]
