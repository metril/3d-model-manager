from __future__ import annotations

import httpx

from app.services.notify.base import Message, ProviderError, field, need


class Pushover:
    kind = "pushover"
    label = "Pushover"
    supports_photo = True
    fields = [
        field("user_key", "User key", secret=True, required=True),
        field("app_token", "App token", secret=True, required=True),
        field("device", "Device"),
        field("priority", "Priority (-2..2)", type="number"),
    ]

    def send(self, config: dict, msg: Message, client: httpx.Client) -> None:
        user, token = need(config, "user_key", "app_token")
        data = {"token": token, "user": user, "title": msg.title, "message": msg.body}
        if device := str(config.get("device") or "").strip():
            data["device"] = device
        if str(config.get("priority") or "").strip() in {"-2", "-1", "0", "1", "2"}:
            data["priority"] = str(config["priority"]).strip()
        files = {"attachment": ("photo.jpg", msg.photo, "image/jpeg")} if msg.photo else None
        resp = client.post("https://api.pushover.net/1/messages.json", data=data, files=files)
        if resp.status_code != 200:
            raise ProviderError(f"Pushover returned HTTP {resp.status_code}")
