"""Generic MQTT relay connection config: one ``settings`` row under
``"mqtt_relay"``. The broker password is Fernet-encrypted at rest (same
write-only ``"***"`` sentinel convention as ``ha_config``). ``MqttRelayConfig``
is the in-process plaintext view and masks the password in ``repr``."""

from __future__ import annotations

import re
from dataclasses import dataclass

from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models import Setting

SETTINGS_KEY = "mqtt_relay"
STATUS_KEY = "relay:status"
STATUS_TTL_S = 30
KEEP_SENTINEL = "***"
COMMANDS = ("pause", "resume", "stop")
DEFAULT_PREFIX = "tdmm"
_PREFIX_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
_PLAIN = (
    "enabled",
    "host",
    "port",
    "tls",
    "tls_verify",
    "username",
    "topic_prefix",
    "commands_enabled",
    "actor_user_id",
)


def valid_prefix(prefix: str) -> bool:
    return bool(_PREFIX_RE.match(prefix))


@dataclass(frozen=True)
class MqttRelayConfig:
    enabled: bool = False
    host: str | None = None
    port: int = 1883
    tls: bool = False
    tls_verify: bool = True
    username: str | None = None
    password: str | None = None
    topic_prefix: str = DEFAULT_PREFIX
    commands_enabled: bool = False
    actor_user_id: int | None = None

    def __repr__(self) -> str:
        return (
            f"MqttRelayConfig(enabled={self.enabled!r}, host={self.host!r}, port={self.port!r}, "
            f"tls={self.tls!r}, username={self.username!r}, "
            f"password={'***' if self.password else None!r}, topic_prefix={self.topic_prefix!r}, "
            f"commands_enabled={self.commands_enabled!r}, actor_user_id={self.actor_user_id!r})"
        )

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.host)

    def signature(self) -> tuple:
        return (
            self.host,
            self.port,
            self.tls,
            self.tls_verify,
            self.username,
            self.password,
            self.topic_prefix,
            self.commands_enabled,
        )


def _to_config(settings: Settings, value: dict | None) -> MqttRelayConfig:
    v = value or {}
    pw = None
    if v.get("password_enc"):
        try:
            pw = decrypt_secret(settings, v["password_enc"])
        except InvalidToken:
            pw = v["password_enc"]
    return MqttRelayConfig(
        enabled=bool(v.get("enabled", False)),
        host=v.get("host") or None,
        port=int(v.get("port") or 1883),
        tls=bool(v.get("tls", False)),
        tls_verify=bool(v.get("tls_verify", True)),
        username=v.get("username") or None,
        password=pw,
        topic_prefix=v.get("topic_prefix") or DEFAULT_PREFIX,
        commands_enabled=bool(v.get("commands_enabled", False)),
        actor_user_id=v.get("actor_user_id"),
    )


async def get_relay_config(db: AsyncSession, settings: Settings) -> MqttRelayConfig:
    row = await db.get(Setting, SETTINGS_KEY)
    return _to_config(settings, row.value if row else None)


def get_relay_config_sync(session: Session, settings: Settings) -> MqttRelayConfig:
    row = session.get(Setting, SETTINGS_KEY)
    return _to_config(settings, row.value if row else None)


async def set_relay_config(db: AsyncSession, settings: Settings, patch: dict) -> MqttRelayConfig:
    row = await db.get(Setting, SETTINGS_KEY)
    value: dict = dict(row.value) if row is not None else {}
    for field in _PLAIN:
        if field in patch:
            value[field] = patch[field]
    pw = patch.get("password", KEEP_SENTINEL)
    if pw is not None and pw != KEEP_SENTINEL:
        value["password_enc"] = encrypt_secret(settings, pw) if pw else None
    if row is None:
        db.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    return _to_config(settings, value)
