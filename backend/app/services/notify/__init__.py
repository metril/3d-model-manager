"""Notification providers registry (Q6)."""

from __future__ import annotations

from app.services.notify.bark import Bark
from app.services.notify.base import MASK, Message, Provider, ProviderError
from app.services.notify.callmebot import CallMeBot
from app.services.notify.discord import Discord
from app.services.notify.email import Email
from app.services.notify.homeassistant import HomeAssistant
from app.services.notify.ntfy import Ntfy
from app.services.notify.pushover import Pushover
from app.services.notify.telegram import Telegram
from app.services.notify.webhook import Webhook

PROVIDERS: dict[str, Provider] = {
    p.kind: p
    for p in (
        Ntfy(),
        Pushover(),
        Telegram(),
        Discord(),
        Webhook(),
        Email(),
        CallMeBot(),
        Bark(),
        HomeAssistant(),
    )
}

__all__ = ["MASK", "PROVIDERS", "Message", "Provider", "ProviderError"]
