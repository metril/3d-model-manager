"""Backups (Q7): ``pg_dump`` + a settings JSON export, written through the
configured storage backend under ``backups/<stamp>/``, optionally pushed to a
git host. The DB is NEVER auto-restored; only the settings JSON can be
re-applied (``restore_settings``). Manual DB restore: see the Q7 plan doc."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import BackupRun, Setting
from app.services import git_backup
from app.services.ops_settings import OpsConfig, get_ops_config_sync
from app.services.storage_config import resolve_backend_sync
from app.storage.base import StorageBackend

log = logging.getLogger(__name__)

PREFIX = "backups"
DUMP_TIMEOUT_S = 1800
# Rows never exported/restored: transient or derived caches.
_SKIP_SETTINGS = {"update_cache"}


class BackupError(Exception):
    pass


def pg_args(database_url: str) -> tuple[list[str], dict[str, str]]:
    """libpq connection args + env for ``pg_dump`` from ``DATABASE_URL``
    (password via ``PGPASSWORD``, never on the command line)."""
    u = urlsplit(database_url.replace("+asyncpg", "").replace("+psycopg", ""))
    args = ["--host", u.hostname or "localhost", "--port", str(u.port or 5432)]
    if u.username:
        args += ["--username", unquote(u.username)]
    args += ["--dbname", (u.path or "/").lstrip("/")]
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)
    return args, env


def dump_database(settings: Settings, dest: Path) -> None:
    args, env = pg_args(settings.database_url)
    cmd = ["pg_dump", "--format=custom", "--no-owner", *args]
    try:
        with dest.open("wb") as fh:
            proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell
                cmd,
                stdout=fh,
                stderr=subprocess.PIPE,
                env=env,
                timeout=DUMP_TIMEOUT_S,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        raise BackupError("pg_dump could not run") from None
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode(errors="replace").strip().splitlines()[-1:]
        raise BackupError(f"pg_dump failed: {tail[0][:200] if tail else proc.returncode}")


def export_settings(session: Session) -> dict:
    rows = session.scalars(select(Setting).order_by(Setting.key)).all()
    return {r.key: r.value for r in rows if r.key not in _SKIP_SETTINGS}


def _chunks(path: Path, size: int = 1 << 20) -> Iterator[bytes]:
    with path.open("rb") as fh:
        while chunk := fh.read(size):
            yield chunk


def _prune(backend: StorageBackend, session: Session, keep: int) -> None:
    runs = session.scalars(
        select(BackupRun)
        .where(BackupRun.status == "ok", BackupRun.location.is_not(None))
        .order_by(BackupRun.id.desc())
    ).all()
    for run in runs[max(keep, 1) :]:
        try:
            for entry in list(backend.walk(run.location)):
                backend.delete(entry.key)
        except Exception:  # noqa: BLE001 -- pruning is best-effort
            log.warning("backup prune failed for run %s", run.id)
            continue
        run.location = None
        run.status = "pruned"


def _git_push(cfg: OpsConfig, stamp: str, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        git_backup.push_file(
            provider=cfg.git_provider or "",
            base_url=cfg.git_base_url,
            repo=cfg.git_repo or "",
            branch=cfg.git_branch,
            token=cfg.git_token or "",
            path=f"tdmm-backups/{name}",
            content=content,
            message=f"backup {stamp}",
        )


def new_run(session: Session, kind: str, user_id: int | None) -> BackupRun:
    run = BackupRun(kind=kind, status="running", created_by_user_id=user_id)
    session.add(run)
    session.commit()
    return run


def run_backup(session: Session, settings: Settings, run_id: int) -> BackupRun:
    run = session.get(BackupRun, run_id)
    if run is None:
        raise BackupError("backup run not found")
    cfg = get_ops_config_sync(session, settings)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"{PREFIX}/{stamp}-{run.id}"
    try:
        backend = resolve_backend_sync(session, settings)
        settings_json = json.dumps(export_settings(session), indent=2, sort_keys=True).encode()
        with tempfile.TemporaryDirectory(prefix="tdmm-backup-") as tmp:
            dump = Path(tmp) / "db.dump"
            dump_database(settings, dump)
            size = dump.stat().st_size + len(settings_json)
            backend.write(f"{prefix}/db.dump", _chunks(dump))
            backend.write(f"{prefix}/settings.json", iter([settings_json]))
            run.location, run.size_bytes = prefix, size
            if cfg.git_active:
                files = {f"{stamp}/settings.json": settings_json}
                if dump.stat().st_size <= git_backup.MAX_FILE_BYTES:
                    files[f"{stamp}/db.dump"] = dump.read_bytes()
                _git_push(cfg, stamp, files)
                run.git_pushed = True
        run.status = "ok"
        run.error = None
        _prune(backend, session, cfg.backup_retention)
    except (BackupError, git_backup.GitPushError) as exc:
        run.status = "failed"
        run.error = str(exc)[:500]
    except Exception:  # noqa: BLE001
        log.exception("backup run %s failed", run.id)
        run.status = "failed"
        run.error = "unexpected error (see logs)"
    run.finished_at = datetime.now(UTC)
    session.commit()
    return run


def restore_settings(session: Session, settings: Settings, run: BackupRun) -> int:
    """Re-apply the settings JSON of ``run`` (upsert per key; keys absent from
    the file are left alone). Returns the number of keys restored."""
    if run.status != "ok" or not run.location:
        raise BackupError("backup is not available")
    backend = resolve_backend_sync(session, settings)
    try:
        data = json.loads(b"".join(backend.read(f"{run.location}/settings.json")))
    except (OSError, ValueError, FileNotFoundError):
        raise BackupError("settings.json is missing or unreadable") from None
    if not isinstance(data, dict):
        raise BackupError("settings.json is malformed")
    n = 0
    for key, value in data.items():
        if key in _SKIP_SETTINGS or not isinstance(value, dict):
            continue
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
        n += 1
    session.commit()
    return n


def due(session: Session, settings: Settings, now: datetime | None = None) -> bool:
    """Scheduled-backup gate: enabled and the last non-failed run is older
    than ``backup_interval_hours`` (or none exists)."""
    cfg = get_ops_config_sync(session, settings)
    if not cfg.backup_enabled:
        return False
    now = now or datetime.now(UTC)
    last = session.scalar(
        select(BackupRun.created_at)
        .where(BackupRun.status.in_(("running", "ok", "pruned")))
        .order_by(BackupRun.id.desc())
        .limit(1)
    )
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last).total_seconds() >= cfg.backup_interval_hours * 3600
