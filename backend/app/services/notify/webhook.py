from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx

from app.services.notify.base import Message, check_url, ensure_ok, field, need


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """``sha256=<hex>`` HMAC over ``"<timestamp>.<body>"`` (replay-resistant)."""
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


class Webhook:
    kind = "webhook"
    label = "Webhook"
    supports_photo = False
    fields = [
        field("url", "URL", required=True),
        field("secret", "Authorization header value", secret=True),
        field(
            "signing_secret",
            "Signing secret (adds X-TDMM-Signature / X-TDMM-Timestamp HMAC-SHA256 headers)",
            secret=True,
        ),
        field(
            "template",
            "JSON body template (optional, {title} {body} {event})",
            placeholder='{"text": "{title}: {body}"}',
        ),
    ]

    def send(self, config: dict, msg: Message, client: httpx.Client) -> None:
        (url,) = need(config, "url")
        url = check_url(url, "URL")
        headers = {"Content-Type": "application/json", "X-TDMM-Event": msg.event}
        if secret := str(config.get("secret") or "").strip():
            headers["Authorization"] = secret
        tpl = str(config.get("template") or "").strip()
        if tpl:
            # Values are JSON-escaped so a quote in a title cannot break the document.
            esc = {
                k: json.dumps(v)[1:-1]
                for k, v in {"title": msg.title, "body": msg.body, "event": msg.event}.items()
            }
            text = tpl
            for k, v in esc.items():
                text = text.replace("{" + k + "}", v)
            content = text.encode()
        else:
            content = json.dumps(
                {"event": msg.event, "title": msg.title, "body": msg.body}
            ).encode()
        if signing := str(config.get("signing_secret") or "").strip():
            ts = str(int(time.time()))
            headers["X-TDMM-Timestamp"] = ts
            headers["X-TDMM-Signature"] = sign(signing, ts, content)
        resp = client.post(url, content=content, headers=headers)
        ensure_ok(resp, "webhook")
