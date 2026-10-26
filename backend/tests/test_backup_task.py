"""Q7 backups: pg_dump (subprocess patched) + settings JSON through the
storage backend, git push shape, retention, restore-settings, admin gating."""

from __future__ import annotations

import json
import subprocess

import pytest
import respx
from sqlalchemy import select

from app.config import get_settings
from app.models import BackupRun, Setting
from app.services import backup as svc
from app.services import git_backup
from app.services.storage_config import resolve_backend_sync
from app.tasks import base
from app.tasks.ops import backup_run

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


@pytest.fixture
def fake_pg_dump(monkeypatch):
    calls = []

    def _run(cmd, stdout=None, stderr=None, env=None, timeout=None, check=False):
        calls.append((cmd, env))
        stdout.write(b"PGDMP-fake")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(svc.subprocess, "run", _run)
    return calls


def test_pg_args_from_database_url():
    args, env = svc.pg_args("postgresql+asyncpg://u%40x:p%3Aw@db:5433/tdmm")
    assert args == ["--host", "db", "--port", "5433", "--username", "u@x", "--dbname", "tdmm"]
    assert env["PGPASSWORD"] == "p:w"


def test_backup_writes_dump_and_settings(fake_pg_dump):
    with base.sync_session() as s:
        s.add(Setting(key="app", value={"x": 1}))
        s.commit()
        run = svc.new_run(s, "manual", None)
        run_id = run.id
    out = backup_run.delay(run_id).get()
    assert out["status"] == "ok"
    with base.sync_session() as s:
        run = s.get(BackupRun, run_id)
        backend = resolve_backend_sync(s, get_settings())
        assert run.location.startswith("backups/")
        assert b"".join(backend.read(f"{run.location}/db.dump")) == b"PGDMP-fake"
        data = json.loads(b"".join(backend.read(f"{run.location}/settings.json")))
        assert data["app"] == {"x": 1}
        assert run.size_bytes > 0 and run.git_pushed is False
    assert fake_pg_dump[0][0][:2] == ["pg_dump", "--format=custom"]


def test_pg_dump_failure_marks_failed(monkeypatch):
    def _run(cmd, stdout=None, **kw):
        return subprocess.CompletedProcess(cmd, 1, b"", b"pg_dump: connection refused\n")

    monkeypatch.setattr(svc.subprocess, "run", _run)
    with base.sync_session() as s:
        run_id = svc.new_run(s, "manual", None).id
    assert backup_run.delay(run_id).get()["status"] == "failed"
    with base.sync_session() as s:
        assert "connection refused" in s.get(BackupRun, run_id).error


def test_retention_prunes_old(fake_pg_dump):
    with base.sync_session() as s:
        s.add(Setting(key="ops", value={"backup_retention": 1}))
        s.commit()
    ids = []
    for _ in range(2):
        with base.sync_session() as s:
            ids.append(svc.new_run(s, "manual", None).id)
        backup_run.delay(ids[-1]).get()
    with base.sync_session() as s:
        first, second = s.get(BackupRun, ids[0]), s.get(BackupRun, ids[1])
        assert first.status == "pruned" and first.location is None
        assert second.status == "ok"


@respx.mock
def test_git_push_shapes():
    kw = dict(repo="o/r", branch="main", token="tok", path="a/b.json", content=b"x", message="m")
    # GitHub: lookup then PUT with sha
    respx.get("https://api.github.com/repos/o/r/contents/a/b.json").respond(json={"sha": "abc"})
    put = respx.put("https://api.github.com/repos/o/r/contents/a/b.json").respond(200)
    git_backup.push_file(provider="github", base_url=None, **kw)
    assert json.loads(put.calls[0].request.content)["sha"] == "abc"
    # Gitea: missing -> POST create
    respx.get("https://g.example/api/v1/repos/o/r/contents/a/b.json").respond(404)
    post = respx.post("https://g.example/api/v1/repos/o/r/contents/a/b.json").respond(201)
    git_backup.push_file(provider="gitea", base_url="https://g.example", **kw)
    assert post.called
    # GitLab: HEAD missing -> POST
    url = "https://gl.example/api/v4/projects/o%2Fr/repository/files/a%2Fb.json"
    respx.head(url).respond(404)
    glpost = respx.post(url).respond(201)
    git_backup.push_file(provider="gitlab", base_url="https://gl.example", **kw)
    assert glpost.calls[0].request.headers["PRIVATE-TOKEN"] == "tok"
    respx.get("https://api.github.com/repos/o/r/contents/a/b.json").respond(500)
    with pytest.raises(git_backup.GitPushError):
        git_backup.push_file(provider="github", base_url=None, **kw)


@respx.mock
async def test_api_run_list_restore_and_git(authenticated_client, viewer_client, fake_pg_dump):
    c = authenticated_client
    put = respx.put(url__regex=r"https://api\.github\.com/repos/o/r/contents/.*").respond(201)
    respx.get(url__regex=r"https://api\.github\.com/repos/o/r/contents/.*").respond(404)
    r = await c.put(
        "/api/settings/ops",
        json={"git_provider": "github", "git_repo": "o/r", "git_token": "secret-tok"},
    )
    assert r.status_code == 200 and r.json()["git_token_set"] is True
    assert "secret-tok" not in r.text

    r = await c.post("/api/admin/backups/run")
    assert r.status_code == 202
    runs = (await c.get("/api/admin/backups")).json()
    assert runs[0]["status"] == "ok" and runs[0]["git_pushed"] is True and put.called
    run_id = runs[0]["id"]

    # mutate a setting, then restore the settings JSON
    with_db = await c.put(
        "/api/settings/app",
        json={
            "printer_enabled": False,
            "scan_interval_s": 0,
            "collection_sync_interval_s": 0,
            "watch_interval_s": 0,
            "watch_stable_s": 10.0,
        },
    )
    assert with_db.status_code == 200
    r = await c.post(f"/api/admin/backups/{run_id}/restore-settings")
    assert r.status_code == 200 and r.json()["restored"] >= 1
    assert (await c.post("/api/admin/backups/99999/restore-settings")).status_code == 404

    for method, path in (
        ("POST", "/api/admin/backups/run"),
        ("GET", "/api/admin/backups"),
        ("POST", f"/api/admin/backups/{run_id}/restore-settings"),
        ("GET", "/api/admin/support-bundle"),
    ):
        assert (await viewer_client.request(method, path)).status_code == 403


async def test_support_bundle_redacts(authenticated_client):
    import io
    import zipfile

    await authenticated_client.put("/api/settings/ops", json={"git_token": "super-secret"})
    r = await authenticated_client.get("/api/admin/support-bundle")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"settings.json", "versions.json", "logs.txt", "logs-services.txt"}
    assert "super-secret" not in z.read("settings.json").decode()
    assert "app_version" in z.read("versions.json").decode()


def test_scheduled_gate(fake_pg_dump):
    from app.tasks.ops import backup_scheduled

    assert backup_scheduled.delay().get() is None  # disabled
    with base.sync_session() as s:
        s.add(Setting(key="ops", value={"backup_enabled": True, "backup_interval_hours": 24}))
        s.commit()
    first = backup_scheduled.delay().get()
    assert first is not None
    assert backup_scheduled.delay().get() is None  # recent run -> not due
    with base.sync_session() as s:
        assert s.scalar(select(BackupRun.kind).where(BackupRun.id == first)) == "scheduled"


async def test_stale_running_run_is_expired(authenticated_client, fake_pg_dump):
    from datetime import UTC, datetime, timedelta

    with base.sync_session() as s:
        run = BackupRun(kind="manual", status="running")
        s.add(run)
        s.commit()
        run.created_at = datetime.now(UTC) - timedelta(seconds=svc.STALE_AFTER_S + 60)
        s.commit()
        rid = run.id
    r = await authenticated_client.post("/api/admin/backups/run")
    assert r.status_code == 202  # not 409: the stuck run was failed
    with base.sync_session() as s:
        assert s.get(BackupRun, rid).status == "failed"


def test_restore_skips_storage_row(fake_pg_dump):
    with base.sync_session() as s:
        s.add(Setting(key="storage", value={"backend": "local", "v": 1}))
        s.add(Setting(key="app", value={"x": 1}))
        s.commit()
        run_id = svc.new_run(s, "manual", None).id
    backup_run.delay(run_id).get()
    with base.sync_session() as s:
        s.get(Setting, "storage").value = {"backend": "local", "v": 2}
        s.get(Setting, "app").value = {"x": 2}
        s.commit()
        n = svc.restore_settings(s, get_settings(), s.get(BackupRun, run_id))
        assert n >= 1
        assert s.get(Setting, "storage").value["v"] == 2
        assert s.get(Setting, "app").value == {"x": 1}


def test_scheduled_gate_skips_while_running_and_expires_stale(fake_pg_dump):
    from datetime import UTC, datetime, timedelta

    from app.tasks.ops import backup_scheduled

    with base.sync_session() as s:
        s.add(Setting(key="ops", value={"backup_enabled": True}))
        run = BackupRun(kind="scheduled", status="running")
        s.add(run)
        s.commit()
    assert backup_scheduled.delay().get() is None  # one in progress
    with base.sync_session() as s:
        r = s.scalar(select(BackupRun))
        r.created_at = datetime.now(UTC) - timedelta(seconds=svc.STALE_AFTER_S + 60)
        s.commit()
    assert backup_scheduled.delay().get() is not None  # stale one expired -> new run
