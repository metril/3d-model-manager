import json

from sqlalchemy import select

from app.config import get_settings
from app.crypto import decrypt_secret
from app.models.notify import NotificationChannel, NotificationLog
from app.services.notify import ProviderError


async def _mk(c, **over):
    body = {
        "kind": "ntfy",
        "name": "phone",
        "config": {"topic": "t", "auth_token": "sekret"},
        "events": ["job_finished"],
        **over,
    }
    return await c.post("/api/notifications/channels", json=body)


async def test_channel_crud_masks_and_keeps_secrets(authenticated_client, db_session):
    c = authenticated_client
    r = await _mk(c, quiet_hours={"start": "22:00", "end": "07:00", "tz": "UTC"})
    assert r.status_code == 201, r.text
    ch = r.json()
    assert ch["config"]["auth_token"] == "********" and ch["config"]["topic"] == "t"
    raw = (await db_session.scalars(select(NotificationChannel))).one()
    assert "sekret" not in raw.config_enc
    assert json.loads(decrypt_secret(get_settings(), raw.config_enc))["auth_token"] == "sekret"

    r = await c.patch(
        f"/api/notifications/channels/{ch['id']}",
        json={"config": {"topic": "t2", "auth_token": "********"}, "enabled": False},
    )
    assert r.status_code == 200 and r.json()["config"]["topic"] == "t2"
    await db_session.refresh(raw)
    assert json.loads(decrypt_secret(get_settings(), raw.config_enc))["auth_token"] == "sekret"
    assert (await c.get("/api/notifications/channels")).json()[0]["enabled"] is False
    assert (await c.delete(f"/api/notifications/channels/{ch['id']}")).status_code == 204


async def test_channel_validation(authenticated_client):
    c = authenticated_client
    assert (await _mk(c, kind="nope")).status_code == 422
    assert (await _mk(c, config={})).status_code == 422  # topic required
    assert (await _mk(c, events=["bogus"])).status_code == 422
    assert (await _mk(c, digest_cron="not cron")).status_code == 422


async def test_test_send_logs_result(authenticated_client, monkeypatch, db_session):
    c = authenticated_client
    cid = (await _mk(c)).json()["id"]
    monkeypatch.setattr("app.api.notifications.send_message", lambda *a, **k: None)
    assert (await c.post(f"/api/notifications/channels/{cid}/test")).json() == {
        "ok": True,
        "error": None,
    }

    def boom(*a, **k):
        raise ProviderError("nope")

    monkeypatch.setattr("app.api.notifications.send_message", boom)
    assert (await c.post(f"/api/notifications/channels/{cid}/test")).json()["error"] == "nope"
    page = (await c.get("/api/notifications/log", params={"status": "failed"})).json()
    assert page["total"] == 1 and page["items"][0]["channel_name"] == "phone"
    assert len((await db_session.scalars(select(NotificationLog))).all()) == 2


async def test_templates_preview_crud_and_defaults(authenticated_client):
    c = authenticated_client
    d = (await c.get("/api/notifications/templates/defaults")).json()
    assert "job_finished" in d and d["job_finished"]["title_tpl"]
    r = await c.post(
        "/api/notifications/templates/preview",
        json={"event": "job_finished", "title_tpl": "Done {{ subtask_name }}", "body_tpl": "x"},
    )
    assert r.json() == {"title": "Done benchy.3mf", "body": "x", "error": None}
    bad = await c.post(
        "/api/notifications/templates/preview",
        json={"event": "job_finished", "title_tpl": "{{ nope }}", "body_tpl": "x"},
    )
    assert bad.json()["error"]
    body = {"event": "job_finished", "title_tpl": "T {{ printer_name }}", "body_tpl": "B"}
    r = await c.post("/api/notifications/templates", json=body)
    assert r.status_code == 201
    tid = r.json()["id"]
    assert (await c.post("/api/notifications/templates", json=body)).status_code == 409
    assert (
        await c.post("/api/notifications/templates", json={**body, "title_tpl": "{{ "})
    ).status_code in (409, 422)
    assert (
        await c.patch(f"/api/notifications/templates/{tid}", json={"body_tpl": "{% if %}"})
    ).status_code == 422
    assert (await c.patch(f"/api/notifications/templates/{tid}", json={"body_tpl": "B2"})).json()[
        "body_tpl"
    ] == "B2"
    assert (await c.delete(f"/api/notifications/templates/{tid}")).status_code == 204


async def test_events_catalog_is_session_only_and_management_is_gated(
    authenticated_client, viewer_client
):
    cat = (await viewer_client.get("/api/notifications/events")).json()
    assert {e["id"] for e in cat["events"]} >= {"job_finished", "hms_error", "dev_mode_off"}
    assert {k["kind"] for k in cat["kinds"]} == {
        "ntfy", "pushover", "telegram", "discord", "webhook", "email", "callmebot", "bark",
        "homeassistant",
    }  # fmt: skip
    for path in ("channels", "templates", "log"):
        assert (await viewer_client.get(f"/api/notifications/{path}")).status_code == 403
