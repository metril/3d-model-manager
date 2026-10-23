from __future__ import annotations

import httpx

from app.services.notify.base import Message, ensure_ok, field, need


class Telegram:
    kind = "telegram"
    label = "Telegram"
    supports_photo = True
    fields = [
        field("bot_token", "Bot token", secret=True, required=True),
        field("chat_id", "Chat ID", required=True),
    ]

    def send(self, config: dict, msg: Message, client: httpx.Client) -> None:
        token, chat = need(config, "bot_token", "chat_id")
        base = f"https://api.telegram.org/bot{token}"
        text = f"{msg.title}\n{msg.body}"
        if msg.photo:
            resp = client.post(
                f"{base}/sendPhoto",
                data={"chat_id": chat, "caption": text[:1024]},
                files={"photo": ("photo.jpg", msg.photo, "image/jpeg")},
            )
        else:
            resp = client.post(f"{base}/sendMessage", json={"chat_id": chat, "text": text})
        ensure_ok(resp, "Telegram")
