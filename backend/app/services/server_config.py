"""DB-backed server (infra) settings: the ``server`` row in ``settings``.

Separate from ``AppConfig`` (``settings:write`` feature knobs): these are
admin-only infra knobs -- public URL, ingest caps, SSE heartbeat, log level,
slicer sidecar, virtual-printer network -- with their own seed/meta rules.

Resolution is literal default -> JSONB overlay. The environment is NEVER
read at resolve time; it is read only by the seed (``seed_server_config``),
which runs once per process start in every long-lived process and, thanks to
``INSERT ... ON CONFLICT DO NOTHING``, is idempotent. The row stores
``_seeded_from_env`` (field names the seed took from the environment) so the
UI can show "Seeded from environment (NAME)"; a PUT prunes edited fields.

The moved env vars are deliberately NOT fields on ``app.config.Settings``:
they are parsed here, straight from ``os.environ``, so a malformed value
degrades to that field's default with a WARNING instead of crashing every
process at ``get_settings()``.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models import Setting

if TYPE_CHECKING:
    from fastapi import Request

log = logging.getLogger(__name__)

SETTINGS_KEY = "server"
SEEDED_META = "_seeded_from_env"
APP_KEY = "app"
MIB = 1024**2

DEFAULT_MAX_UPLOAD_BYTES = 4 * 1024**3
DEFAULT_MAX_DOWNLOAD_BYTES = 2 * 1024**3

# field -> env var name, consulted ONLY by the seed.
ENV_SEED: dict[str, str] = {
    "public_url": "TDMM_PUBLIC_URL",
    "max_upload_bytes": "MAX_UPLOAD_BYTES",
    "max_download_bytes": "MAX_DOWNLOAD_BYTES",
    "sse_heartbeat_s": "SSE_HEARTBEAT_INTERVAL",
    "slicer_url": "TDMM_SLICER_URL",
    "slice_timeout_s": "TDMM_SLICE_TIMEOUT_S",
    "orcaslicer_enabled": "TDMM_ORCASLICER_ENABLED",
    "orcaslicer_url": "TDMM_ORCASLICER_URL",
    "orcaslicer_launcher_url": "TDMM_ORCASLICER_LAUNCHER_URL",
    "orcaslicer_launcher_token": "TDMM_ORCASLICER_LAUNCHER_TOKEN",
    "vp_bind": "TDMM_VP_BIND",
    "vp_port_ssdp": "TDMM_VP_PORT_SSDP",
    "vp_port_mqtt": "TDMM_VP_PORT_MQTT",
    "vp_port_ftp": "TDMM_VP_PORT_FTP",
    "vp_pasv_min": "TDMM_VP_PASV_MIN",
    "vp_pasv_max": "TDMM_VP_PASV_MAX",
    "vp_pasv_address": "TDMM_VP_PASV_ADDRESS",
    "smtp.host": "TDMM_SMTP_HOST",
    "smtp.port": "TDMM_SMTP_PORT",
    "smtp.security": "TDMM_SMTP_SECURITY",
    "smtp.username": "TDMM_SMTP_USERNAME",
    "smtp.password": "TDMM_SMTP_PASSWORD",
    "smtp.from_addr": "TDMM_SMTP_FROM",
}
SECRET_MASK = "***"  # noqa: S105
# Env vars that are removed/moved but may still be set; warned about too.
EXTRA_IGNORED_ENV = ("COOKIE_SECURE", "TDMM_VP_ENABLED", "TDMM_OBICO_URL", "TDMM_OBICO_CALLBACK")

# Fields participating in the cross-field (ports / passive range) check.
_VP_NET_FIELDS = (
    "vp_port_ssdp",
    "vp_port_mqtt",
    "vp_port_ftp",
    "vp_pasv_min",
    "vp_pasv_max",
)


class SmtpConfig(BaseModel):
    """Server-wide outgoing mail. ``password`` is encrypted in the row and
    serializes as ``***`` to the API (``mode="json"``); python-mode dumps
    (used by the row writer, which encrypts) carry the plaintext."""

    host: str = ""
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["none", "starttls", "tls"] = "starttls"
    username: str = ""
    password: SecretStr | None = None
    from_addr: str = ""
    from_name: str = ""

    @field_validator("host", "username", "from_addr", "from_name", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("must be a string")
        return v.strip()

    @field_validator("password", mode="before")
    @classmethod
    def _blank_password(cls, v: Any) -> Any:
        return None if v == "" else v

    @field_serializer("password", when_used="always")
    def _ser_password(self, v: SecretStr | None, info: Any) -> str | None:
        if v is None:
            return None
        return SECRET_MASK if info.mode == "json" else v.get_secret_value()

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_addr)


class ServerConfig(BaseModel):
    smtp: SmtpConfig = Field(default_factory=SmtpConfig)
    public_url: str | None = None
    max_upload_bytes: int = Field(default=DEFAULT_MAX_UPLOAD_BYTES, ge=MIB)
    max_download_bytes: int = Field(default=DEFAULT_MAX_DOWNLOAD_BYTES, ge=MIB)
    sse_heartbeat_s: int = Field(default=15, ge=5, le=120)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    slicer_enabled: bool = True
    slicer_url: str = "http://slicer:3001"
    slice_timeout_s: int = Field(default=600, ge=30, le=7200)
    orcaslicer_enabled: bool = False
    orcaslicer_url: str = "http://orcaslicer:3000"
    orcaslicer_launcher_url: str = "http://orcaslicer:3010"
    orcaslicer_launcher_token: SecretStr | None = None
    vp_bind: str = "0.0.0.0"  # noqa: S104
    vp_port_ssdp: int = Field(default=2021, ge=1, le=65535)
    vp_port_mqtt: int = Field(default=8883, ge=1, le=65535)
    vp_port_ftp: int = Field(default=990, ge=1, le=65535)
    vp_pasv_min: int = Field(default=50000, ge=1, le=65535)
    vp_pasv_max: int = Field(default=50009, ge=1, le=65535)
    vp_pasv_address: str | None = None

    @field_validator("public_url", mode="before")
    @classmethod
    def _public_url(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("must be a string")
        v = v.strip()
        if not v:
            return None
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("must be an http(s) URL, e.g. https://models.example.com")
        if parts.query or parts.fragment:
            raise ValueError("must not contain a query string or fragment")
        return v.rstrip("/")

    @field_validator("slicer_url", "orcaslicer_url", "orcaslicer_launcher_url", mode="before")
    @classmethod
    def _slicer_url(cls, v: Any) -> Any:
        if not isinstance(v, str):
            raise ValueError("must be a string")
        v = v.strip().rstrip("/")
        if v:
            parts = urlsplit(v)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("must be an http(s) URL")
        return v

    @field_validator("orcaslicer_launcher_token", mode="before")
    @classmethod
    def _blank_token(cls, v: Any) -> Any:
        return None if v == "" else v

    @field_serializer("orcaslicer_launcher_token", when_used="always")
    def _ser_token(self, v: SecretStr | None, info: Any) -> str | None:
        if v is None:
            return None
        return SECRET_MASK if info.mode == "json" else v.get_secret_value()

    @field_validator("vp_bind", mode="before")
    @classmethod
    def _bind(cls, v: Any) -> Any:
        if not isinstance(v, str):
            raise ValueError("must be a string")
        v = v.strip()
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError("must be an IP address, e.g. 0.0.0.0") from None
        return v

    @field_validator("vp_pasv_address", mode="before")
    @classmethod
    def _pasv_address(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("must be a string")
        v = v.strip()
        if not v:
            return None
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError("must be an IP address") from None
        return v

    @model_validator(mode="after")
    def _cross(self, info: ValidationInfo) -> ServerConfig:
        if info.context and info.context.get("skip_cross"):
            return self
        problem = vp_network_problem(self.model_dump())
        if problem:
            raise ValueError(problem)
        return self


def vp_network_problem(d: dict[str, Any]) -> str | None:
    """Cross-field VP network rules; ``None`` when consistent."""
    fixed = (d["vp_port_ssdp"], d["vp_port_mqtt"], d["vp_port_ftp"])
    if len(set(fixed)) != 3:
        return "SSDP, MQTT and FTP ports must be distinct"
    if d["vp_pasv_min"] > d["vp_pasv_max"]:
        return "passive port range: min must be <= max"
    if any(d["vp_pasv_min"] <= p <= d["vp_pasv_max"] for p in fixed):
        return "passive port range must not include the SSDP/MQTT/FTP ports"
    return None


_FIELDS = tuple(ServerConfig.model_fields)


def _defaults() -> dict[str, Any]:
    return ServerConfig().model_dump()


def _decode_smtp(raw: Any) -> Any:
    """Row -> model input: decrypt the stored SMTP password (an undecryptable
    value degrades to unset, with a warning, rather than failing the read)."""
    if not isinstance(raw, dict):
        return {}
    raw = dict(raw)
    token = raw.get("password")
    if token:
        try:
            raw["password"] = decrypt_secret(get_settings(), token)
        except (InvalidToken, ValueError):
            log.warning("server settings: stored smtp.password is undecryptable; ignoring")
            raw["password"] = None
    return raw


def _decode_token(token: Any) -> Any:
    """Row -> model input for ``orcaslicer_launcher_token`` (undecryptable -> unset)."""
    if not token:
        return None
    try:
        return decrypt_secret(get_settings(), token)
    except (InvalidToken, ValueError):
        log.warning("server settings: stored orcaslicer_launcher_token is undecryptable; ignoring")
        return None


def _encode(data: dict[str, Any]) -> dict[str, Any]:
    """Python-mode dump -> row: encrypt the SMTP password."""
    out = dict(data)
    smtp = dict(out.get("smtp") or {})
    if smtp.get("password"):
        smtp["password"] = encrypt_secret(get_settings(), smtp["password"])
    out["smtp"] = smtp
    if out.get("orcaslicer_launcher_token"):
        out["orcaslicer_launcher_token"] = encrypt_secret(
            get_settings(), out["orcaslicer_launcher_token"]
        )
    return out


def flat(cfg: ServerConfig) -> dict[str, Any]:
    """Top-level fields with ``smtp`` flattened to ``smtp.<field>`` keys (the
    granularity of ``ENV_SEED`` / ``_seeded_from_env``)."""
    out: dict[str, Any] = {}
    for f in _FIELDS:
        v = getattr(cfg, f)
        if isinstance(v, SmtpConfig):
            for sf in SmtpConfig.model_fields:
                sv = getattr(v, sf)
                out[f"smtp.{sf}"] = sv.get_secret_value() if isinstance(sv, SecretStr) else sv
        else:
            out[f] = v.get_secret_value() if isinstance(v, SecretStr) else v
    return out


def _resolve(value: dict | None) -> ServerConfig:
    """Defaults overlaid with the row. A stored value that no longer
    validates (hand-edited row) degrades field-by-field to its default
    rather than failing the caller."""
    data = _defaults()
    for field in _FIELDS:
        if value and field in value:
            data[field] = value[field]
    data["smtp"] = _decode_smtp(data["smtp"])
    data["orcaslicer_launcher_token"] = _decode_token(data.get("orcaslicer_launcher_token"))
    try:
        return ServerConfig.model_validate(data)
    except ValidationError as exc:
        defaults = _defaults()
        for err in exc.errors():
            loc = err["loc"][0] if err["loc"] else None
            bad = [loc] if loc in defaults else list(_VP_NET_FIELDS)
            for f in bad:
                log.warning("server settings: invalid stored %s; using default", f)
                data[f] = defaults[f]
        return ServerConfig.model_validate(data, context={"skip_cross": True})


async def get_server_config(db: AsyncSession, settings: Settings | None = None) -> ServerConfig:
    row = await db.get(Setting, SETTINGS_KEY)
    return _resolve(row.value if row else None)


def get_server_config_sync(session: Session, settings: Settings | None = None) -> ServerConfig:
    row = session.get(Setting, SETTINGS_KEY)
    return _resolve(row.value if row else None)


def current_sync(settings: Settings | None = None) -> ServerConfig:
    """One-shot read in its own short sync session (for call sites without one)."""
    from app.tasks import base

    with base.sync_session() as session:
        return get_server_config_sync(session, settings)


def _meta(value: dict | None) -> list[str]:
    raw = (value or {}).get(SEEDED_META) or []
    return [f for f in raw if f in ENV_SEED]


async def seeded_fields(db: AsyncSession) -> list[str]:
    row = await db.get(Setting, SETTINGS_KEY)
    return _meta(row.value if row else None)


def _row_value(cfg: ServerConfig, seeded: list[str]) -> dict:
    return {**_encode(cfg.model_dump()), SEEDED_META: seeded}


async def set_server_config(
    db: AsyncSession, cfg: ServerConfig, *, commit: bool = True
) -> ServerConfig:
    """Persist ``cfg`` (full replace). Fields whose value changed leave
    ``_seeded_from_env``. Returns the resolved config."""
    row = await db.get(Setting, SETTINGS_KEY)
    old = _resolve(row.value if row else None)
    seeded = _meta(row.value if row else None)
    fo, fn = flat(old), flat(cfg)
    changed = {f for f in fn if fo[f] != fn[f]}
    value = _row_value(cfg, [f for f in seeded if f not in changed])
    if row is None:
        db.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value  # fresh dict: JSONB change tracking
    if commit:
        await db.commit()
    return _resolve(value)


# --- seed ---------------------------------------------------------------


def _with_field(data: dict, field: str, raw: Any) -> dict:
    if "." not in field:
        return {**data, field: raw}
    group, sub = field.split(".", 1)
    return {**data, group: {**(data.get(group) or {}), sub: raw}}


def _merge_field(data: dict, cand: ServerConfig, field: str) -> dict:
    if "." not in field:
        v = getattr(cand, field)
        return {**data, field: v.get_secret_value() if isinstance(v, SecretStr) else v}
    group, sub = field.split(".", 1)
    sv = getattr(getattr(cand, group), sub)
    if isinstance(sv, SecretStr):
        sv = sv.get_secret_value()
    return {**data, group: {**(data.get(group) or {}), sub: sv}}


def _build_seed(environ: Any, legacy_app: dict | None) -> tuple[dict, list[str]]:
    """Env (explicitly set, non-empty) -> legacy ``app`` row -> default, per
    field. An invalid env value warns and falls back for that field only."""
    data = _defaults()
    seeded: list[str] = []
    skip = {"skip_cross": True}
    for field, env_name in ENV_SEED.items():
        raw = environ.get(env_name)
        if raw is not None and raw.strip() != "":
            try:
                cand = ServerConfig.model_validate(_with_field(data, field, raw), context=skip)
            except ValidationError as exc:
                log.warning(
                    "%s=%r is invalid (%s); using the default for %s",
                    env_name,
                    "***" if field == "smtp.password" else raw,
                    exc.errors()[0]["msg"],
                    field,
                )
            else:
                data = _merge_field(data, cand, field)
                seeded.append(field)
                continue
        if field in ("slicer_url", "slice_timeout_s") and legacy_app:
            legacy = legacy_app.get(field)
            if legacy not in (None, ""):
                try:
                    cand = ServerConfig.model_validate({**data, field: legacy}, context=skip)
                except ValidationError:
                    continue
                data[field] = getattr(cand, field)
    problem = vp_network_problem(data)
    if problem:
        log.warning("virtual-printer env seed is inconsistent (%s); using default ports", problem)
        d = _defaults()
        for f in _VP_NET_FIELDS:
            data[f] = d[f]
        seeded = [f for f in seeded if f not in _VP_NET_FIELDS]
    return data, seeded


def _seed_insert(value: dict):
    return (
        pg_insert(Setting)
        .values(key=SETTINGS_KEY, value=value)
        .on_conflict_do_nothing(index_elements=["key"])
    )


async def seed_server_config(db: AsyncSession, settings: Settings | None = None) -> bool:
    """Insert the ``server`` row from env/legacy/defaults if absent.
    ``True`` when this call inserted it; an existing row is never touched."""
    if await db.get(Setting, SETTINGS_KEY) is not None:
        return False
    legacy = await db.get(Setting, APP_KEY)
    data, seeded = _build_seed(os.environ, legacy.value if legacy else None)
    result = await db.execute(_seed_insert({**_encode(data), SEEDED_META: seeded}))
    await db.commit()
    return bool(result.rowcount)


def seed_server_config_sync(session: Session, settings: Settings | None = None) -> bool:
    if session.get(Setting, SETTINGS_KEY) is not None:
        return False
    legacy = session.get(Setting, APP_KEY)
    data, seeded = _build_seed(os.environ, legacy.value if legacy else None)
    result = session.execute(_seed_insert({**_encode(data), SEEDED_META: seeded}))
    session.commit()
    return bool(result.rowcount)


def seed_all_sync(session: Session, settings: Settings | None = None) -> None:
    """Server + Obico seed for sync processes (worker/beat/printerd/VP)."""
    from app.services import obico

    seed_server_config_sync(session, settings)
    obico.seed_obico_config_sync(session)


def seed_all_sync_safe() -> None:
    """``seed_all_sync`` in its own session; a DB hiccup logs, never raises."""
    from app.tasks import base

    try:
        with base.sync_session() as session:
            seed_all_sync(session)
    except SQLAlchemyError:
        log.exception("server settings seed failed")


async def migrate_legacy_slicer(db: AsyncSession) -> bool:
    """One-shot: move leftover ``slicer_url``/``slice_timeout_s`` keys from
    the ``app`` row into ``server`` (only into fields still at their default
    and not seeded from env), then drop them from ``app``."""
    app_row = await db.get(Setting, APP_KEY)
    if app_row is None or not any(k in app_row.value for k in ("slicer_url", "slice_timeout_s")):
        return False
    legacy = dict(app_row.value)
    row = await db.get(Setting, SETTINGS_KEY)
    cfg = _resolve(row.value if row else None)
    seeded = _meta(row.value if row else None)
    defaults = _defaults()
    data = cfg.model_dump()
    for field in ("slicer_url", "slice_timeout_s"):
        val = legacy.get(field)
        if val in (None, "") or field in seeded or data[field] != defaults[field]:
            continue
        try:
            data[field] = getattr(ServerConfig.model_validate({**data, field: val}), field)
        except ValidationError:
            log.warning("legacy app.%s=%r is invalid; dropped", field, val)
    value = _row_value(ServerConfig.model_validate(data), seeded)
    if row is None:
        db.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    app_row.value = {k: v for k, v in legacy.items() if k not in ("slicer_url", "slice_timeout_s")}
    await db.commit()
    return True


# --- warnings / helpers ---------------------------------------------------


def ignored_env() -> list[str]:
    """Names of moved/removed env vars still set in the environment."""
    names = [*ENV_SEED.values(), *EXTRA_IGNORED_ENV]
    return [n for n in names if os.environ.get(n, "").strip() != ""]


def warn_ignored_server_env(
    settings: Settings | None = None, cfg: ServerConfig | None = None
) -> list[str]:
    names = ignored_env()
    for name in names:
        log.warning("%s is ignored after first boot; edit Settings -> Server", name)
    secure = os.environ.get("COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on")
    if secure and (cfg is None or not cfg.public_url):
        log.warning(
            "COOKIE_SECURE is no longer used: the Secure flag follows the request scheme or the "
            "Public URL. Set the Public URL in Settings -> Server."
        )
    cs = os.environ.get("COOKIE_SECURE")
    https = bool(cfg and cfg.public_url and cfg.public_url.startswith("https://"))
    if cs is not None and cs.strip() != "" and not secure and https:
        log.warning(
            "COOKIE_SECURE is falsy but the Public URL is https: cookies are now Secure and "
            "plain-http logins will fail. Set the Public URL to http or browse over https."
        )
    return names


def cookie_secure(cfg: ServerConfig, request: Request | None) -> bool:
    if cfg.public_url:
        return cfg.public_url.startswith("https://")
    return request is not None and request.url.scheme == "https"


def public_origin(cfg: ServerConfig, request: Request) -> str:
    return (cfg.public_url or str(request.base_url)).rstrip("/")


__all__ = [
    "ENV_SEED",
    "SECRET_MASK",
    "ServerConfig",
    "SmtpConfig",
    "cookie_secure",
    "current_sync",
    "flat",
    "get_server_config",
    "get_server_config_sync",
    "ignored_env",
    "migrate_legacy_slicer",
    "public_origin",
    "seed_server_config",
    "seed_server_config_sync",
    "seeded_fields",
    "set_server_config",
    "warn_ignored_server_env",
]
