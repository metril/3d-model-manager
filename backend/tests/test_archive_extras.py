"""T7 archive extras: edit/delete, compare, purge, calendar, outcome confirm,
timelapse editor and photos."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AuditLog, Printer, PrintJob, User
from app.models.enums import (
    ArchiveFetchState,
    ArchiveResult,
    ArchiveVerdict,
    BlobFormat,
    BlobKind,
    PrinterKind,
    PrintJobOrigin,
    PrintJobState,
    VerdictSource,
)
from app.models.library import Blob
from app.models.printing import PrintArchive
from app.services import archive_confirm
from app.services.archive import archive_path
from app.services.archive_settings import ConfirmConfig
from app.tasks.base import sync_session

pytestmark = pytest.mark.usefixtures("data_dir", "library_root")

_n = 0


async def _printer(db: AsyncSession, name: str = "p1") -> Printer:
    p = Printer(
        name=name, kind=PrinterKind.BAMBU_LAN, host="10.0.0.9", serial=name, access_code_enc="x"
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _arc(
    db: AsyncSession,
    printer: Printer,
    *,
    result: ArchiveResult = ArchiveResult.COMPLETED,
    owner: User | None = None,
    meta: dict | None = None,
    age_days: float = 0,
    duration_s: int = 3600,
    **cols,
) -> PrintArchive:
    global _n
    _n += 1
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name=f"job{_n}",
        state=PrintJobState.FINISHED,
        created_by_user_id=owner.id if owner else None,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    db.add(job)
    await db.flush()
    a = PrintArchive(
        print_job_id=job.id,
        result=result,
        duration_s=duration_s,
        slicer_meta=meta,
        fetch_state=ArchiveFetchState.DONE,
        created_at=datetime.now(UTC) - timedelta(days=age_days),
        **cols,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


def _png(size=(8, 8)) -> bytes:
    b = BytesIO()
    Image.new("RGB", size, (200, 10, 10)).save(b, format="PNG")
    return b.getvalue()


# --- PATCH / DELETE -----------------------------------------------------------


async def test_patch_override_semantics(authenticated_client, db_session):
    a = await _arc(db_session, await _printer(db_session))
    c = authenticated_client
    r = await c.patch(
        f"/api/archives/{a.id}",
        json={"notes": " hi ", "tags": ["a", "a", " b "], "result_override": "failed"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "failed" and body["recorded_result"] == "completed"
    assert body["notes"] == "hi" and body["tags"] == ["a", "b"]
    assert len((await c.get("/api/archives?result=failed")).json()) == 1
    assert (await c.get("/api/archives?result=completed")).json() == []
    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "archive.result_override")
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    # Override equal to the recorded result is a clear.
    r = await c.patch(f"/api/archives/{a.id}", json={"result_override": "completed"})
    assert r.json()["result_override"] is None and r.json()["result"] == "completed"


async def test_patch_permissions(
    member, login_as, make_group, db_session: AsyncSession, make_user, viewer_client
):
    p = await _printer(db_session)
    other = await _printer(db_session, "p2")
    a = await _arc(db_session, p)
    # No ACL on the printer -> invisible -> 404 (never 403).
    assert (
        await viewer_client.patch(f"/api/archives/{a.id}", json={"notes": "x"})
    ).status_code == 404
    # Visible but cannot print -> 403.
    await make_group(
        "ro", permissions=[], members=[member], printer_access={p.id: (False, False, False)}
    )
    c = await login_as(member)
    assert (await c.patch(f"/api/archives/{a.id}", json={"notes": "x"})).status_code == 403
    # printers:print + ACL print -> ok.
    writer = await make_user("member")
    await make_group(
        "w",
        permissions=["printers:print"],
        members=[writer],
        printer_access={p.id: (True, True, False)},
    )
    cw = await login_as(writer)
    assert (await cw.patch(f"/api/archives/{a.id}", json={"notes": "x"})).status_code == 200
    # Owner of the job, visible printer, no print perm -> ok.
    b = await _arc(db_session, other, owner=member)
    await make_group("seeother", members=[member], printer_access={other.id: (False, False, False)})
    c = await login_as(member)
    assert (await c.patch(f"/api/archives/{b.id}", json={"notes": "mine"})).status_code == 200
    # Delete is admin only.
    assert (await cw.delete(f"/api/archives/{a.id}")).status_code == 403


async def test_delete_keeps_print_job_and_removes_blob(authenticated_client, db_session):
    p = await _printer(db_session)
    sha = "c" * 64
    db_session.add(Blob(hash=sha, size=3, kind=BlobKind.IMAGE, format=BlobFormat.PNG))
    await db_session.flush()
    a = await _arc(db_session, p, thumbnail_blob=sha)
    path = archive_path(get_settings(), sha, "png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png")
    job_id = a.print_job_id
    r = await authenticated_client.delete(f"/api/archives/{a.id}")
    assert r.status_code == 204
    assert (await authenticated_client.get(f"/api/archives/{a.id}")).status_code == 404
    db_session.expire_all()
    assert await db_session.get(PrintJob, job_id) is not None
    assert await db_session.get(Blob, sha) is None and not path.exists()
    audit = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "archive.delete")))
        .scalars()
        .all()
    )
    assert len(audit) == 1


# --- compare / calendar -------------------------------------------------------


async def test_compare_bounds_diff_and_correlation(authenticated_client, db_session):
    p = await _printer(db_session)
    a1 = await _arc(db_session, p, meta={"layer_height": 0.2, "nozzle": 0.4, "print_time_s": 100})
    a2 = await _arc(
        db_session,
        p,
        result=ArchiveResult.FAILED,
        meta={"layer_height": 0.1, "nozzle": 0.4, "print_time_s": 200},
        est_filament=[{"grams": 5}],
    )
    c = authenticated_client
    assert (await c.get(f"/api/archives/compare?ids={a1.id}")).status_code == 422
    assert (await c.get("/api/archives/compare?ids=1,2,3,4,5,6")).status_code == 422
    assert (await c.get("/api/archives/compare?ids=a,b")).status_code == 422
    assert (await c.get(f"/api/archives/compare?ids={a1.id},9999")).status_code == 404
    r = await c.get(f"/api/archives/compare?ids={a1.id},{a2.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["key"] for d in body["settings_diff"]] == ["layer_height"]
    assert body["settings_diff"][0]["values"] == {str(a1.id): 0.2, str(a2.id): 0.1}
    assert body["time"][str(a2.id)] == {"estimated_s": 200, "actual_s": 3600}
    assert body["filament"][str(a2.id)] == [{"grams": 5}]
    corr = {(x["key"], x["value"]): x for x in body["correlation"]}
    assert corr[("layer_height", 0.2)]["success_rate"] == 1.0
    assert corr[("layer_height", 0.1)]["success_rate"] == 0.0


async def test_compare_hides_invisible(member_client, db_session):
    p = await _printer(db_session)
    a, b = await _arc(db_session, p), await _arc(db_session, p)
    r = await member_client.get(f"/api/archives/compare?ids={a.id},{b.id}")
    assert r.status_code == 404


async def test_calendar(authenticated_client, db_session):
    p = await _printer(db_session)
    await _arc(db_session, p, age_days=0, duration_s=7200)
    await _arc(db_session, p, age_days=0, result=ArchiveResult.FAILED, duration_s=3600)
    await _arc(db_session, p, age_days=10)
    today = datetime.now(UTC).date()
    c = authenticated_client
    r = await c.get(f"/api/archives/calendar?from={today - timedelta(days=30)}&to={today}")
    assert r.status_code == 200
    days = {d["date"]: d for d in r.json()}
    assert days[today.isoformat()] == {
        "date": today.isoformat(),
        "count": 2,
        "ok": 1,
        "failed": 1,
        "hours": 3.0,
    }
    assert len(days) == 2
    assert (
        await c.get(f"/api/archives/calendar?from={today - timedelta(days=401)}&to={today}")
    ).status_code == 422
    assert (
        await c.get(f"/api/archives/calendar?from={today}&to={today - timedelta(days=1)}")
    ).status_code == 422


# --- purge --------------------------------------------------------------------


async def test_purge_preview_equals_run_and_skips_classes(
    authenticated_client, db_session, member_client
):
    p = await _printer(db_session)
    sha = "d" * 64
    db_session.add(Blob(hash=sha, size=10, kind=BlobKind.IMAGE, format=BlobFormat.PNG))
    await db_session.flush()
    old = dict(age_days=400)
    gone = await _arc(db_session, p, thumbnail_blob=sha, **old)
    failed = await _arc(db_session, p, result=ArchiveResult.FAILED, **old)
    overridden = await _arc(db_session, p, result_override=ArchiveResult.FAILED, **old)
    verdict = await _arc(db_session, p, user_verdict=ArchiveVerdict.GOOD, **old)
    pending = await _arc(db_session, p, confirm_requested=True, **old)
    fresh = await _arc(db_session, p, age_days=1)
    keep = {failed.id, overridden.id, verdict.id, pending.id, fresh.id}
    gone_id, gone_job = gone.id, gone.print_job_id
    c = authenticated_client
    r = await c.put(
        "/api/settings/archive_purge",
        json={
            "enabled": True,
            "older_than_days": 365,
            "keep_failed": True,
            "keep_with_verdict": True,
        },
    )
    assert r.status_code == 200
    prev = (await c.post("/api/archives/purge/preview")).json()
    assert prev == {"count": 1, "bytes": 10}
    run = (await c.post("/api/archives/purge/run")).json()
    assert run == {"deleted": 1, "bytes": 10}
    db_session.expire_all()
    left = {a.id for a in (await db_session.execute(select(PrintArchive))).scalars()}
    assert left == keep
    assert gone_id not in left
    # print_jobs untouched.
    assert await db_session.get(PrintJob, gone_job) is not None
    stats = (await c.get("/api/archives/purge/stats")).json()
    assert stats["last_deleted"] == 1 and stats["last_bytes"] == 10 and stats["last_run_at"]
    # Setting PUT preserves stats; keep flags off now purge everything old but pending.
    await c.put(
        "/api/settings/archive_purge",
        json={
            "enabled": True,
            "older_than_days": 365,
            "keep_failed": False,
            "keep_with_verdict": False,
        },
    )
    assert (await c.get("/api/archives/purge/stats")).json()["last_deleted"] == 1
    assert (await c.post("/api/archives/purge/preview")).json()["count"] == 3
    audits = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "archive.purge")))
        .scalars()
        .all()
    )
    assert len(audits) == 1
    # Admin only.
    assert (await member_client.post("/api/archives/purge/run")).status_code == 403
    assert (await member_client.post("/api/archives/purge/preview")).status_code == 403


async def test_purge_task_gated_by_enabled(authenticated_client, db_session):
    from app.tasks.archive import purge

    p = await _printer(db_session)
    await _arc(db_session, p, age_days=500)
    assert purge.apply().get() == "disabled"
    await authenticated_client.put(
        "/api/settings/archive_purge",
        json={
            "enabled": True,
            "older_than_days": 30,
            "keep_failed": False,
            "keep_with_verdict": True,
        },
    )
    assert purge.apply().get().startswith("purged 1")
    db_session.expire_all()
    assert (await db_session.execute(select(PrintArchive))).first() is None


# --- outcome confirm ----------------------------------------------------------


@pytest.fixture
def emitted(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.services.notify.events.emit",
        lambda ev, pid=None, payload=None: calls.append((ev, pid, payload)),
    )
    return calls


async def _pending_archive(db_session, emitted, **cfg) -> tuple[PrintArchive, str]:
    """An archive for which a confirm request was minted; returns (arc, plaintext token)."""
    p = await _printer(db_session)
    aid = (await _arc(db_session, p)).id
    with sync_session() as s:
        archive_settings_cfg = ConfirmConfig(enabled=True, external_prints=True, **cfg)
        from app.models import Setting

        s.merge(Setting(key="confirm", value=archive_settings_cfg.model_dump()))
        s.commit()
        arc = s.get(PrintArchive, aid)
        job = s.get(PrintJob, arc.print_job_id)
        assert archive_confirm.request_confirm(s, get_settings(), arc, job, "P1")
        s.commit()
    ev, _pid, payload = emitted[-1]
    assert ev == "print_confirm_request"
    token = payload["good_url"].split("/confirm/")[1].split("/")[0]
    db_session.expire_all()
    return await db_session.get(PrintArchive, aid), token


async def test_confirm_flow(client: httpx.AsyncClient, db_session, emitted, redis_client):
    arc, token = await _pending_archive(db_session, emitted)
    arc_id = arc.id
    payload = emitted[-1][2]
    assert payload["good_url"].endswith(f"/api/archives/confirm/{token}/good?tap=1")
    assert payload["bad_url"].endswith(f"/api/archives/confirm/{token}/bad?tap=1")
    # Hashed at rest, 7-day expiry.
    assert arc.confirm_token != token and arc.confirm_token == archive_confirm.hash_token(token)
    assert arc.confirm_requested and arc.confirm_expires_at > datetime.now(UTC) + timedelta(days=6)
    # GET is idempotent and never records.
    for _ in range(2):
        r = await client.get(f"/api/archives/confirm/{token}/bad")
        assert r.status_code == 200 and "<form" in r.text and "submit()" not in r.text
    r = await client.get(f"/api/archives/confirm/{token}/bad?tap=1")
    assert "document.forms[0].submit()" in r.text
    db_session.expire_all()
    assert (await db_session.get(PrintArchive, arc_id)).user_verdict is None
    # POST records once.
    assert (await client.post(f"/api/archives/confirm/{token}/bad")).status_code == 200
    db_session.expire_all()
    got = await db_session.get(PrintArchive, arc_id)
    assert got.user_verdict == ArchiveVerdict.BAD and got.user_verdict_source == VerdictSource.LINK
    assert got.result_override == ArchiveResult.FAILED and got.confirm_token_used_at is not None
    assert (await client.post(f"/api/archives/confirm/{token}/good")).status_code == 410
    assert (await client.get(f"/api/archives/confirm/{token}/good")).status_code == 410
    assert (await client.get("/api/archives/confirm/nope/good")).status_code == 404
    assert (await client.get(f"/api/archives/confirm/{token}/maybe")).status_code == 404


async def test_confirm_expired_and_rate_limit(client, db_session, emitted, redis_client):
    arc, token = await _pending_archive(db_session, emitted)
    arc.confirm_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    assert (await client.post(f"/api/archives/confirm/{token}/good")).status_code == 410
    await redis_client.flushdb()
    codes = [(await client.get("/api/archives/confirm/x/good")).status_code for _ in range(31)]
    assert codes[:30] == [404] * 30 and codes[30] == 429


async def test_verdict_good_clears_only_bad_override(authenticated_client, db_session):
    p = await _printer(db_session)
    manual = await _arc(db_session, p, result_override=ArchiveResult.FAILED)
    c = authenticated_client
    r = await c.post(
        f"/api/archives/{manual.id}/verdict", json={"verdict": "good", "source": "dialog"}
    )
    assert r.status_code == 200 and r.json()["result"] == "failed"  # manual override survives
    a = await _arc(db_session, p)
    r = await c.post(f"/api/archives/{a.id}/verdict", json={"verdict": "bad", "source": "card"})
    assert r.json()["result"] == "failed" and r.json()["user_verdict_source"] == "card"
    r = await c.post(f"/api/archives/{a.id}/verdict", json={"verdict": "good", "source": "api"})
    assert r.json()["result"] == "completed" and r.json()["result_override"] is None
    assert (
        await c.post(f"/api/archives/{a.id}/verdict", json={"verdict": "good", "source": "link"})
    ).status_code == 422


async def test_bad_verdict_on_failed_run_sets_no_override(authenticated_client, db_session):
    p = await _printer(db_session)
    a = await _arc(db_session, p, result=ArchiveResult.FAILED)
    r = await authenticated_client.post(f"/api/archives/{a.id}/verdict", json={"verdict": "bad"})
    assert r.json()["result_override"] is None


async def test_defaults_sweep_and_plate_clear(db_session, emitted):
    a, _t = await _pending_archive(db_session, emitted, default_outcome="good", default_after_h=24)
    aid = a.id
    with sync_session() as s:
        assert archive_confirm.apply_defaults(s) == 0  # too fresh
        arc = s.get(PrintArchive, aid)
        arc.created_at = datetime.now(UTC) - timedelta(hours=25)
        s.commit()
        assert archive_confirm.apply_defaults(s) == 1
        arc = s.get(PrintArchive, a.id)
        assert (
            arc.user_verdict == ArchiveVerdict.GOOD and arc.user_verdict_source == VerdictSource.API
        )
    # plate-clear subscriber (called directly)
    b, _t2 = await _pending_archive(db_session, emitted, default_good_on_plate_clear=True)
    bid = b.id
    with sync_session() as s:
        pid = s.get(PrintJob, s.get(PrintArchive, bid).print_job_id).printer_id
    archive_confirm.on_plate_cleared(pid, None)
    db_session.expire_all()
    got = await db_session.get(PrintArchive, bid)
    assert got.user_verdict == ArchiveVerdict.GOOD
    assert got.user_verdict_source == VerdictSource.PLATE_CLEAR
    # disabled flag -> no-op
    c, _t3 = await _pending_archive(db_session, emitted, default_good_on_plate_clear=False)
    cid = c.id
    with sync_session() as s:
        pid = s.get(PrintJob, s.get(PrintArchive, cid).print_job_id).printer_id
    archive_confirm.on_plate_cleared(pid, None)
    db_session.expire_all()
    assert (await db_session.get(PrintArchive, cid)).user_verdict is None


def test_archive_job_requests_confirm(fake_adapter, sliced_3mf_bytes, seed, emitted):
    from app.models import Setting

    seed.s.merge(Setting(key="confirm", value=ConfirmConfig(enabled=True).model_dump()))
    seed.s.commit()
    seed.model("DotC parts - Part 1")
    ext_job = seed.job(subtask="DotC parts - Part 1_plate_1")
    fake_adapter.files["/cache/DotC parts - Part 1_plate_1.3mf"] = sliced_3mf_bytes
    from app.services.archive import archive_job

    arc = archive_job(seed.s, get_settings(), ext_job, ArchiveResult.COMPLETED)
    assert not arc.confirm_requested and emitted == []  # external prints off
    seed.s.merge(
        Setting(key="confirm", value=ConfirmConfig(enabled=True, external_prints=True).model_dump())
    )
    seed.s.commit()
    arc = archive_job(seed.s, get_settings(), ext_job, ArchiveResult.COMPLETED)
    assert arc.confirm_requested and emitted[0][0] == "print_confirm_request"
    n = len(emitted)
    archive_job(seed.s, get_settings(), ext_job, ArchiveResult.COMPLETED)
    assert len(emitted) == n  # redelivery does not re-mint


def test_catalog_has_confirm_event():
    from app.services.notify.catalog import EVENT_IDS

    assert "print_confirm_request" in EVENT_IDS


def test_hooks_registration_is_guarded():
    # Works whether or not T3's hook registry exists yet.
    assert archive_confirm.register_hooks() in (True, False)


# --- timelapse editor ---------------------------------------------------------


def _make_video(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x64:rate=10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )  # fmt: skip


async def _arc_with_timelapse(db: AsyncSession, tmp_path: Path) -> PrintArchive:
    import hashlib

    src = tmp_path / "src.mp4"
    _make_video(src)
    data = src.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    dest = archive_path(get_settings(), sha, "mp4")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    db.add(Blob(hash=sha, size=len(data), kind=BlobKind.VIDEO, format=BlobFormat.MP4))
    await db.flush()
    return await _arc(db, await _printer(db), timelapse_blob=sha)


async def test_timelapse_info_thumbnails_process(
    authenticated_client, db_session, tmp_path, redis_client
):
    a = await _arc_with_timelapse(db_session, tmp_path)
    aid = a.id
    c = authenticated_client
    info = (await c.get(f"/api/archives/{a.id}/timelapse/info")).json()
    assert info["width"] == 64 and info["height"] == 64 and info["fps"] == 10.0
    assert 1.8 < info["duration_s"] < 2.3 and info["bytes"] > 0
    r = await c.get(f"/api/archives/{a.id}/timelapse/thumbnails?n=3")
    assert len(r.json()["frames"]) == 3 and r.json()["frames"][0].startswith(
        "data:image/jpeg;base64,"
    )
    assert await redis_client.exists(f"tl:thumbs:{a.timelapse_blob}:3")
    assert (await c.get(f"/api/archives/{a.id}/timelapse/thumbnails?n=3")).json() == r.json()
    assert (await c.get(f"/api/archives/{a.id}/timelapse/thumbnails?n=99")).status_code == 422

    old = a.timelapse_blob
    r = await c.post(
        f"/api/archives/{a.id}/timelapse/process",
        data={"trim_start": "0.5", "trim_end": "1.5", "speed": "2", "save_mode": "new"},
    )
    assert r.status_code == 202
    state = (await c.get(f"/api/archives/{a.id}/timelapse/jobs/{r.json()['job_id']}")).json()
    assert state["state"] == "done" and state["filename"].endswith(".mp4")
    detail = (await c.get(f"/api/archives/{a.id}")).json()
    assert [p["kind"] for p in detail["photos"]] == ["timelapse"]
    got = await c.get(f"/api/archives/{a.id}/photos/{state['filename']}")
    assert got.status_code == 200 and got.headers["content-type"] == "video/mp4"
    db_session.expire_all()
    assert (await db_session.get(PrintArchive, aid)).timelapse_blob == old

    r = await c.post(
        f"/api/archives/{a.id}/timelapse/process",
        data={"trim_start": "0", "speed": "2", "save_mode": "replace"},
    )
    state = (await c.get(f"/api/archives/{a.id}/timelapse/jobs/{r.json()['job_id']}")).json()
    assert state["state"] == "done"
    db_session.expire_all()
    new = (await db_session.get(PrintArchive, aid)).timelapse_blob
    assert new != old and not archive_path(get_settings(), old, "mp4").exists()
    info2 = (await c.get(f"/api/archives/{a.id}/timelapse/info")).json()
    assert info2["duration_s"] < info["duration_s"]
    # validation
    bad = await c.post(f"/api/archives/{a.id}/timelapse/process", data={"speed": "9"})
    assert bad.status_code == 422
    bad = await c.post(
        f"/api/archives/{a.id}/timelapse/process", data={"trim_start": "2", "trim_end": "1"}
    )
    assert bad.status_code == 422
    assert (await c.get(f"/api/archives/{a.id}/timelapse/jobs/nope")).status_code == 404


async def test_timelapse_requires_print_perm_and_media(
    authenticated_client, viewer_client, db_session
):
    a = await _arc(db_session, await _printer(db_session))
    assert (
        await authenticated_client.get(f"/api/archives/{a.id}/timelapse/info")
    ).status_code == 404
    r = await viewer_client.post(f"/api/archives/{a.id}/timelapse/process", data={})
    assert r.status_code == 403


# --- photos -------------------------------------------------------------------


async def test_photo_upload_list_get_delete(authenticated_client, db_session):
    a = await _arc(db_session, await _printer(db_session))
    c = authenticated_client
    r = await c.post(f"/api/archives/{a.id}/photos", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 201, r.text
    name = r.json()["filename"]
    assert name.endswith(".png") and r.json()["kind"] == "photo"
    assert [p["filename"] for p in (await c.get(f"/api/archives/{a.id}/photos")).json()] == [name]
    got = await c.get(f"/api/archives/{a.id}/photos/{name}")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert (await c.get(f"/api/archives/{a.id}/photos/missing.png")).status_code == 404
    assert (await c.delete(f"/api/archives/{a.id}/photos/{name}")).status_code == 204
    assert (await c.get(f"/api/archives/{a.id}/photos")).json() == []
    assert not any(
        archive_path(get_settings(), h, "png").exists() for h in [a.timelapse_blob or ""]
    )


async def test_photo_validation_exif_and_resize(authenticated_client, db_session):
    a = await _arc(db_session, await _printer(db_session))
    c = authenticated_client
    url = f"/api/archives/{a.id}/photos"
    assert (await c.post(url, files={"file": ("x.txt", b"hello", "text/plain")})).status_code == 415
    assert (
        await c.post(url, files={"file": ("x.png", b"\x89PNG not really", "image/png")})
    ).status_code == 415
    big = b"\0" * (15 * 1024 * 1024 + 1)
    assert (await c.post(url, files={"file": ("x.jpg", big, "image/jpeg")})).status_code == 413
    # EXIF stripped
    img = Image.new("RGB", (16, 16), (1, 2, 3))
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    assert Image.open(BytesIO(buf.getvalue())).getexif().get(0x010F) == "SecretCam"
    r = await c.post(url, files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 201
    out = (await c.get(f"{url}/{r.json()['filename']}")).content
    assert not Image.open(BytesIO(out)).getexif()
    # downscaled to 4096 max
    wide = BytesIO()
    Image.new("RGB", (5000, 10)).save(wide, format="PNG")
    r = await c.post(url, files={"file": ("w.png", wide.getvalue(), "image/png")})
    out = (await c.get(f"{url}/{r.json()['filename']}")).content
    assert Image.open(BytesIO(out)).size[0] == 4096


async def test_photo_writer_gate(member_client, login_as, db_session, make_group, member):
    p = await _printer(db_session)
    a = await _arc(db_session, p)
    await make_group("ro", members=[member], printer_access={p.id: (False, False, False)})
    c = await login_as(member)
    r = await c.post(f"/api/archives/{a.id}/photos", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 403


async def test_settings_groups_roundtrip(authenticated_client, member_client):
    c = authenticated_client
    assert (await c.get("/api/settings/confirm")).json() == ConfirmConfig().model_dump()
    body = ConfirmConfig(enabled=True, default_outcome="good", default_after_h=2).model_dump()
    assert (await c.put("/api/settings/confirm", json=body)).json() == body
    assert (
        await c.put("/api/settings/confirm", json={**body, "default_after_h": 0})
    ).status_code == 422
    assert (
        await c.put("/api/settings/archive_purge", json={"older_than_days": 0})
    ).status_code == 422
    assert (await member_client.put("/api/settings/confirm", json=body)).status_code == 403
