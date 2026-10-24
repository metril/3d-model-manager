from __future__ import annotations

import json

import httpx

from app.services.notify.base import Message, check_url, ensure_ok, field, need


class Webhook:
    kind = "webhook"
    label = "Webhook"
    supports_photo = False
    fields = [
        field("url", "URL", required=True),
        field("secret", "Authorization header value", secret=True),
        field(
            "template",
            "JSON body template (optional, {title} {body} {event})",
            placeholder='{"text": "{title}: {body}"}',
        ),
    ]

    def send(self, config: dict, msg: Message, client: httpx.Client) -> None:
        (url,) = need(config, "url")
        url = check_url(url, "URL")
        headers = {}
        if secret := str(config.get("secret") or "").strip():
            headers["Authorization"] = secret
        tpl = str(config.get("template") or "").strip()
        if tpl:
            # Values are JSON-escaped so a quote in a title cannot break the document.
            esc = {
                k: json.dumps(v)[1:-1]
                for k, v in {"title": msg.title, "body": msg.body, "event": msg.event}.items()
            }
            body = tpl
            for k, v in esc.items():
                body = body.replace("{" + k + "}", v)
            resp = client.post(
                url, content=body.encode(), headers={**headers, "Content-Type": "application/json"}
            )
        else:
            resp = client.post(
                url,
                json={"event": msg.event, "title": msg.title, "body": msg.body},
                headers=headers,
            )
        ensure_ok(resp, "webhook")
