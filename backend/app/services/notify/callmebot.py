from __future__ import annotations

import httpx

from app.services.notify.base import Message, ensure_ok, field, need


class CallMeBot:
    kind = "callmebot"
    label = "CallMeBot (WhatsApp)"
    supports_photo = False
    fields = [
        field("phone", "Phone (with country code)", required=True),
        field("apikey", "API key", secret=True, required=True),
    ]

    def send(self, config: dict, msg: Message, client: httpx.Client) -> None:
        phone, apikey = need(config, "phone", "apikey")
        resp = client.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": phone, "text": f"{msg.title}\n{msg.body}", "apikey": apikey},
        )
        ensure_ok(resp, "CallMeBot")
