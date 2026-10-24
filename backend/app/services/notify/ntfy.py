from __future__ import annotations

import httpx

from app.services.notify.base import Message, check_url, ensure_ok, field, need


class Ntfy:
    kind = "ntfy"
    label = "ntfy"
    supports_photo = True
    fields = [
        field("server", "Server", default="https://ntfy.sh", placeholder="https://ntfy.sh"),
        field("topic", "Topic", required=True),
        field("auth_token", "Access token", secret=True),
        field("priority", "Priority (1-5)", type="number"),
    ]

    def send(self, config: dict, msg: Message, client: httpx.Client) -> None:
        (topic,) = need(config, "topic")
        server = check_url(str(config.get("server") or "https://ntfy.sh"), "server")
        headers: dict[str, str | bytes] = {"Title": msg.title.encode()}
        if token := str(config.get("auth_token") or "").strip():
            headers["Authorization"] = f"Bearer {token}"
        if str(config.get("priority") or "").strip() in {"1", "2", "3", "4", "5"}:
            headers["Priority"] = str(config["priority"]).strip()
        if msg.photo:
            headers["Filename"] = "photo.jpg"
            headers["Message"] = msg.body.replace("\n", "\\n").encode()
            resp = client.put(f"{server}/{topic}", content=msg.photo, headers=headers)
        else:
            resp = client.post(f"{server}/{topic}", content=msg.body.encode(), headers=headers)
        ensure_ok(resp, "ntfy")
