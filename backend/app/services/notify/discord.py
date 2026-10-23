from __future__ import annotations

import json

import httpx

from app.services.notify.base import Message, check_url, ensure_ok, field, need


class Discord:
    kind = "discord"
    label = "Discord"
    supports_photo = True
    fields = [field("webhook_url", "Webhook URL", secret=True, required=True)]

    def send(self, config: dict, msg: Message, client: httpx.Client) -> None:
        (url,) = need(config, "webhook_url")
        url = check_url(url, "webhook URL")
        content = f"**{msg.title}**\n{msg.body}"[:2000]
        if msg.photo:
            resp = client.post(
                url,
                data={"payload_json": json.dumps({"content": content})},
                files={"file": ("photo.jpg", msg.photo, "image/jpeg")},
            )
        else:
            resp = client.post(url, json={"content": content})
        ensure_ok(resp, "Discord")
