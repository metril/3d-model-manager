"""Per-model print history (Branch 5 Task 1): a user-entered log of print
attempts.

``POST /models/{model_id}/prints``/``GET /models/{model_id}/prints``/
``PATCH /prints/{id}``/``DELETE /prints/{id}``, plus ``ModelDetail``'s
``print_count``/``last_printed_at`` aggregates.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.library import Model, Print
from app.services import prints as prints_service

pytestmark = pytest.mark.usefixtures("library_root")


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


async def test_create_print_defaults_printed_at_and_result(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Defaults")

    response = await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["model_id"] == model["id"]
    assert body["result"] == "success"
    assert body["printed_at"] is not None
    assert body["printer_name"] is None
    assert body["filament"] is None
    assert body["duration_min"] is None
    assert body["notes"] is None
    assert body["created_at"] is not None


async def test_create_print_accepts_all_fields(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Print Full")

    response = await authenticated_client.post(
        f"/api/models/{model['id']}/prints",
        json={
            "printed_at": "2026-07-01T10:00:00Z",
            "printer_name": "Bambu X1C",
            "filament": "PLA Black",
            "filament_g": 42.5,
            "result": "fail",
            "duration_min": 125,
            "notes": "warped corner",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["printed_at"] == "2026-07-01T10:00:00Z"
    assert body["printer_name"] == "Bambu X1C"
    assert body["filament"] == "PLA Black"
    assert body["filament_g"] == 42.5
    assert body["result"] == "fail"
    assert body["duration_min"] == 125
    assert body["notes"] == "warped corner"


async def test_create_print_negative_filament_g_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Negative Filament")

    response = await authenticated_client.post(
        f"/api/models/{model['id']}/prints", json={"filament_g": -1}
    )

    assert response.status_code == 422


async def test_patch_print_filament_g(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Print Patch Filament")
    created = await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})
    print_id = created.json()["id"]

    response = await authenticated_client.patch(
        f"/api/prints/{print_id}", json={"filament_g": 12.0}
    )

    assert response.status_code == 200, response.text
    assert response.json()["filament_g"] == 12.0


async def test_create_print_unknown_model_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.post("/api/models/999999/prints", json={})

    assert response.status_code == 404


async def test_create_print_invalid_result_is_422(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Print Invalid Result")

    response = await authenticated_client.post(
        f"/api/models/{model['id']}/prints", json={"result": "bogus"}
    )

    assert response.status_code == 422


async def test_create_print_negative_duration_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Negative Duration")

    response = await authenticated_client.post(
        f"/api/models/{model['id']}/prints", json={"duration_min": -1}
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# list / ordering
# ---------------------------------------------------------------------------


async def test_list_prints_unknown_model_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.get("/api/models/999999/prints")

    assert response.status_code == 404


async def test_list_prints_is_reverse_chronological_with_id_tiebreak(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Ordering")

    # Two rows share the SAME `printed_at` -- the higher id (created second)
    # must sort first, proving the `id desc` tiebreak actually fires.
    earliest = await authenticated_client.post(
        f"/api/models/{model['id']}/prints",
        json={"printed_at": "2026-06-01T00:00:00Z"},
    )
    tied_a = await authenticated_client.post(
        f"/api/models/{model['id']}/prints",
        json={"printed_at": "2026-06-15T00:00:00Z"},
    )
    tied_b = await authenticated_client.post(
        f"/api/models/{model['id']}/prints",
        json={"printed_at": "2026-06-15T00:00:00Z"},
    )
    latest = await authenticated_client.post(
        f"/api/models/{model['id']}/prints",
        json={"printed_at": "2026-07-01T00:00:00Z"},
    )
    for r in (earliest, tied_a, tied_b, latest):
        assert r.status_code == 201, r.text

    listing = await authenticated_client.get(f"/api/models/{model['id']}/prints")
    assert listing.status_code == 200
    ids = [row["id"] for row in listing.json()]
    assert ids == [
        latest.json()["id"],
        tied_b.json()["id"],
        tied_a.json()["id"],
        earliest.json()["id"],
    ]


async def test_list_prints_only_returns_rows_for_that_model(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model_a = await _create_model(authenticated_client, "Print Scope A")
    model_b = await _create_model(authenticated_client, "Print Scope B")

    await authenticated_client.post(f"/api/models/{model_a['id']}/prints", json={})
    await authenticated_client.post(f"/api/models/{model_b['id']}/prints", json={})
    await authenticated_client.post(f"/api/models/{model_b['id']}/prints", json={})

    listing_a = await authenticated_client.get(f"/api/models/{model_a['id']}/prints")
    listing_b = await authenticated_client.get(f"/api/models/{model_b['id']}/prints")

    assert len(listing_a.json()) == 1
    assert len(listing_b.json()) == 2
    assert all(row["model_id"] == model_a["id"] for row in listing_a.json())
    assert all(row["model_id"] == model_b["id"] for row in listing_b.json())


async def test_list_prints_empty_for_model_with_no_prints(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print None Yet")

    listing = await authenticated_client.get(f"/api/models/{model['id']}/prints")

    assert listing.status_code == 200
    assert listing.json() == []


async def test_model_prints_hides_auto_runs_from_invisible_printers(
    authenticated_client: httpx.AsyncClient,
    make_user,
    make_group,
    login_as,
    db_session: AsyncSession,
) -> None:
    """A member whose printer ACL only covers p1 sees the manual print plus
    the auto-archived run on p1, never the one on hidden p2 -- an admin
    sees all three (P3 Task 2).
    """
    from app.models import Printer, PrintJob
    from app.models.enums import PrinterKind, PrintJobOrigin, PrintJobState, PrintSource

    model = await _create_model(authenticated_client, "Prints Visibility")
    await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})

    p1 = Printer(
        name="p1", kind=PrinterKind.BAMBU_LAN, host="10.0.0.9", serial="p1", access_code_enc="x"
    )
    p2 = Printer(
        name="p2", kind=PrinterKind.BAMBU_LAN, host="10.0.0.9", serial="p2", access_code_enc="x"
    )
    db_session.add_all([p1, p2])
    await db_session.flush()
    job1 = PrintJob(printer_id=p1.id, origin=PrintJobOrigin.EXTERNAL, state=PrintJobState.FINISHED)
    job2 = PrintJob(printer_id=p2.id, origin=PrintJobOrigin.EXTERNAL, state=PrintJobState.FINISHED)
    db_session.add_all([job1, job2])
    await db_session.flush()
    db_session.add_all(
        [
            Print(model_id=model["id"], print_job_id=job1.id, source=PrintSource.AUTO),
            Print(model_id=model["id"], print_job_id=job2.id, source=PrintSource.AUTO),
        ]
    )
    await db_session.commit()

    user = await make_user("member")
    await make_group("printer-folks", members=[user], printer_access={p1.id: (True, False, False)})
    member_client = await login_as(user)

    member_listing = await member_client.get(f"/api/models/{model['id']}/prints")
    assert member_listing.status_code == 200
    assert len(member_listing.json()) == 2

    admin_listing = await authenticated_client.get(f"/api/models/{model['id']}/prints")
    assert len(admin_listing.json()) == 3


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------


async def test_patch_print_only_changes_sent_fields(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Patch")
    created = await authenticated_client.post(
        f"/api/models/{model['id']}/prints",
        json={"printer_name": "Bambu X1C", "filament": "PLA Black", "result": "success"},
    )
    print_id = created.json()["id"]

    response = await authenticated_client.patch(
        f"/api/prints/{print_id}", json={"result": "fail", "notes": "nozzle clog"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "fail"
    assert body["notes"] == "nozzle clog"
    # Untouched fields survive the partial patch.
    assert body["printer_name"] == "Bambu X1C"
    assert body["filament"] == "PLA Black"


async def test_patch_print_invalid_result_is_422(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Print Patch Invalid")
    created = await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})
    print_id = created.json()["id"]

    response = await authenticated_client.patch(f"/api/prints/{print_id}", json={"result": "bogus"})

    assert response.status_code == 422


async def test_patch_print_negative_duration_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Patch Negative")
    created = await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})
    print_id = created.json()["id"]

    response = await authenticated_client.patch(
        f"/api/prints/{print_id}", json={"duration_min": -5}
    )

    assert response.status_code == 422


async def test_patch_print_unknown_id_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.patch("/api/prints/999999", json={"result": "fail"})

    assert response.status_code == 404


async def test_patch_print_explicit_null_clears_nullable_fields(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Patch Null Clear")
    created = await authenticated_client.post(
        f"/api/models/{model['id']}/prints",
        json={"printer_name": "Bambu X1C", "filament": "PLA Black"},
    )
    print_id = created.json()["id"]

    response = await authenticated_client.patch(
        f"/api/prints/{print_id}", json={"printer_name": None, "filament": None}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["printer_name"] is None
    assert body["filament"] is None


async def test_patch_print_explicit_null_result_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Patch Null Result")
    created = await authenticated_client.post(
        f"/api/models/{model['id']}/prints", json={"result": "success"}
    )
    print_id = created.json()["id"]

    response = await authenticated_client.patch(f"/api/prints/{print_id}", json={"result": None})

    assert response.status_code == 422
    unchanged = await authenticated_client.get(f"/api/models/{model['id']}/prints")
    assert unchanged.json()[0]["result"] == "success"


async def test_patch_print_explicit_null_printed_at_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Patch Null Printed At")
    created = await authenticated_client.post(
        f"/api/models/{model['id']}/prints",
        json={"printed_at": "2026-07-01T10:00:00Z"},
    )
    print_id = created.json()["id"]

    response = await authenticated_client.patch(
        f"/api/prints/{print_id}", json={"printed_at": None}
    )

    assert response.status_code == 422
    unchanged = await authenticated_client.get(f"/api/models/{model['id']}/prints")
    assert unchanged.json()[0]["printed_at"] == "2026-07-01T10:00:00Z"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_print_removes_it(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Print Delete")
    created = await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})
    print_id = created.json()["id"]

    response = await authenticated_client.delete(f"/api/prints/{print_id}")
    assert response.status_code == 204

    listing = await authenticated_client.get(f"/api/models/{model['id']}/prints")
    assert listing.json() == []


async def test_delete_print_unknown_id_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.delete("/api/prints/999999")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# model delete cascades prints
# ---------------------------------------------------------------------------


async def test_model_hard_delete_cascades_prints(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M1 only supports soft-delete (archive) via the API -- the cascade is
    exercised at the DB level directly (a raw SQL DELETE, since there is no
    hard-delete endpoint to drive it through HTTP), mirroring the print
    queue's matching cascade test.
    """
    model = await _create_model(authenticated_client, "Print Cascade")
    created = await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})
    print_id = created.json()["id"]

    await db_session.execute(text("DELETE FROM models WHERE id = :id"), {"id": model["id"]})
    await db_session.commit()

    remaining = (
        await db_session.execute(select(Print).where(Print.id == print_id))
    ).scalar_one_or_none()
    assert remaining is None


# ---------------------------------------------------------------------------
# ModelDetail aggregates
# ---------------------------------------------------------------------------


async def test_model_detail_print_aggregates_zero_state(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Aggregate Zero")

    detail = await authenticated_client.get(f"/api/models/{model['slug']}")

    assert detail.status_code == 200
    body = detail.json()
    assert body["print_count"] == 0
    assert body["last_printed_at"] is None


async def test_model_detail_print_aggregates_reflect_rows(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Print Aggregate Rows")

    await authenticated_client.post(
        f"/api/models/{model['id']}/prints", json={"printed_at": "2026-06-01T00:00:00Z"}
    )
    await authenticated_client.post(
        f"/api/models/{model['id']}/prints", json={"printed_at": "2026-07-01T00:00:00Z"}
    )
    await authenticated_client.post(
        f"/api/models/{model['id']}/prints", json={"printed_at": "2026-05-01T00:00:00Z"}
    )

    detail = await authenticated_client.get(f"/api/models/{model['slug']}")

    assert detail.status_code == 200
    body = detail.json()
    assert body["print_count"] == 3
    assert body["last_printed_at"] == "2026-07-01T00:00:00Z"


# ---------------------------------------------------------------------------
# print_count (R13b): single writer in app.services.prints, self-healed by
# recount_print_counts on scan.
# ---------------------------------------------------------------------------


async def test_create_print_increments_model_print_count(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    model = await _create_model(authenticated_client, "Print Count Increment")

    await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})
    await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})

    row = await db_session.get(Model, model["id"])
    await db_session.refresh(row)
    assert row.print_count == 2


async def test_delete_print_decrements_model_print_count(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    model = await _create_model(authenticated_client, "Print Count Decrement")
    created = await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})
    await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})
    print_id = created.json()["id"]

    response = await authenticated_client.delete(f"/api/prints/{print_id}")
    assert response.status_code == 204

    row = await db_session.get(Model, model["id"])
    await db_session.refresh(row)
    assert row.print_count == 1


async def test_recount_print_counts_fixes_drift(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    model = await _create_model(authenticated_client, "Print Count Drift")
    await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})
    await authenticated_client.post(f"/api/models/{model['id']}/prints", json={})

    # Simulate drift: manually corrupt the denormalized counter, bypassing
    # the single writer (app.services.prints).
    await db_session.execute(
        text("UPDATE models SET print_count = 999 WHERE id = :id"), {"id": model["id"]}
    )
    await db_session.commit()

    await prints_service.recount_print_counts(db_session)

    row = await db_session.get(Model, model["id"])
    await db_session.refresh(row)
    assert row.print_count == 2


async def test_recount_print_counts_resets_models_with_zero_prints(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    model = await _create_model(authenticated_client, "Print Count Zero Drift")
    await db_session.execute(
        text("UPDATE models SET print_count = 5 WHERE id = :id"), {"id": model["id"]}
    )
    await db_session.commit()

    await prints_service.recount_print_counts(db_session)

    row = await db_session.get(Model, model["id"])
    await db_session.refresh(row)
    assert row.print_count == 0


async def test_model_detail_print_aggregates_do_not_leak_across_models(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model_a = await _create_model(authenticated_client, "Print Aggregate A")
    model_b = await _create_model(authenticated_client, "Print Aggregate B")

    await authenticated_client.post(f"/api/models/{model_a['id']}/prints", json={})

    detail_b = await authenticated_client.get(f"/api/models/{model_b['slug']}")

    assert detail_b.json()["print_count"] == 0
    assert detail_b.json()["last_printed_at"] is None


# ---------------------------------------------------------------------------
# P2 archive: server-side cost, ownership, model-less runs
# ---------------------------------------------------------------------------


@pytest.fixture
def make_auto_run(db_session: AsyncSession):
    async def _make(owner: int | None, **kw: object) -> int:
        row = Print(source="auto", model_id=None, created_by_user_id=owner, **kw)
        db_session.add(row)
        await db_session.commit()
        return row.id

    return _make


async def test_manual_print_gets_server_cost(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Costed")
    r = await authenticated_client.post(
        f"/api/models/{model['id']}/prints", json={"filament_g": 250, "duration_min": 120}
    )
    body = r.json()
    assert body["filament_cost"] == 5.0 and body["total_cost"] == 5.0
    assert body["source"] == "manual"


async def test_patch_run_ownership_and_cost(
    member_client: httpx.AsyncClient, member, admin_user, make_auto_run
) -> None:
    other = await make_auto_run(admin_user.id)
    unowned = await make_auto_run(None)
    assert (
        await member_client.patch(f"/api/prints/{other}", json={"quality": "good"})
    ).status_code == 403
    assert (
        await member_client.patch(f"/api/prints/{unowned}", json={"quality": "good"})
    ).status_code == 403
    assert (
        await member_client.patch("/api/prints/999999", json={"quality": "good"})
    ).status_code == 404
    own = await make_auto_run(member.id, filament_g=100, machine_cost=3.0)
    r = await member_client.patch(
        f"/api/prints/{own}",
        json={"quality": "reject", "failure_reason": "spaghetti", "filament_g": 50},
    )
    body = r.json()
    assert body["quality"] == "reject" and body["failure_reason"] == "spaghetti"
    assert body["filament_cost"] == 1.0 and body["machine_cost"] == 3.0
    assert body["total_cost"] == 4.0


async def test_patch_duration_rescales_wear_cost(
    authenticated_client: httpx.AsyncClient, make_auto_run
) -> None:
    rid = await make_auto_run(
        None, duration_min=60, machine_cost=1.0, wear_cost=0.5, total_cost=1.5
    )
    r = await authenticated_client.patch(f"/api/prints/{rid}", json={"duration_min": 120})
    body = r.json()
    assert body["wear_cost"] == pytest.approx(1.0)
    assert body["total_cost"] == pytest.approx(body["machine_cost"] + 1.0)


async def test_delete_model_less_run(
    authenticated_client: httpx.AsyncClient, make_auto_run
) -> None:
    rid = await make_auto_run(None)
    assert (await authenticated_client.delete(f"/api/prints/{rid}")).status_code == 204


async def test_recent_prints_include_model_less_runs(
    authenticated_client: httpx.AsyncClient, make_auto_run
) -> None:
    from app.services import stats

    stats._cache = None
    rid = await make_auto_run(None)
    r = await authenticated_client.get("/api/stats")
    assert rid in [p["id"] for p in r.json()["recent_prints"]]
