"""Apprise provider: fans one message out to any Apprise URL (Slack, Discord,
Matrix, Gotify, 100+ services) in-process. URLs embed secrets, so they are
never echoed in errors or logs -- only ``scheme://host`` appears."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

from app.services.notify.base import Message, ProviderError, field, need


def _target(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}" if parts.hostname else f"{parts.scheme}://"


class Apprise:
    kind = "apprise"
    label = "Apprise (Slack, Discord, Matrix, Gotify, 100+ services)"
    supports_photo = True
    fields = [
        field(
            "urls",
            "Apprise URLs",
            secret=True,
            required=True,
            multiline=True,
            help="One URL per line. See the Apprise URL wiki: github.com/caronc/apprise/wiki",
        ),
    ]

    def send(self, config: dict, msg: Message, client: httpx.Client) -> None:
        import apprise  # lazy: keeps the provider registry import light
        from apprise.attachment.memory import AttachMemory

        # Apprise logs target URLs at INFO/DEBUG; keep it at WARNING.
        logging.getLogger("apprise").setLevel(logging.WARNING)
        (raw,) = need(config, "urls")
        urls: list[tuple[int, str]] = []
        for n, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append((n, line))
        if not urls:
            raise ProviderError("no Apprise URLs configured")

        targets = []
        for n, url in urls:
            a = apprise.Apprise()
            if not a.add(url):
                raise ProviderError(f"invalid Apprise URL on line {n}")
            targets.append((a, _target(url)))

        failed: list[str] = []
        for a, target in targets:
            attach = (
                apprise.AppriseAttachment(
                    AttachMemory(content=msg.photo, name="photo.jpg", mimetype="image/jpeg")
                )
                if msg.photo
                else None
            )
            try:
                ok = a.notify(
                    title=msg.title,
                    body=msg.body,
                    body_format=apprise.NotifyFormat.TEXT,
                    attach=attach,
                )
            except Exception:  # noqa: BLE001 - exception text may carry the URL
                ok = False
            if not ok:
                failed.append(target)
        if failed:
            raise ProviderError(f"Apprise delivery failed: {', '.join(failed)}")
