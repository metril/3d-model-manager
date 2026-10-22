"""Print plan / send flow (P5 spool tray plan, Task 7)."""

from __future__ import annotations

import json

import httpx
import pytest
import redis as redis_lib
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Model, Printer, PrintJob, Revision
from app.models.enums import BlobFormat, BlobKind, PrinterKind, PrintJobState
from app.models.processing import BlobMeta
from app.printers.base import state_key
from tests import corpus

pytestmark = pytest.mark.usefixtures("library_root", "printer_enabled")

PLATES = [
    {
        "index": 1,
        "prediction_s": 3600,
        "weight_g": 12.5,
        "filaments": [{"filament_id": 1, "type": "PLA", "color": "#FF0000", "used_g": 12.5}],
    },
    {
        "index": 2,
        "prediction_s": 1800,
        "weight_g": 7.5,
        "filaments": [{"filament_id": 1, "type": "PETG", "color": "#0000FF", "used_g": 7.5}],
    },
]

TRAYS = [
    {"slot": 0, "ams_unit": 0, "tray": 0, "tray_type": "PLA", "color": "#FF0000"},
    {"slot": 1, "ams_unit": 0, "tray": 1, "tray_type": "PETG", "color": "#0000FF"},
    {"slot": 254, "ams_unit": 255, "tray": 0, "tray_type": "PLA", "color": "#FF0000"},
]


async def _sliced_file(db_session: AsyncSession, seed_file, *, printer_model="Bambu Lab A1 mini"):
    content = corpus.sliced_gcode_3mf()
    model = Model(slug="planm", name="Plan M")
    db_session.add(model)
    await db_session.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db_session.add(rev)
    await db_session.commit()
    await db_session.refresh(rev)
    f = await seed_file(
        model,
        rev,
        "job.gcode.3mf",
        content,
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    db_session.add(
        BlobMeta(
            blob_hash=f.blob_hash,
            printer_model=printer_model,
            plate_count=2,
            raw={"plates": PLATES},
        )
    )
    await db_session.commit()
    return f, model


async def _printer(db: AsyncSession, name: str = "P1", model: str = "A1 mini") -> Printer:
    from app.config import get_settings
    from app.crypto import encrypt_secret

    p = Printer(
        name=name,
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.1",
        serial=name,
        access_code_enc=encrypt_secret(get_settings(), "12345678"),
        model=model,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


def _seed_state(redis_url: str, printer_id: int, state: dict) -> None:
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(printer_id), json.dumps(state))
    c.close()


def _clear_state(redis_url: str, printer_id: int) -> None:
    c = redis_lib.Redis.from_url(redis_url)
    c.delete(state_key(printer_id))
    c.close()


async def test_plan_404_file(authenticated_client: httpx.AsyncClient):
    r = await authenticated_client.post("/api/print/plan", json={"file_id": 99999})
    assert r.status_code == 404


async def test_plan_422_non_sliced_file(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, seed_file
):
    model = Model(slug="stlm", name="Stl M")
    db_session.add(model)
    await db_session.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db_session.add(rev)
    await db_session.commit()
    await db_session.refresh(rev)
    f = await seed_file(model, rev, "box.stl", corpus.box_stl(), blob_format=BlobFormat.STL)
    r = await authenticated_client.post("/api/print/plan", json={"file_id": f.id})
    assert r.status_code == 422


async def test_plan_mapping_indices(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, seed_file, redis_url: str
):
    f, model = await _sliced_file(db_session, seed_file)
    p = await _printer(db_session)
    _seed_state(redis_url, p.id, {"gcode_state": "IDLE", "trays": TRAYS})
    try:
        r1 = await authenticated_client.post(
            "/api/print/plan", json={"file_id": f.id, "plate": 1, "printer_id": p.id}
        )
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert body1["model_id"] == model.id
        assert body1["plate"] == 1
        assert body1["filaments"][0]["status"] == "ok"
        assert body1["filaments"][0]["tray"] == 0
        assert body1["ams_mapping"] == [0]
        assert body1["use_ams"] is True

        r2 = await authenticated_client.post(
            "/api/print/plan", json={"file_id": f.id, "plate": 2, "printer_id": p.id}
        )
        body2 = r2.json()
        assert body2["plate"] == 2
        assert body2["filaments"][0]["status"] == "ok"
        assert body2["filaments"][0]["tray"] == 1
    finally:
        _clear_state(redis_url, p.id)


async def test_plan_override_regrades(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, seed_file, redis_url: str
):
    f, _model = await _sliced_file(db_session, seed_file)
    p = await _printer(db_session)
    _seed_state(redis_url, p.id, {"gcode_state": "IDLE", "trays": TRAYS})
    try:
        r = await authenticated_client.post(
            "/api/print/plan",
            json={"file_id": f.id, "plate": 1, "printer_id": p.id, "overrides": {"1": 1}},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # filament 1 is PLA; tray 1 is PETG -- overriding onto a type
        # mismatch must downgrade the match to "type", not "ok".
        assert body["filaments"][0]["tray"] == 1
        assert body["filaments"][0]["status"] == "type"
    finally:
        _clear_state(redis_url, p.id)


async def test_plan_warns_busy_and_low_spool(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, seed_file, redis_url: str
):
    f, _model = await _sliced_file(db_session, seed_file)
    p = await _printer(db_session)
    spool = (
        await authenticated_client.post(
            "/api/spools",
            json={
                "material_type": "PLA",
                "color_hex": "#FF0000",
                "label_weight_g": 100,
                "weight_used_g": 95,
            },
        )
    ).json()
    await authenticated_client.put(
        f"/api/printers/{p.id}/slots/0/0", json={"spool_id": spool["id"]}
    )
    _seed_state(redis_url, p.id, {"gcode_state": "RUNNING", "trays": TRAYS})
    try:
        r = await authenticated_client.post(
            "/api/print/plan", json={"file_id": f.id, "plate": 1, "printer_id": p.id}
        )
        assert r.status_code == 200, r.text
        warnings = r.json()["warnings"]
        assert "printer busy" in warnings
        assert any("low" in w for w in warnings)
    finally:
        _clear_state(redis_url, p.id)


async def test_plan_filters_printers_by_acl(
    authenticated_client: httpx.AsyncClient,
    member_client: httpx.AsyncClient,
    member,
    make_group,
    db_session: AsyncSession,
    seed_file,
):
    f, _model = await _sliced_file(db_session, seed_file)
    visible = await _printer(db_session, "visible")
    await _printer(db_session, "hidden")
    await make_group("g", members=[member], printer_access={visible.id: (True, False, False)})

    r = await member_client.post("/api/print/plan", json={"file_id": f.id})
    assert r.status_code == 200, r.text
    ids = {pp["id"] for pp in r.json()["printers"]}
    assert ids == {visible.id}


async def test_send_requires_printer_print(
    viewer_client: httpx.AsyncClient,
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file,
):
    f, _model = await _sliced_file(db_session, seed_file)
    p = await _printer(db_session)
    r = await viewer_client.post(
        "/api/print/send", json={"file_id": f.id, "plate": 1, "printer_id": p.id}
    )
    assert r.status_code == 403


async def test_send_creates_job_with_mapping(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file,
    redis_url: str,
    fake_adapter,
):
    f, _model = await _sliced_file(db_session, seed_file)
    p = await _printer(db_session)
    _seed_state(redis_url, p.id, {"gcode_state": "IDLE", "trays": TRAYS})
    try:
        r = await authenticated_client.post(
            "/api/print/send",
            json={
                "file_id": f.id,
                "plate": 1,
                "printer_id": p.id,
                "use_ams": True,
                "ams_mapping": [0],
            },
        )
        assert r.status_code == 201, r.text
        assert len(fake_adapter.uploaded) == 1
        spec = fake_adapter.uploaded[0]
        assert spec.use_ams is True
        assert spec.ams_mapping == (0,)
    finally:
        _clear_state(redis_url, p.id)


async def test_legacy_print_route_still_works(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file,
    redis_url: str,
    fake_adapter,
):
    f, _model = await _sliced_file(db_session, seed_file)
    p = await _printer(db_session)
    _seed_state(redis_url, p.id, {"gcode_state": "IDLE", "trays": TRAYS})
    try:
        r = await authenticated_client.post(
            f"/api/printers/{p.id}/print", json={"file_id": f.id, "plate": 1}
        )
        assert r.status_code == 201, r.text
    finally:
        _clear_state(redis_url, p.id)


async def test_reprint_returns_plan(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, seed_file, redis_url: str
):
    from app.models.printing import PrintArchive

    f, _model = await _sliced_file(db_session, seed_file)
    p = await _printer(db_session)
    job = PrintJob(printer_id=p.id, file_id=f.id, state=PrintJobState.FINISHED, plate=1)
    db_session.add(job)
    await db_session.flush()
    arc = PrintArchive(
        print_job_id=job.id,
        plate=1,
        result="completed",
        est_filament=[{"filament_id": 1, "tray": 0}],
    )
    db_session.add(arc)
    await db_session.commit()
    await db_session.refresh(arc)

    _seed_state(redis_url, p.id, {"gcode_state": "IDLE", "trays": TRAYS})
    try:
        r = await authenticated_client.post(f"/api/archives/{arc.id}/reprint")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["file_id"] == f.id
        assert body["plate"] == 1
        assert body["printer_id"] == p.id
        assert body["filaments"][0]["tray"] == 0
    finally:
        _clear_state(redis_url, p.id)


async def test_reprint_409_without_library_file(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    from app.models.printing import PrintArchive

    p = await _printer(db_session)
    job = PrintJob(printer_id=p.id, file_id=None, state=PrintJobState.FINISHED, plate=1)
    db_session.add(job)
    await db_session.flush()
    arc = PrintArchive(print_job_id=job.id, plate=1, result="completed", archive_blob=None)
    db_session.add(arc)
    await db_session.commit()
    await db_session.refresh(arc)

    r = await authenticated_client.post(f"/api/archives/{arc.id}/reprint")
    assert r.status_code == 409


async def test_reprint_acl(member_client: httpx.AsyncClient, db_session: AsyncSession):
    from app.models.printing import PrintArchive

    p = await _printer(db_session)
    job = PrintJob(printer_id=p.id, file_id=None, state=PrintJobState.FINISHED, plate=1)
    db_session.add(job)
    await db_session.flush()
    arc = PrintArchive(print_job_id=job.id, plate=1, result="completed")
    db_session.add(arc)
    await db_session.commit()
    await db_session.refresh(arc)

    r = await member_client.post(f"/api/archives/{arc.id}/reprint")
    assert r.status_code == 404
