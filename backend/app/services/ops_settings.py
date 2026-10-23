"""Q7 ops settings: one ``settings`` row under key ``"ops"`` (backup schedule,
git push target, retention). The git token is Fernet-encrypted at rest and
write-only (omitted / ``None`` / ``"***"`` keeps, ``""`` clears). A second row,
``"update_cache"``, holds the update checker's last result."""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models import Setting

OPS_KEY = "ops"
UPDATE_CACHE_KEY = "update_cache"
KEEP = "***"
GIT_PROVIDERS = ("github", "gitlab", "gitea", "forgejo")


@dataclass(frozen=True)
class OpsConfig:
    backup_enabled: bool = False
    backup_interval_hours: int = 24
    backup_retention: int = 7
    git_provider: str | None = None
    git_base_url: str | None = None  # self-hosted gitlab/gitea/forgejo
    git_repo: str | None = None  # "owner/name"
    git_branch: str = "main"
    git_token: str | None = None
    update_check_enabled: bool = True

    def __repr__(self) -> str:
        return f"OpsConfig(git_provider={self.git_provider!r}, git_repo={self.git_repo!r}, ...)"

    @property
    def git_active(self) -> bool:
        return bool(self.git_provider and self.git_repo and self.git_token)


_PLAIN = (
    "backup_enabled",
    "backup_interval_hours",
    "backup_retention",
    "git_provider",
    "git_base_url",
    "git_repo",
    "git_branch",
    "update_check_enabled",
)


def _to_config(settings: Settings, value: dict | None) -> OpsConfig:
    v = value or {}
    token = None
    raw = v.get("git_token_enc")
    if raw:
        try:
            token = decrypt_secret(settings, raw)
        except InvalidToken:
            token = raw
    kw = {k: v[k] for k in _PLAIN if k in v and v[k] is not None}
    return OpsConfig(git_token=token, **kw)


async def get_ops_config(db: AsyncSession, settings: Settings) -> OpsConfig:
    row = await db.get(Setting, OPS_KEY)
    return _to_config(settings, row.value if row else None)


def get_ops_config_sync(session: Session, settings: Settings) -> OpsConfig:
    row = session.get(Setting, OPS_KEY)
    return _to_config(settings, row.value if row else None)


async def set_ops_config(db: AsyncSession, settings: Settings, patch: dict) -> OpsConfig:
    row = await db.get(Setting, OPS_KEY)
    value: dict = dict(row.value) if row is not None else {}
    for field in _PLAIN:
        if field in patch:
            value[field] = patch[field]
    token = patch.get("git_token", KEEP)
    if token is not None and token != KEEP:
        value["git_token_enc"] = encrypt_secret(settings, token) if token else None
    if row is None:
        db.add(Setting(key=OPS_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    return _to_config(settings, value)


async def get_update_cache(db: AsyncSession) -> dict | None:
    row = await db.get(Setting, UPDATE_CACHE_KEY)
    return row.value if row else None


def set_update_cache_sync(session: Session, value: dict) -> None:
    row = session.get(Setting, UPDATE_CACHE_KEY)
    if row is None:
        session.add(Setting(key=UPDATE_CACHE_KEY, value=value))
    else:
        row.value = value
    session.commit()
