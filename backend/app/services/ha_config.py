"""Home Assistant connection config (P8 plan): a single ``settings`` row
under key ``"home_assistant"`` -- ``{ha_mqtt_url, ha_mqtt_user,
ha_mqtt_password_enc, ha_discovery_prefix, ha_url, ha_token_enc}``. No env
backing (DB only, set via the Settings UI card). The two secrets
(``ha_mqtt_password``/``ha_token``) are Fernet-encrypted at rest via the
same ``app.crypto`` seam as the printer access code and storage/import-
token secrets, write-only with the same sentinel convention: an omitted
field or the literal ``"***"`` keeps the stored value, ``""`` clears it,
anything else (re)encrypts. A pre-M6-style plaintext row (e.g. seeded
directly in a test, or left behind by a future bug) still reads correctly
via the per-field ``InvalidToken`` fallback -- ``app.services.
secrets_at_rest`` eagerly re-encrypts it at startup.

``HaConfig`` is the plaintext, in-process view handed to the MQTT bridge
and the REST poller -- never serialized back to a client; its ``__repr__``
masks both secrets so a stray log/debug print never leaks them (the P2
access-code rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models import Setting

SETTINGS_KEY = "home_assistant"

# Sentinel the API layer echoes back for an unchanged secret (same
# convention as storage/import-token secrets) -- `set_ha_config` treats an
# omitted key, `None`, or this literal all as "keep the stored value".
_KEEP_SENTINEL = "***"

_SECRET_FIELDS = (
    ("ha_mqtt_password", "ha_mqtt_password_enc"),
    ("ha_token", "ha_token_enc"),
)
_PLAIN_FIELDS = ("ha_mqtt_url", "ha_mqtt_user", "ha_discovery_prefix", "ha_url")


@dataclass(frozen=True)
class HaConfig:
    """Plaintext HA connection config, in-process only."""

    ha_mqtt_url: str | None
    ha_mqtt_user: str | None
    ha_mqtt_password: str | None
    ha_url: str | None
    ha_token: str | None
    ha_discovery_prefix: str = "homeassistant"

    def __repr__(self) -> str:
        def mask(value: str | None) -> str | None:
            return "***" if value else value

        return (
            f"HaConfig(ha_mqtt_url={self.ha_mqtt_url!r}, ha_mqtt_user={self.ha_mqtt_user!r}, "
            f"ha_mqtt_password={mask(self.ha_mqtt_password)!r}, "
            f"ha_url={self.ha_url!r}, ha_token={mask(self.ha_token)!r}, "
            f"ha_discovery_prefix={self.ha_discovery_prefix!r})"
        )

    def mqtt_signature(self) -> tuple[str | None, str | None, str | None, str]:
        """What the bridge diffs to decide whether it needs to reconnect --
        the broker URL/credentials and the discovery prefix (a prefix
        change invalidates previously-published retained discovery)."""
        return (
            self.ha_mqtt_url,
            self.ha_mqtt_user,
            self.ha_mqtt_password,
            self.ha_discovery_prefix,
        )


def parse_mqtt_url(url: str) -> tuple[str, int, bool]:
    """Parse ``mqtt://host[:1883]`` or ``mqtts://host[:8883]`` (TLS, system
    CA) into ``(host, port, tls)``. Any other scheme, a missing host, or a
    URL carrying userinfo (``user:pass@host``, which this scheme doesn't
    support -- auth is separate ``ha_mqtt_user``/``ha_mqtt_password``
    fields) raises ``ValueError``.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("mqtt", "mqtts"):
        raise ValueError(f"unsupported mqtt URL scheme: {parsed.scheme!r}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("mqtt URL must not contain userinfo")
    if not parsed.hostname:
        raise ValueError("mqtt URL must contain a host")
    tls = parsed.scheme == "mqtts"
    port = parsed.port if parsed.port is not None else (8883 if tls else 1883)
    return parsed.hostname, port, tls


def _decrypt_field(settings: Settings, value: dict, enc_key: str) -> str | None:
    raw = value.get(enc_key)
    if not raw:
        return None
    try:
        return decrypt_secret(settings, raw)
    except InvalidToken:
        return raw  # legacy/test plaintext


def _to_config(settings: Settings, value: dict | None) -> HaConfig:
    value = value or {}
    return HaConfig(
        ha_mqtt_url=value.get("ha_mqtt_url"),
        ha_mqtt_user=value.get("ha_mqtt_user"),
        ha_mqtt_password=_decrypt_field(settings, value, "ha_mqtt_password_enc"),
        ha_url=value.get("ha_url"),
        ha_token=_decrypt_field(settings, value, "ha_token_enc"),
        ha_discovery_prefix=value.get("ha_discovery_prefix") or "homeassistant",
    )


async def get_ha_config(db: AsyncSession, settings: Settings) -> HaConfig:
    row = await db.get(Setting, SETTINGS_KEY)
    return _to_config(settings, row.value if row else None)


def get_ha_config_sync(session: Session, settings: Settings) -> HaConfig:
    row = session.get(Setting, SETTINGS_KEY)
    return _to_config(settings, row.value if row else None)


async def set_ha_config(db: AsyncSession, settings: Settings, patch: dict) -> HaConfig:
    """Sentinel-merge ``patch`` onto the stored row: a plain field present
    in ``patch`` replaces the stored value (absent -> keep); a secret field
    is kept on an absent key, ``None``, or ``"***"``, cleared on ``""``, and
    (re)encrypted on anything else. Always commits."""
    row = await db.get(Setting, SETTINGS_KEY)
    value: dict = dict(row.value) if row is not None else {}

    for field in _PLAIN_FIELDS:
        if field in patch:
            value[field] = patch[field]

    for field, enc_key in _SECRET_FIELDS:
        new_value = patch.get(field, _KEEP_SENTINEL)
        if new_value is None or new_value == _KEEP_SENTINEL:
            continue  # keep whatever's already stored
        if new_value == "":
            value[enc_key] = None
        else:
            value[enc_key] = encrypt_secret(settings, new_value)

    if row is None:
        db.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        # A FRESH dict, never an in-place mutation -- mirrors
        # app.services.app_config/storage_config's assignment so the ORM's
        # JSONB change-tracking actually sees the write.
        row.value = value
    await db.commit()
    return _to_config(settings, value)
