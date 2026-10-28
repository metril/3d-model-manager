import pytest

from app.models.enums import ImportSite, ImportState
from app.models.system import Import
from app.services import imports as imports_service


async def _seed(db_session, *, state: ImportState, external_id: str = "42") -> Import:
    imp = Import(
        url=f"https://fake.test/thing/{external_id}",
        site=ImportSite.THINGIVERSE,
        external_id=external_id,
        state=state,
        error="boom" if state == ImportState.FAILED else None,
    )
    db_session.add(imp)
    await db_session.commit()
    await db_session.refresh(imp)
    return imp


@pytest.mark.asyncio
async def test_dismiss_is_idempotent_and_retry_clears_it(
    authenticated_client, library_root, data_dir, db_session, monkeypatch
):
    imp = await _seed(db_session, state=ImportState.FAILED)
    first = await authenticated_client.post(f"/api/imports/{imp.id}/dismiss")
    assert first.status_code == 200, first.text
    stamp = first.json()["dismissed_at"]
    assert stamp is not None
    again = await authenticated_client.post(f"/api/imports/{imp.id}/dismiss")
    assert again.status_code == 200 and again.json()["dismissed_at"] == stamp

    monkeypatch.setattr(
        imports_service.import_from_url, "apply_async", lambda *, args, task_id: None
    )
    r = await authenticated_client.post(f"/api/imports/{imp.id}/retry")
    assert r.status_code == 200 and r.json()["dismissed_at"] is None


@pytest.mark.asyncio
async def test_dismiss_non_failed_is_409_and_unknown_is_404(
    authenticated_client, library_root, data_dir, db_session
):
    imp = await _seed(db_session, state=ImportState.DONE)
    assert (await authenticated_client.post(f"/api/imports/{imp.id}/dismiss")).status_code == 409
    assert (await authenticated_client.post("/api/imports/999999/dismiss")).status_code == 404


@pytest.mark.asyncio
async def test_dismiss_failed_only_touches_undismissed_failed_rows(
    authenticated_client, library_root, data_dir, db_session
):
    a = await _seed(db_session, state=ImportState.FAILED, external_id="1")
    b = await _seed(db_session, state=ImportState.FAILED, external_id="2")
    done = await _seed(db_session, state=ImportState.DONE, external_id="3")
    await authenticated_client.post(f"/api/imports/{a.id}/dismiss")

    r = await authenticated_client.post("/api/imports/dismiss-failed")
    assert r.status_code == 200 and r.json() == {"dismissed": 1}
    rows = {x["id"]: x for x in (await authenticated_client.get("/api/imports")).json()}
    assert rows[a.id]["dismissed_at"] and rows[b.id]["dismissed_at"]
    assert rows[done.id]["dismissed_at"] is None
    assert (await authenticated_client.post("/api/imports/dismiss-failed")).json() == {
        "dismissed": 0
    }


@pytest.mark.asyncio
async def test_list_filters_offset_and_failed_count(
    authenticated_client, library_root, data_dir, db_session
):
    ids = [
        (await _seed(db_session, state=ImportState.FAILED, external_id=str(i))).id for i in range(3)
    ]
    await _seed(db_session, state=ImportState.DONE, external_id="d")
    await authenticated_client.post(f"/api/imports/{ids[0]}/dismiss")

    assert (await authenticated_client.get("/api/imports/failed-count")).json() == {"count": 2}
    failed = (await authenticated_client.get("/api/imports?state=failed")).json()
    assert {r["id"] for r in failed} == set(ids)
    hidden = (
        await authenticated_client.get("/api/imports?state=failed&include_dismissed=false")
    ).json()
    assert {r["id"] for r in hidden} == set(ids[1:])
    page = (await authenticated_client.get("/api/imports?state=failed&limit=2&offset=2")).json()
    assert len(page) == 1
    assert (await authenticated_client.get("/api/imports?limit=0")).status_code == 422
