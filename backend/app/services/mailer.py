"""Outgoing mail through the server-wide SMTP settings (``ServerConfig.smtp``).

``send`` only enqueues the Celery task ``app.tasks.mail.send`` (retries with
backoff); ``deliver`` is the synchronous smtplib transport shared by that task,
the ``/settings/server/smtp/test`` route and the email notify provider.
Message bodies and credentials are never logged.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.server_config import SmtpConfig, get_server_config

TIMEOUT_S = 15


class MailError(Exception):
    """Delivery failed; the message names the failure class, never a secret."""


async def is_configured(db: AsyncSession) -> bool:
    return (await get_server_config(db)).smtp.configured


def from_header(cfg: SmtpConfig) -> str:
    return formataddr((cfg.from_name, cfg.from_addr)) if cfg.from_name else cfg.from_addr


def smtp_deliver(
    mail: EmailMessage,
    *,
    host: str,
    port: int,
    security: str,
    username: str | None = None,
    password: str | None = None,
) -> None:
    """Send ``mail``. ``security``: ``none`` | ``starttls`` | ``tls`` (alias ``ssl``)."""
    try:
        if security in ("tls", "ssl"):
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                host, port, timeout=TIMEOUT_S, context=ssl.create_default_context()
            )
        else:
            smtp = smtplib.SMTP(host, port, timeout=TIMEOUT_S)
        with smtp:
            if security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if username:
                smtp.login(username, password or "")
            smtp.send_message(mail)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"SMTP failed: {type(exc).__name__}") from exc


def build_message(
    cfg: SmtpConfig, to: str, subject: str, text: str, html: str | None = None
) -> EmailMessage:
    mail = EmailMessage()
    mail["Subject"] = subject
    mail["From"] = from_header(cfg)
    mail["To"] = to
    mail.set_content(text)
    if html:
        mail.add_alternative(html, subtype="html")
    return mail


def deliver(cfg: SmtpConfig, to: str, subject: str, text: str, html: str | None = None) -> None:
    """Synchronous send via ``cfg``; raises ``MailError``."""
    if not cfg.configured:
        raise MailError("SMTP is not configured")
    smtp_deliver(
        build_message(cfg, to, subject, text, html),
        host=cfg.host,
        port=cfg.port,
        security=cfg.security,
        username=cfg.username,
        password=cfg.password.get_secret_value() if cfg.password else None,
    )


def send(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Enqueue an email (fire-and-forget; the worker retries 3x with backoff)."""
    from app.tasks.mail import send_mail

    send_mail.delay(to, subject, text, html)
