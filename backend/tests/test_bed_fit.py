"""Pre-flight bed-fit: pure math, the file bed-fit endpoint and the slice guard."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import update

from app.models.enums import PrinterKind
from app.models.printing import Printer
from app.models.processing import BlobMeta
from app.models.slicing import SlicerProfile
from app.services.bed_fit import check_fit, parse_printable_height
from tests.test_slice_jobs import FakeClient, _setup

pytestmark = pytest.mark.usefixtures("library_root")


def test_fits_with_margin() -> None:
    r = check_fit((178, 100, 50), (180, 180), 180)
    assert r.status == "fits" and r.fits and not r.rotated and r.overflow == {}
    assert r.required_bed == (179, 101)


def test_margin_makes_exact_size_too_large() -> None:
    r = check_fit((180, 100, 10), (180, 180))
    assert r.status == "too_large" and r.overflow == {"x": 1.0}
    assert check_fit((180, 100, 10), (180, 180), margin_mm=0).status == "fits"


def test_rotated_fits() -> None:
    r = check_fit((200, 100, 10), (120, 250))
    assert r.status == "rotated" and r.fits and r.rotated
    assert r.required_bed == (101, 201)


def test_too_large_reports_best_orientation_overflow() -> None:
    r = check_fit((212, 100, 10), (180, 180))
    assert r.status == "too_large" and not r.fits and r.overflow == {"x": 33.0}


def test_height() -> None:
    r = check_fit((50, 50, 260), (256, 256), 250)
    assert r.status == "too_large" and r.overflow == {"z": 10.0}
    assert check_fit((50, 50, 260), (256, 256)).status == "fits"


def test_parse_printable_height() -> None:
    assert parse_printable_height({"printable_height": "256"}) == 256.0
    assert parse_printable_height({"max_print_height": 180}) == 180.0
    assert parse_printable_height({}) is None


_AREA = {"printable_area": ["0x0", "180x0", "180x180", "0x180"], "printable_height": "180"}


async def _prep(db, seed_file, slug, dims, with_printer=True):
    model, f, recipe = await _setup(db, seed_file, slug)
    await db.execute(
        update(SlicerProfile)
        .where(SlicerProfile.id == recipe.printer_profile_id)
        .values(data=_AREA)
    )
    if dims is not None:
        db.add(BlobMeta(blob_hash=f.blob_hash, dims_mm=dims))
    printer = None
    if with_printer:
        printer = Printer(
            name=slug, kind=PrinterKind.BAMBU_LAN, host="h", serial=slug, access_code_enc="x"
        )
        db.add(printer)
        await db.flush()
        recipe.target_printer_id = printer.id
    await db.commit()
    return model, f, recipe, printer


@pytest.mark.parametrize(
    ("dims", "expect"),
    [([100, 100, 50], "fits"), ([212, 100, 50], "too_large"), (None, "unknown")],
)
async def test_endpoint_statuses(
    authenticated_client: httpx.AsyncClient, db_session, seed_file, dims, expect
) -> None:
    model, f, _, p = await _prep(db_session, seed_file, f"bf-{expect}", dims)
    r = await authenticated_client.get(
        f"/api/models/{model.slug}/files/{f.id}/bed-fit", params={"printer_id": p.id}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == expect


async def test_endpoint_unknown_without_bed(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    model, f, _, _ = await _prep(db_session, seed_file, "bf-noprof", [500, 500, 50], False)
    r = await authenticated_client.get(
        f"/api/models/{model.slug}/files/{f.id}/bed-fit", params={"printer_id": 999999}
    )
    assert r.status_code == 200 and r.json()["status"] == "unknown"


async def test_slice_guard_422_and_force(
    authenticated_client: httpx.AsyncClient, db_session, seed_file, monkeypatch
) -> None:
    from app.tasks import slicing
    from tests.server_helpers import set_server

    _, f, recipe, _ = await _prep(db_session, seed_file, "bf-guard", [212, 100, 50], False)
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: FakeClient(error=RuntimeError()))
    r = await authenticated_client.post(f"/api/files/{f.id}/slice", json={"recipe_id": recipe.id})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "does_not_fit" and d["overflow"] == {"x": 33.0}
    r = await authenticated_client.post(
        f"/api/files/{f.id}/slice", json={"recipe_id": recipe.id, "force": True}
    )
    assert r.status_code == 201, r.text


async def test_slice_guard_skips_sliced_blob(
    authenticated_client: httpx.AsyncClient, db_session, seed_file, monkeypatch
) -> None:
    from app.tasks import slicing
    from tests.server_helpers import set_server

    _, f, recipe, _ = await _prep(db_session, seed_file, "bf-sliced", [500, 500, 50], False)
    await db_session.execute(
        update(BlobMeta).where(BlobMeta.blob_hash == f.blob_hash).values(print_time_s=60)
    )
    await db_session.commit()
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: FakeClient(error=RuntimeError()))
    r = await authenticated_client.post(f"/api/files/{f.id}/slice", json={"recipe_id": recipe.id})
    assert r.status_code == 201, r.text


def test_rotation_only_counts_when_slicer_orients() -> None:
    r = check_fit((200, 100, 10), (120, 250), allow_rotate=False)
    assert r.status == "too_large" and r.rotated and r.overflow == {"x": 81.0}
    assert r.dims == (200, 100, 10)


def test_every_overflowing_axis_listed() -> None:
    r = check_fit((300, 300, 300), (180, 180), 180)
    assert r.overflow == {"x": 121.0, "y": 121.0, "z": 120.0}


async def test_endpoint_prefers_recipe_bed(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    model, f, recipe, p = await _prep(db_session, seed_file, "bf-recipe", [212, 100, 50])
    r = await authenticated_client.get(
        f"/api/models/{model.slug}/files/{f.id}/bed-fit", params={"recipe_id": recipe.id}
    )
    assert r.status_code == 200 and r.json()["status"] == "too_large"


async def test_multi_plate_3mf_is_advisory_only(
    authenticated_client: httpx.AsyncClient, db_session, seed_file, monkeypatch
) -> None:
    from app.models.enums import BlobFormat, BlobKind
    from app.models.library import Revision
    from app.tasks import slicing
    from tests.server_helpers import set_server

    model, f, recipe, _ = await _prep(db_session, seed_file, "bf-3mf", None, False)
    rev = await db_session.get(Revision, f.revision_id)
    f3 = await seed_file(
        model, rev, "proj.3mf", b"3mf", blob_format=BlobFormat.THREEMF, blob_kind=BlobKind.MESH
    )
    db_session.add(BlobMeta(blob_hash=f3.blob_hash, dims_mm=[900, 200, 50], plate_count=4))
    await db_session.commit()
    r = await authenticated_client.get(
        f"/api/models/{model.slug}/files/{f3.id}/bed-fit", params={"recipe_id": recipe.id}
    )
    assert r.json()["status"] == "unknown"
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: FakeClient(error=RuntimeError()))
    r = await authenticated_client.post(f"/api/files/{f3.id}/slice", json={"recipe_id": recipe.id})
    assert r.status_code == 201, r.text


async def test_file_bed_fit_without_slug(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    _, f, recipe, _ = await _prep(db_session, seed_file, "bf-slugless", [212, 100, 50], False)
    r = await authenticated_client.get(
        f"/api/files/{f.id}/bed-fit", params={"recipe_id": recipe.id}
    )
    assert r.status_code == 200 and r.json()["status"] == "too_large"


async def test_queue_guard_422_and_force(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    from tests.server_helpers import set_server

    _, f, recipe, _ = await _prep(db_session, seed_file, "bf-queue", [212, 100, 50], False)
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    body = {"file_id": f.id, "recipe_id": recipe.id}
    r = await authenticated_client.post("/api/queue", json=body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "does_not_fit"
    r = await authenticated_client.post("/api/queue", json={**body, "force": True})
    assert r.status_code == 201, r.text


def _zip3mf(*names: str) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        for n in names:
            zf.writestr(n, "{}")
    return buf.getvalue()


async def _seed_3mf(db, seed_file, slug, data, dims):
    from app.models.enums import BlobFormat, BlobKind
    from app.models.library import Revision

    model, f, recipe, _ = await _prep(db, seed_file, slug, None, False)
    rev = await db.get(Revision, f.revision_id)
    f3 = await seed_file(
        model, rev, "proj.3mf", data, blob_format=BlobFormat.THREEMF, blob_kind=BlobKind.MESH
    )
    db.add(BlobMeta(blob_hash=f3.blob_hash, dims_mm=dims))
    await db.commit()
    return model, f3, recipe


async def test_3mf_without_plate_count_is_computed_and_persisted(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    model, f3, recipe = await _seed_3mf(db_session, seed_file, "bf-pc1", _zip3mf(), [315, 61, 31])
    r = await authenticated_client.get(
        f"/api/models/{model.slug}/files/{f3.id}/bed-fit", params={"recipe_id": recipe.id}
    )
    body = r.json()
    assert body["status"] == "too_large" and body["height"] == 180.0 and body["bed"] == [180, 180]
    meta = await db_session.get(BlobMeta, f3.blob_hash, populate_existing=True)
    assert meta.plate_count == 1


async def test_multi_plate_project_without_plate_count_is_unknown(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    data = _zip3mf("Metadata/plate_1.json", "Metadata/plate_2.json", "Metadata/plate_3.json")
    model, f3, recipe = await _seed_3mf(db_session, seed_file, "bf-pc3", data, [900, 61, 31])
    r = await authenticated_client.get(
        f"/api/files/{f3.id}/bed-fit", params={"recipe_id": recipe.id}
    )
    assert r.json()["status"] == "unknown"
    meta = await db_session.get(BlobMeta, f3.blob_hash, populate_existing=True)
    assert meta.plate_count == 3


async def test_batch_dispatch_guard_lists_files_and_force(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    from tests.server_helpers import set_server

    _, f, recipe, _ = await _prep(db_session, seed_file, "bf-batch", [212, 100, 50], False)
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    r = await authenticated_client.post(
        "/api/batches",
        json={"name": "b", "file_id": f.id, "recipe_id": recipe.id, "quantity": 2},
    )
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    r = await authenticated_client.post(f"/api/batches/{bid}/dispatch")
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "does_not_fit"
    assert [(x["id"], x["fit"]["status"]) for x in d["files"]] == [(f.id, "too_large")]
    assert d["files"][0]["name"]
    r = await authenticated_client.post(f"/api/batches/{bid}/dispatch", json={"force": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"


async def test_guard_files_fit_lists_every_offender(db_session, seed_file) -> None:
    from fastapi import HTTPException

    from app.models.slicing import PrintRecipe
    from app.services.bed_fit import guard_files_fit

    _, f, recipe, _ = await _prep(db_session, seed_file, "bf-many", [212, 100, 50], False)
    recipe = await db_session.get(PrintRecipe, recipe.id)
    with pytest.raises(HTTPException) as ei:
        await guard_files_fit(db_session, recipe, [f, f])
    assert ei.value.detail["code"] == "does_not_fit"
    assert [x["id"] for x in ei.value.detail["files"]] == [f.id, f.id]


async def test_pipeline_queue_step_guard_and_force(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    from tests.server_helpers import set_server

    _, f, recipe, _ = await _prep(db_session, seed_file, "bf-pipe", [212, 100, 50], False)
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    r = await authenticated_client.post(
        "/api/slicer-pipelines",
        json={"name": "q", "recipe_id": recipe.id, "steps": [{"kind": "queue"}]},
    )
    pid = r.json()["id"]
    r = await authenticated_client.post(f"/api/slicer-pipelines/{pid}/run", json={"file_id": f.id})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "does_not_fit"
    r = await authenticated_client.post(
        f"/api/slicer-pipelines/{pid}/run", json={"file_id": f.id, "force": True}
    )
    assert r.status_code == 201 and r.json()["kind"] == "queue", r.text
