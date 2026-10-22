"""Idempotent, EAGER re-encryption of any legacy plaintext secret at rest
(M6 A1). Called once from the API lifespan on startup -- NOT lazily on the
next settings-save. The api/worker/printerd containers share the same DB and
the same Fernet key (tdmm_data:/data), so upgrading once from the api process
encrypts the shared rows for every process."""

from __future__ import annotations

from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models import Setting
from app.services import storage_config
from app.storage.config import parse_storage_config


async def reencrypt_secrets_at_rest(db: AsyncSession, settings: Settings) -> None:
    changed = False
    storage_row = await db.get(Setting, "storage")
    if storage_row is not None:
        data, was_plaintext = storage_config.decrypt_config_row(settings, dict(storage_row.value))
        if was_plaintext:
            storage_row.value = storage_config.encrypt_config_secret(
                settings, parse_storage_config(data)
            )
            changed = True
    token_row = await db.get(Setting, "import_tokens")
    if token_row is not None:
        updates: dict = {}
        for field in ("thingiverse_token", "makerworld_token"):
            token = (token_row.value or {}).get(field)
            if not token:
                continue
            try:
                decrypt_secret(settings, token)  # already ciphertext -> no-op
            except InvalidToken:
                updates[field] = encrypt_secret(settings, token)
        if updates:
            # Preserve every key this pass doesn't manage (e.g. a future
            # field) as well as the sibling token field this pass leaves
            # untouched -- never collapse the row down to just what changed.
            token_row.value = {**token_row.value, **updates}
            changed = True
    # P8 Home Assistant plan: ha_mqtt_password_enc/ha_token_enc under the
    # "home_assistant" row -- same InvalidToken-fallback re-encrypt as the
    # import tokens above.
    ha_row = await db.get(Setting, "home_assistant")
    if ha_row is not None:
        updates = {}
        for field in ("ha_mqtt_password_enc", "ha_token_enc"):
            secret = (ha_row.value or {}).get(field)
            if not secret:
                continue
            try:
                decrypt_secret(settings, secret)  # already ciphertext -> no-op
            except InvalidToken:
                updates[field] = encrypt_secret(settings, secret)
        if updates:
            ha_row.value = {**ha_row.value, **updates}
            changed = True
    if changed:
        await db.commit()
