"""``GET /api/features`` (M4 Task 4): session-gated but NOT printer-gated --
the frontend reads this to decide whether to show the Printer nav even when
the feature flag is off (SPEC "API surface").

Round 8 T6 adds the watched-folder slicer fields (`app.tasks.slicer_watch`,
Round 8 T5): `watch_dir` (the container path, or null) and
`watch_enabled` (dir set AND a positive poll interval -- the same
condition `app.tasks.celery_app` uses to register the beat entry).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.models import Setting


async def test_features_disabled_by_default(authenticated_client):
    r = await authenticated_client.get("/api/features")
    assert r.status_code == 200 and r.json() == {
        "printer_enabled": False,
        "watch_dir": None,
        "watch_enabled": False,
        "virtual_printer_enabled": True,
        "slicer_configured": False,
        "orcaslicer_configured": False,
    }


async def test_features_enabled(authenticated_client, printer_enabled):
    r = await authenticated_client.get("/api/features")
    assert r.json() == {
        "printer_enabled": True,
        "watch_dir": None,
        "watch_enabled": False,
        "virtual_printer_enabled": True,
        "slicer_configured": False,
        "orcaslicer_configured": False,
    }


@pytest.fixture
def slicer_watch_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Sets both `WATCH_DIR` and a positive poll interval --
    mirrors `test_slicer_watch.py`'s `watch_dir` fixture, plus the interval
    the beat-entry condition also requires."""
    watch = tmp_path / "watch"
    watch.mkdir()
    monkeypatch.setenv("WATCH_DIR", str(watch))
    monkeypatch.setenv("WATCH_INTERVAL", "30")
    get_settings.cache_clear()
    yield watch
    get_settings.cache_clear()


async def test_features_slicer_watch_enabled(authenticated_client, slicer_watch_configured):
    r = await authenticated_client.get("/api/features")
    body = r.json()
    assert body["watch_enabled"] is True
    assert body["watch_dir"] == str(slicer_watch_configured)


async def test_features_slicer_watch_dir_without_interval_stays_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authenticated_client
):
    """A configured dir with the interval left at 0 (the default) must NOT
    report enabled -- mirrors the beat-entry condition in
    `app.tasks.celery_app`, which needs BOTH."""
    watch = tmp_path / "watch"
    watch.mkdir()
    monkeypatch.setenv("WATCH_DIR", str(watch))
    get_settings.cache_clear()

    r = await authenticated_client.get("/api/features")
    body = r.json()

    get_settings.cache_clear()
    assert body["watch_dir"] == str(watch)
    assert body["watch_enabled"] is False


# ---------------------------------------------------------------------------
# Round 10 T3: printer_enabled and watch_enabled's interval half now come
# from the DB-backed AppConfig (app.services.app_config), not the env-only
# Settings snapshot the two tests above still exercise.
# ---------------------------------------------------------------------------


async def test_features_db_row_overrides_env(
    authenticated_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_session
):
    """A DB row's printer_enabled/watch_interval_s win even when the
    corresponding env var is unset (left at its default/False) -- proving
    /features reads live off the DB, not a process-start env snapshot.
    watch_dir itself stays env-only (a filesystem path, not a
    runtime-editable setting), so it's seeded via WATCH_DIR as usual."""
    watch = tmp_path / "watch"
    watch.mkdir()
    monkeypatch.setenv("WATCH_DIR", str(watch))  # WATCH_INTERVAL left unset
    get_settings.cache_clear()
    db_session.add(Setting(key="app", value={"printer_enabled": True, "watch_interval_s": 30}))
    await db_session.commit()

    r = await authenticated_client.get("/api/features")
    body = r.json()

    get_settings.cache_clear()
    assert body["printer_enabled"] is True
    assert body["watch_dir"] == str(watch)
    assert body["watch_enabled"] is True


async def test_features_slicer_configured(authenticated_client):
    from tests.server_helpers import set_server

    await set_server(slicer_enabled=True, slicer_url="http://slicer:3001")
    assert (await authenticated_client.get("/api/features")).json()["slicer_configured"] is True
    await set_server(slicer_enabled=False)
    assert (await authenticated_client.get("/api/features")).json()["slicer_configured"] is False
    await set_server(slicer_enabled=True, slicer_url="")
    assert (await authenticated_client.get("/api/features")).json()["slicer_configured"] is False


async def test_features_orcaslicer_configured(authenticated_client):
    from tests.server_helpers import set_server

    await set_server(orcaslicer_enabled=True, orcaslicer_url="http://orcaslicer:3000")
    assert (await authenticated_client.get("/api/features")).json()["orcaslicer_configured"] is True
    await set_server(orcaslicer_enabled=False)
    assert (await authenticated_client.get("/api/features")).json()[
        "orcaslicer_configured"
    ] is False
    await set_server(orcaslicer_enabled=True, orcaslicer_url="")
    assert (await authenticated_client.get("/api/features")).json()[
        "orcaslicer_configured"
    ] is False
