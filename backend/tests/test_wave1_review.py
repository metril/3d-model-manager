"""Wave-1 review fixes: trashed models are unreachable / unprintable, trash
holds queue entries, plug schedule + unlink safety, project ACL/ownership,
ext/v2 audit + queue visibility, trash list scoping, auto-purge guard."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import redis as redis_lib
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import AuditLog, Printer, SmartPlug
from app.models.enums import (
    ArchiveFetchState,
    ArchiveResult,
    BlobFormat,
    BlobKind,
    PrinterKind,
    PrintJobOrigin,
    PrintJobState,
    QueueState,
)
from app.models.library import File, Model, PrintQueueEntry, Revision
from app.models.printing import PrintArchive, PrintJob
from app.printers.base import state_key
from app.services import dispatcher
from app.services.smart_plugs import manager
from app.tasks import base
from tests.auth_fixtures import LoginAs, MakeGroup, MakeUser

pytestmark = pytest.mark.usefixtures("library_root", "data_dir", "printer_enabled")


async def _model_and_file(
    client: httpx.AsyncClient,
    db: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
    name: str,
) -> tuple[dict, File]:
    m = (await client.post("/api/models", json={"name": name})).json()
    model = await db.get(Model, m["id"])
    rev = await db.get(Revision, m["current_revision"]["id"])
    f = await seed_file(
        model,
        rev,
        "job.gcode.3mf",
        b"gc-" + name.encode(),
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    return m, f


# -- 1 + 2: trashed model files unreachable; queue entries held ------------------


async def test_trashed_model_files_unreachable_and_queue_held(
    authenticated_client, db_session: AsyncSession, seed_file
) -> None:
    c = authenticated_client
    m, f = await _model_and_file(c, db_session, seed_file, "Gone")
    fid = f.id
    assert (await c.get(f"/api/files/{fid}")).status_code == 200
    q = await c.post("/api/queue", json={"file_id": fid})
    assert q.status_code == 201 and q.json()["state"] == "ready"
    qid = q.json()["id"]

    assert (await c.delete(f"/api/models/{m['slug']}")).status_code == 204
    db_session.expire_all()
    e = await db_session.get(PrintQueueEntry, qid)
    assert (e.state, e.last_error) == (QueueState.HELD, "model in trash")

    assert (await c.get(f"/api/files/{fid}")).status_code == 404
    assert (await c.get(f"/api/files/{fid}/download")).status_code == 404
    assert (await c.post(f"/api/files/{fid}/slicer-link")).status_code == 404
    assert (await c.post("/api/queue", json={"file_id": fid})).status_code == 404
    assert (
        await c.post("/api/batches", json={"name": "b", "file_id": fid, "quantity": 1})
    ).status_code == 404
    assert (
        await c.post("/api/batches", json={"name": "b", "model_id": m["id"], "quantity": 1})
    ).status_code == 404

    with base.sync_session() as s:  # the dispatcher never even sees it
        mm = s.get(Model, m["id"])
        e2 = s.get(PrintQueueEntry, qid)
        e2.state = QueueState.READY  # even if something flipped it back
        s.commit()
        assert mm.deleted_at is not None
        assert dispatcher.eligible_queue_items(s) == []

    r = await c.post("/api/trash/restore", json={"ids": [m["id"]]})
    assert r.status_code == 200
    db_session.expire_all()
    e = await db_session.get(PrintQueueEntry, qid)
    assert (e.state, e.last_error) == (QueueState.READY, None)
    assert (await c.get(f"/api/files/{fid}")).status_code == 200


# -- 3: scheduled off respects printer state -----------------------------------------


class _Drv:
    def __init__(self) -> None:
        self.offs = 0

    def off(self) -> None:
        self.offs += 1

    def on(self) -> None:
        pass


def test_scheduled_off_deferred_while_printer_busy(redis_url: str) -> None:
    r = redis_lib.Redis.from_url(redis_url)
    r.flushdb()
    plug = SmartPlug(
        id=77,
        printer_id=5,
        kind="rest",
        config_enc="",
        controls_printer_power=True,
        schedule_enabled=True,
        schedule_off_time="22:00",
        schedule_tz="UTC",
    )
    drv = _Drv()
    t = datetime(2026, 1, 5, 22, 0, 30, tzinfo=UTC)
    r.set(state_key(5), json.dumps({"gcode_state": "RUNNING", "nozzle_temper": 210}))
    assert manager._run_schedule(r, plug, drv, t) is False and drv.offs == 0
    # wanted by the queue: also deferred
    r.set(state_key(5), json.dumps({"gcode_state": "IDLE", "nozzle_temper": 25}))
    assert manager._run_schedule(r, plug, drv, t, {5}) is False and drv.offs == 0
    # idle + cold, later inside the deferral window: fires
    assert manager._run_schedule(r, plug, drv, t + timedelta(minutes=30), set()) is True
    assert drv.offs == 1
    assert manager._run_schedule(r, plug, drv, t + timedelta(minutes=31), set()) is False
    r.close()


# -- 12: printer delete unlinks plugs safely -------------------------------------------


async def test_unlinked_plug_is_admin_only_and_unscheduled(
    authenticated_client, member_client, member, db_session: AsyncSession, make_group
) -> None:
    c = authenticated_client
    pid = (
        await c.post(
            "/api/printers",
            json={"name": "P", "host": "10.0.0.5", "serial": "0309ABC", "access_code": "12345678"},
        )
    ).json()["id"]
    body = {
        "name": "Heater",
        "printer_id": pid,
        "kind": "rest",
        "config": {"on_url": "http://plug/on", "off_url": "http://plug/off"},
        "auto_off_after_min": 10,
        "schedule_enabled": True,
        "schedule_off_time": "22:00",
    }
    plug_id = (await c.post("/api/plugs", json=body)).json()["id"]
    assert (await c.delete(f"/api/printers/{pid}")).status_code == 204
    out = (await c.get(f"/api/plugs/{plug_id}")).json()
    assert out["printer_id"] is None
    assert not (out["schedule_enabled"] or out["auto_off"] or out["controls_printer_power"])
    # a printers:control holder who is not an admin can no longer drive it
    await make_group("ctl", permissions=["printers:control"], members=[member])
    r = await member_client.post(f"/api/plugs/{plug_id}/on")
    assert r.status_code == 403


# -- 4 + 7 + 8: projects ------------------------------------------------------------------


async def test_projects_exclude_trashed_models(authenticated_client, db_session) -> None:
    c = authenticated_client
    live = (await c.post("/api/models", json={"name": "Live"})).json()
    dead = (await c.post("/api/models", json={"name": "Dead"})).json()
    pid = (await c.post("/api/projects", json={"name": "Robot"})).json()["id"]
    assert (
        await c.post(f"/api/projects/{pid}/models", json={"model_ids": [live["id"]]})
    ).status_code == 200
    await c.delete(f"/api/models/{dead['slug']}")
    r = await c.post(f"/api/projects/{pid}/models", json={"model_ids": [dead["id"]]})
    assert r.status_code == 404
    r = await c.post(f"/api/projects/{pid}/items", json={"kind": "part", "model_id": dead["id"]})
    assert r.status_code == 404
    r = await c.patch(f"/api/projects/{pid}", json={"cover_model_id": dead["id"]})
    assert r.status_code == 404
    # a part whose model is trashed later drops out of the rollup / queue-remaining
    await c.post(f"/api/projects/{pid}/models", json={"model_ids": [live["id"]]})
    await c.delete(f"/api/models/{live['slug']}")
    out = (await c.get(f"/api/projects/{pid}")).json()
    assert all(i["model_name"] is None for i in out["items"])
    q = await c.post(f"/api/projects/{pid}/queue-remaining", json={})
    assert q.status_code == 200 and q.json()["enqueued"] == []


async def test_project_mutations_need_owner(
    authenticated_client, make_user: MakeUser, make_group: MakeGroup, login_as: LoginAs
) -> None:
    pid = (await authenticated_client.post("/api/projects", json={"name": "Mine"})).json()["id"]
    other = await make_user("member")
    await make_group("writers", permissions=["library:write"], members=[other])
    oc = await login_as(other)
    assert (await oc.get(f"/api/projects/{pid}")).status_code == 200
    assert (await oc.patch(f"/api/projects/{pid}", json={"name": "x"})).status_code == 403
    assert (
        await oc.post(f"/api/projects/{pid}/items", json={"kind": "purchased", "name": "screw"})
    ).status_code == 403
    assert (await oc.delete(f"/api/projects/{pid}")).status_code == 403
    assert (
        await oc.post(f"/api/projects/{pid}/archives", json={"archive_ids": [1]})
    ).status_code == 403
    assert (
        await authenticated_client.patch(f"/api/projects/{pid}", json={"name": "ok"})
    ).status_code == 200


async def test_project_archives_respect_printer_acl(
    authenticated_client,
    db_session: AsyncSession,
    make_user: MakeUser,
    make_group: MakeGroup,
    login_as: LoginAs,
) -> None:
    member = await make_user("member")
    await make_group("writers", permissions=["library:write"], members=[member])
    mc = await login_as(member)  # no printer ACL at all
    pid = (await mc.post("/api/projects", json={"name": "Mine"})).json()["id"]
    printer = Printer(
        name="hidden", kind=PrinterKind.BAMBU_LAN, host="h", serial="hid", access_code_enc="x"
    )
    db_session.add(printer)
    await db_session.flush()
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name="j",
        state=PrintJobState.FINISHED,
    )
    db_session.add(job)
    await db_session.flush()
    arc = PrintArchive(
        print_job_id=job.id,
        result=ArchiveResult.COMPLETED,
        fetch_state=ArchiveFetchState.DONE,
        project_id=None,
    )
    db_session.add(arc)
    await db_session.commit()
    arc_id = arc.id

    r = await mc.post(f"/api/projects/{pid}/archives", json={"archive_ids": [arc_id]})
    assert r.json() == {"linked": 0}
    db_session.expire_all()
    assert (await db_session.get(PrintArchive, arc_id)).project_id is None
    # an admin links it; the member then must not SEE it
    admin_pid = (await authenticated_client.post("/api/projects", json={"name": "A"})).json()["id"]
    r = await authenticated_client.post(
        f"/api/projects/{admin_pid}/archives", json={"archive_ids": [arc_id]}
    )
    assert r.json() == {"linked": 1}
    assert len((await authenticated_client.get(f"/api/projects/{admin_pid}/archives")).json()) == 1
    assert (await mc.get(f"/api/projects/{admin_pid}/archives")).json() == []
    tl = (await mc.get(f"/api/projects/{admin_pid}/timeline")).json()
    assert all(e["kind"] != "print" for e in tl)


# -- 5: auto-purge skips models with in-flight store jobs -----------------------------------


async def test_auto_purge_skips_pending_store(
    authenticated_client, db_session: AsyncSession, seed_file
) -> None:
    from app.models.system import Job
    from app.tasks.trash import purge_expired

    m, f = await _model_and_file(authenticated_client, db_session, seed_file, "Pending")
    fid = f.id
    await authenticated_client.delete(f"/api/models/{m['slug']}")
    await db_session.execute(
        text("update models set deleted_at = :t where id = :i"),
        {"t": datetime.now(UTC) - timedelta(days=90), "i": m["id"]},
    )
    await db_session.execute(text("update files set verified_at = NULL where id = :i"), {"i": fid})
    db_session.add(
        Job(
            id="00000000-0000-0000-0000-000000000042",
            type="store_to_backend",
            subject_type="file",
            subject_id=fid,
            state="running",
        )
    )
    await db_session.commit()
    get_settings.cache_clear()
    assert purge_expired() == 0
    assert (await db_session.get(Model, m["id"])) is not None
    await db_session.execute(text("update jobs set state = 'succeeded'"))
    await db_session.commit()
    assert purge_expired() == 1


# -- 6: plate gate never blocks on a camera and fails closed -------------------------------


def test_plate_gate_without_frame_holds_without_waiting(redis_url: str) -> None:
    import time

    from app.models.printing import PlateReference
    from app.services import camera_state as cs

    c = redis_lib.Redis.from_url(redis_url)
    c.flushdb()
    with base.sync_session() as s:
        p = Printer(
            name="g",
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial="GATE",
            access_code_enc=encrypt_secret(get_settings(), "1"),
            options={"plate_check": {"enabled": True, "threshold": 1.0}},
        )
        s.add(p)
        s.flush()
        s.add(PlateReference(printer_id=p.id, plate_type="", jpeg=b"x"))
        s.commit()
        item = PrintQueueEntry(model_id=1, position=1, state=QueueState.READY)
        t0 = time.monotonic()
        ok = dispatcher.plate_gate(s, c, item, p, {})
        assert ok is False and time.monotonic() - t0 < 2.0
        assert item.last_error == dispatcher.PLATE_NO_FRAME and item.state == QueueState.READY
        assert cs.viewer_count(c, p.id) == 1  # camera warmed asynchronously
    c.close()


# -- 9 + 10: ext/v2 --------------------------------------------------------------------------


async def test_ext_v2_list_queue_hides_unassigned_for_allowlisted_key(
    authenticated_client, client, db_session
) -> None:
    c = authenticated_client
    pr = await c.post(
        "/api/printers",
        json={"name": "A", "host": "10.0.0.5", "serial": "0309ABC", "access_code": "12345678"},
    )
    pid = pr.json()["id"]
    model = (await c.post("/api/models", json={"name": "M"})).json()
    entry = (await c.post("/api/queue", json={"model_id": model["id"]})).json()
    await db_session.execute(text("update print_queue set created_by_user_id = NULL"))
    await db_session.commit()
    unrestricted = (
        await c.post("/api/settings/api-keys", json={"label": "u", "scopes": ["read"]})
    ).json()["key"]
    limited = (
        await c.post(
            "/api/settings/api-keys",
            json={"label": "l", "scopes": ["read"], "printer_ids": [pid]},
        )
    ).json()["key"]
    r = await client.get("/api/ext/v2/queue", headers={"X-API-Key": unrestricted})
    assert [e["id"] for e in r.json()] == [entry["id"]]
    r = await client.get("/api/ext/v2/queue", headers={"X-API-Key": limited})
    assert r.json() == []


async def test_ext_v2_rejected_scope_not_audited_as_success(
    authenticated_client, client, db_session
) -> None:
    c = authenticated_client
    key = (await c.post("/api/settings/api-keys", json={"label": "ro", "scopes": ["read"]})).json()[
        "key"
    ]
    r = await client.post("/api/ext/v2/queue", json={"model_id": 1}, headers={"X-API-Key": key})
    assert r.status_code == 403
    actions = list((await db_session.execute(select(AuditLog.action))).scalars())
    assert "api_key:post" not in actions and "api_key:denied" in actions


# -- 11: trash list scoping ----------------------------------------------------------------------


async def test_trash_list_scoped_to_own_and_needs_write(
    authenticated_client, member_client, viewer_client
) -> None:
    mine = (await member_client.post("/api/models", json={"name": "Mine"})).json()
    theirs = (await authenticated_client.post("/api/models", json={"name": "Admins"})).json()
    await authenticated_client.post(
        "/api/models/bulk-delete", json={"ids": [mine["id"], theirs["id"]]}
    )
    got = (await member_client.get("/api/trash")).json()["items"]
    assert [i["id"] for i in got] == [mine["id"]]
    assert len((await authenticated_client.get("/api/trash")).json()["items"]) == 2
    assert (await viewer_client.get("/api/trash")).status_code == 403
