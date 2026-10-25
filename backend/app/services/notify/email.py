from __future__ import annotations

from email.message import EmailMessage

import httpx

from app.services.mailer import MailError, from_header, smtp_deliver
from app.services.notify.base import Message, ProviderError, field, need


class Email:
    kind = "email"
    label = "Email (SMTP)"
    supports_photo = True
    fields = [
        field(
            "use_server_smtp",
            "Use server SMTP (Settings -> Server)",
            type="select",
            options=["false", "true"],
            default="false",
        ),
        field("host", "SMTP host"),
        field("port", "Port", type="number", default="587"),
        field(
            "security",
            "Security",
            type="select",
            options=["starttls", "ssl", "none"],
            default="starttls",
        ),
        field("username", "Username"),
        field("password", "Password", secret=True),
        field("from_addr", "From"),
        field("to_addr", "To (comma separated)", required=True),
    ]

    def send(self, config: dict, msg: Message, client: httpx.Client | None = None) -> None:
        if str(config.get("use_server_smtp") or "").lower() in ("true", "1", "yes"):
            self._send_server(config, msg)
            return
        host, from_addr, to_addr = need(config, "host", "from_addr", "to_addr")
        try:
            port = int(str(config.get("port") or 587))
        except ValueError as exc:
            raise ProviderError("port must be a number") from exc
        mail = _compose(msg, from_addr, to_addr)
        try:
            smtp_deliver(
                mail,
                host=host,
                port=port,
                security=str(config.get("security") or "starttls"),
                username=str(config["username"]) if config.get("username") else None,
                password=str(config.get("password") or ""),
            )
        except MailError as exc:
            raise ProviderError(str(exc)) from exc

    @staticmethod
    def _send_server(config: dict, msg: Message) -> None:
        from app.services.server_config import current_sync

        (to_addr,) = need(config, "to_addr")
        cfg = current_sync().smtp
        if not cfg.configured:
            raise ProviderError("server SMTP is not configured")
        mail = _compose(msg, from_header(cfg), to_addr)
        try:
            smtp_deliver(
                mail,
                host=cfg.host,
                port=cfg.port,
                security=cfg.security,
                username=cfg.username or None,
                password=cfg.password.get_secret_value() if cfg.password else None,
            )
        except MailError as exc:
            raise ProviderError(str(exc)) from exc


def _compose(msg: Message, from_addr: str, to_addr: str) -> EmailMessage:
    mail = EmailMessage()
    mail["Subject"] = msg.title
    mail["From"] = from_addr
    mail["To"] = to_addr
    mail.set_content(msg.body)
    if msg.photo:
        mail.add_attachment(msg.photo, maintype="image", subtype="jpeg", filename="photo.jpg")
    return mail
