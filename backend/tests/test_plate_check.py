"""V4 plate-empty detection: pure analysis, the check/calibration API and the
dispatcher pre-dispatch gate."""

from __future__ import annotations

import io

import httpx
import pytest
import redis as redis_lib
import redis.asyncio as aioredis
from PIL import Image, ImageDraw

from app.config import get_settings
from app.models import Printer
from app.models.enums import QueueState
from app.models.library import PrintQueueEntry
from app.services import camera_state as cs
from app.services import dispatcher
from app.services import plate_detection as pd
from app.tasks import base
from tests.test_dispatcher import (
    IDLE_STATE,
    _seed_admin,
    _seed_entry,
    _seed_model_file,
    _seed_printer,
    _seed_state,
)


def _jpeg(*, object_on_plate: bool) -> bytes:
    img = Image.new("RGB", (640, 480))
    px = img.load()
    for x in range(640):
        for y in range(480):
            v = 90 + (x + y) // 12
            px[x, y] = (v, v, v)
    if object_on_plate:
        ImageDraw.Draw(img).rectangle((250, 250, 400, 400), fill=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


EMPTY = _jpeg(object_on_plate=False)
FULL = _jpeg(object_on_plate=True)


def test_analyze_empty_vs_object() -> None:
    assert pd.analyze(EMPTY, [EMPTY]).empty is True
    res = pd.analyze(FULL, [EMPTY], with_preview=True)
    assert res.empty is False
    assert res.score > pd.DEFAULT_THRESHOLD
    assert res.preview is not None and res.preview_data_url().startswith("data:image/jpeg")


def test_analyze_uses_best_reference_and_flags_missing() -> None:
    res = pd.analyze(FULL, [EMPTY, FULL])
    assert res.empty is True and res.best_ref == 1
    assert pd.analyze(FULL, []).needs_calibration is True


def test_config_and_validation() -> None:
    cfg = pd.config_from_options(
        {"plate_check": {"enabled": True, "threshold": 2.5, "plate_type": "PEI"}}
    )
    assert (cfg.enabled, cfg.threshold, cfg.plate_type) == (True, 2.5, "PEI")
    assert pd.config_from_options(None).enabled is False
    with pytest.raises(ValueError):
        pd.validate_options({"plate_check": {"threshold": 999}})
    with pytest.raises(ValueError):
        pd.validate_options({"plate_check": "yes"})


@pytest.mark.usefixtures("printer_enabled")
async def test_calibrate_check_delete_api(
    authenticated_client: httpx.AsyncClient, printer_row: Printer, redis_client: aioredis.Redis
) -> None:
    base_url = f"/api/printers/{printer_row.id}"
    await redis_client.set(cs.frame_key(printer_row.id), EMPTY)

    r = await authenticated_client.post(f"{base_url}/check-plate")
    assert r.status_code == 200 and r.json()["needs_calibration"] is True

    r = await authenticated_client.post(f"{base_url}/plate-references", json={"label": "a"})
    assert r.status_code == 201
    ref_id = r.json()["id"]

    r = await authenticated_client.post(f"{base_url}/check-plate")
    body = r.json()
    assert body["empty"] is True and body["references"] == 1 and body["preview"]

    await redis_client.set(cs.frame_key(printer_row.id), FULL)
    r = await authenticated_client.post(f"{base_url}/check-plate")
    assert r.json()["empty"] is False

    r = await authenticated_client.get(f"{base_url}/plate-references/{ref_id}/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"

    assert (
        await authenticated_client.delete(f"{base_url}/plate-references/{ref_id}")
    ).status_code == 204
    assert (
        await authenticated_client.delete(f"{base_url}/plate-references/{ref_id}")
    ).status_code == 404


@pytest.mark.usefixtures("printer_enabled")
async def test_reference_rotation_caps_at_max(
    authenticated_client: httpx.AsyncClient, printer_row: Printer, redis_client: aioredis.Redis
) -> None:
    await redis_client.set(cs.frame_key(printer_row.id), EMPTY)
    url = f"/api/printers/{printer_row.id}/plate-references"
    for _ in range(pd.MAX_REFERENCES + 2):
        assert (await authenticated_client.post(url, json={})).status_code == 201
    assert len((await authenticated_client.get(url)).json()) == pd.MAX_REFERENCES


@pytest.mark.usefixtures("printer_enabled")
async def test_diagnose_reports_frame(
    authenticated_client: httpx.AsyncClient, printer_row: Printer, redis_client: aioredis.Redis
) -> None:
    await redis_client.set(cs.frame_key(printer_row.id), EMPTY)
    r = await authenticated_client.post(f"/api/printers/{printer_row.id}/camera/diagnose")
    assert r.status_code == 200
    # printer_row has no model -> no built-in camera: kind none, no frame wait
    assert r.json()["camera_kind"] == "none" and r.json()["ok"] is False


@pytest.mark.usefixtures("data_dir", "printer_enabled", "library_root")
def test_dispatch_gate_holds_then_releases(redis_url: str, fake_adapter, monkeypatch) -> None:
    from app.models.printing import PlateReference

    emitted: list[tuple] = []
    monkeypatch.setattr(
        dispatcher.events, "emit", lambda ev, pid=None, payload=None: emitted.append((ev, pid))
    )
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="plate-gate")
        printer = _seed_printer(s, name="GateP")
        printer.options = {"plate_check": {"enabled": True, "threshold": 1.0}}
        s.add(PlateReference(printer_id=printer.id, plate_type="", jpeg=EMPTY))
        admin_id = _seed_admin(s)
        entry = _seed_entry(s, model=model, file=file, state=QueueState.READY, created_by=admin_id)
        s.commit()
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)
    client = redis_lib.Redis.from_url(redis_url)

    cs.put_frame(client, pid, FULL)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    assert counts["sent"] == 0
    with base.sync_session() as s:
        e = s.get(PrintQueueEntry, entry_id)
        assert (e.state, e.last_error, e.attempts) == (QueueState.WAITING, pd.PLATE_NOT_EMPTY, 0)
    assert emitted == [("plate_not_empty", pid)]

    # still occupied: stays held, no second notification
    cs.put_frame(client, pid, FULL)
    with base.sync_session() as s:
        dispatcher.dispatch_once(s, client, settings)
    assert len(emitted) == 1

    # plate cleared: the held entry dispatches
    cs.put_frame(client, pid, EMPTY)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()
    assert counts["sent"] == 1
    with base.sync_session() as s:
        assert s.get(PrintQueueEntry, entry_id).state == QueueState.DISPATCHED
