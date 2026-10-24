from __future__ import annotations

import httpx

from app.services.ha_client import HaClient, HaUnreachable
from app.services.notify.base import Message, ProviderError, field, need


class HomeAssistant:
    """Calls ``notify.<service>`` through the HA connection from Settings."""

    kind = "homeassistant"
    label = "Home Assistant"
    supports_photo = False
    fields = [field("service", "Notify service", required=True, placeholder="mobile_app_phone")]

    def send(self, config: dict, msg: Message, client: httpx.Client | None = None) -> None:
        (service,) = need(config, "service")
        service = service.removeprefix("notify.")
        from app.config import get_settings
        from app.services.ha_config import get_ha_config_sync
        from app.tasks import base

        with base.sync_session() as s:
            cfg = get_ha_config_sync(s, get_settings())
        if not cfg.ha_url or not cfg.ha_token:
            raise ProviderError("Home Assistant is not configured")
        ha = HaClient(cfg.ha_url, cfg.ha_token)
        try:
            ha.call_service("notify", service, {"title": msg.title, "message": msg.body})
        except HaUnreachable as exc:
            raise ProviderError(str(exc)) from exc
        finally:
            ha.close()
