from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

import httpx

from app.services.notify.base import Message, ProviderError, field, need


class Email:
    kind = "email"
    label = "Email (SMTP)"
    supports_photo = True
    fields = [
        field("host", "SMTP host", required=True),
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
        field("from_addr", "From", required=True),
        field("to_addr", "To (comma separated)", required=True),
    ]

    def send(self, config: dict, msg: Message, client: httpx.Client | None = None) -> None:
        host, from_addr, to_addr = need(config, "host", "from_addr", "to_addr")
        try:
            port = int(str(config.get("port") or 587))
        except ValueError as exc:
            raise ProviderError("port must be a number") from exc
        security = str(config.get("security") or "starttls")
        mail = EmailMessage()
        mail["Subject"] = msg.title
        mail["From"] = from_addr
        mail["To"] = to_addr
        mail.set_content(msg.body)
        if msg.photo:
            mail.add_attachment(msg.photo, maintype="image", subtype="jpeg", filename="photo.jpg")
        try:
            if security == "ssl":
                smtp = smtplib.SMTP_SSL(
                    host, port, timeout=15, context=ssl.create_default_context()
                )
            else:
                smtp = smtplib.SMTP(host, port, timeout=15)
            with smtp:
                if security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                if config.get("username"):
                    smtp.login(str(config["username"]), str(config.get("password") or ""))
                smtp.send_message(mail)
        except (smtplib.SMTPException, OSError) as exc:
            raise ProviderError(f"SMTP failed: {type(exc).__name__}") from exc
