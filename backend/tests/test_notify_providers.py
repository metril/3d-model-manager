import json
from unittest.mock import MagicMock

import httpx
import pytest

from app.services.ha_config import HaConfig
from app.services.notify import PROVIDERS, Message, ProviderError

MSG = Message("Title é", "line1\nline2", "job_finished")
PHOTO = Message("T", "B", "job_finished", photo=b"\xff\xd8jpeg\xff\xd9")


def _client(status: int = 200, body: dict | None = None):
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status, json=body or {})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_ntfy_text_and_photo():
    c, seen = _client()
    PROVIDERS["ntfy"].send({"topic": "t", "auth_token": "tok", "priority": "4"}, MSG, c)
    r = seen[0]
    assert r.method == "POST" and str(r.url) == "https://ntfy.sh/t"
    assert r.headers["authorization"] == "Bearer tok" and r.headers["priority"] == "4"
    assert r.headers["title"] == "Title é" or r.headers["title"]
    PROVIDERS["ntfy"].send({"server": "http://n.lan", "topic": "t"}, PHOTO, c)
    r = seen[1]
    assert r.method == "PUT" and r.content == PHOTO.photo and r.headers["filename"] == "photo.jpg"


def test_ntfy_requires_topic_and_http_error_has_no_body():
    c, _ = _client(500, {"secret": "leak"})
    with pytest.raises(ProviderError, match="topic"):
        PROVIDERS["ntfy"].send({}, MSG, c)
    with pytest.raises(ProviderError) as e:
        PROVIDERS["ntfy"].send({"topic": "t"}, MSG, c)
    assert "500" in str(e.value) and "leak" not in str(e.value)


def test_pushover_photo_multipart():
    c, seen = _client()
    PROVIDERS["pushover"].send({"user_key": "u", "app_token": "a"}, PHOTO, c)
    r = seen[0]
    assert "api.pushover.net" in str(r.url)
    assert r.headers["content-type"].startswith("multipart/form-data")
    assert b"photo.jpg" in r.content


def test_telegram_message_and_photo():
    c, seen = _client()
    cfg = {"bot_token": "123:abc", "chat_id": "9"}
    PROVIDERS["telegram"].send(cfg, MSG, c)
    assert str(seen[0].url) == "https://api.telegram.org/bot123:abc/sendMessage"
    assert json.loads(seen[0].content)["chat_id"] == "9"
    PROVIDERS["telegram"].send(cfg, PHOTO, c)
    assert seen[1].url.path.endswith("/sendPhoto")


def test_discord_json_and_photo():
    c, seen = _client()
    cfg = {"webhook_url": "https://discord.com/api/webhooks/1/x"}
    PROVIDERS["discord"].send(cfg, MSG, c)
    assert "**Title é**" in json.loads(seen[0].content)["content"]
    PROVIDERS["discord"].send(cfg, PHOTO, c)
    assert b"payload_json" in seen[1].content


def test_webhook_default_and_template_escapes_json():
    c, seen = _client()
    PROVIDERS["webhook"].send({"url": "http://h/x", "secret": "s3"}, MSG, c)
    assert json.loads(seen[0].content)["event"] == "job_finished"
    assert seen[0].headers["authorization"] == "s3"
    msg = Message('say "hi"', "b", "e")
    PROVIDERS["webhook"].send({"url": "http://h/x", "template": '{"text": "{title}"}'}, msg, c)
    assert json.loads(seen[1].content) == {"text": 'say "hi"'}


def test_webhook_hmac_signature():
    import hashlib
    import hmac

    c, seen = _client()
    PROVIDERS["webhook"].send({"url": "http://h/x", "signing_secret": "topsecret"}, MSG, c)
    req = seen[0]
    ts = req.headers["x-tdmm-timestamp"]
    expect = hmac.new(b"topsecret", ts.encode() + b"." + req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-tdmm-signature"] == f"sha256={expect}"
    assert req.headers["x-tdmm-event"] == "job_finished"
    PROVIDERS["webhook"].send({"url": "http://h/x"}, MSG, c)
    assert "x-tdmm-signature" not in seen[1].headers


def test_callmebot_and_bark():
    c, seen = _client()
    PROVIDERS["callmebot"].send({"phone": "+1", "apikey": "k"}, MSG, c)
    assert seen[0].url.params["apikey"] == "k"
    PROVIDERS["bark"].send({"device_key": "dk", "group": "g"}, MSG, c)
    assert seen[1].url.path == "/push" and json.loads(seen[1].content)["group"] == "g"
    bad, _ = _client(200, {"code": 400})
    with pytest.raises(ProviderError, match="400"):
        PROVIDERS["bark"].send({"device_key": "dk"}, MSG, bad)


def test_email_sends_with_attachment(monkeypatch):
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    monkeypatch.setattr("app.services.mailer.smtplib.SMTP", MagicMock(return_value=smtp))
    cfg = {
        "host": "mx",
        "port": "587",
        "username": "u",
        "password": "p",
        "from_addr": "a@x",
        "to_addr": "b@x",
    }
    PROVIDERS["email"].send(cfg, PHOTO, None)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("u", "p")
    sent = smtp.send_message.call_args.args[0]
    assert sent["Subject"] == "T" and any(sent.iter_attachments())


def test_email_failure_is_provider_error(monkeypatch):
    import smtplib

    monkeypatch.setattr(
        "app.services.mailer.smtplib.SMTP", MagicMock(side_effect=smtplib.SMTPException("x"))
    )
    with pytest.raises(ProviderError, match="SMTP"):
        PROVIDERS["email"].send({"host": "h", "from_addr": "a", "to_addr": "b"}, MSG, None)


def test_homeassistant_calls_notify_service(monkeypatch):
    ha = MagicMock()
    monkeypatch.setattr("app.services.notify.homeassistant.HaClient", MagicMock(return_value=ha))
    cfg = HaConfig(None, None, None, "http://ha", "tok")
    monkeypatch.setattr("app.services.ha_config.get_ha_config_sync", lambda s, st: cfg)
    PROVIDERS["homeassistant"].send({"service": "notify.mobile_app_p"}, MSG, None)
    ha.call_service.assert_called_once_with(
        "notify", "mobile_app_p", {"title": "Title é", "message": "line1\nline2"}
    )


def test_ha_client_call_service():
    from app.services.ha_client import HaClient, HaUnreachable

    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(500 if "bad" in req.url.path else 200, json=[])

    ha = HaClient("http://ha", "tok")
    ha._http = httpx.Client(base_url="http://ha", transport=httpx.MockTransport(handler))
    ha.call_service("notify", "x", {"message": "m"})
    assert seen[0].url.path == "/api/services/notify/x"
    with pytest.raises(HaUnreachable):
        ha.call_service("notify", "bad", {})
