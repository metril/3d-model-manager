from __future__ import annotations

import httpx

from app.services.notify.base import Message, ProviderError, check_url, ensure_ok, field, need


class Bark:
    kind = "bark"
    label = "Bark (iOS)"
    supports_photo = False
    fields = [
        field("server", "Server", default="https://api.day.app"),
        field("device_key", "Device key", secret=True, required=True),
        field("group", "Group"),
        field("sound", "Sound"),
    ]

    def send(self, config: dict, msg: Message, client: httpx.Client) -> None:
        (key,) = need(config, "device_key")
        server = check_url(str(config.get("server") or "https://api.day.app"), "server")
        payload = {"device_key": key, "title": msg.title, "body": msg.body}
        for opt in ("group", "sound"):
            if str(config.get(opt) or "").strip():
                payload[opt] = str(config[opt]).strip()
        resp = client.post(f"{server}/push", json=payload)
        ensure_ok(resp, "Bark")
        try:
            body = resp.json()
        except ValueError:
            return
        if isinstance(body, dict) and body.get("code") not in (200, None):
            raise ProviderError(f"Bark error {body.get('code')}")
