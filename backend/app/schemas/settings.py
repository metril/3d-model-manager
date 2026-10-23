"""Schemas for the storage settings API (SPEC "Storage layer"; Task 6
brief). ``StorageConfigIn``/``StorageConfigOut`` wrap the per-backend
pydantic models in ``app.storage.config`` behind the flat ``{backend,
config}`` shape Task 7's UI consumes -- the raw ``config`` dict is validated
via ``parse_storage_config`` on the way in (``app.api.settings``) and
produced via ``redacted()`` on the way out.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from app.schemas.imports import NonEmptyStr
from app.services.app_config import AppConfig

# Bambu account region (global = api.bambulab.com, china = api.bambulab.cn) --
# constrained at the schema boundary so a bogus region 422s here rather than
# silently defaulting inside bambu_auth (matches the frontend's own union).
BambuRegion = Literal["global", "china"]

if TYPE_CHECKING:
    from app.config import Settings
    from app.models import ApiToken, StorageBackendRow
    from app.storage.config import StorageConfig


class StorageConfigIn(BaseModel):
    backend: str
    config: dict = {}


class StorageConfigOut(BaseModel):
    backend: str
    config: dict

    @classmethod
    def from_config(cls, config: StorageConfig) -> StorageConfigOut:
        from app.storage.config import redacted

        return cls(backend=config.backend, config=redacted(config))


class ConnectionTestOut(BaseModel):
    ok: bool
    detail: str
    latency_ms: int


# ---------------------------------------------------------------------------
# Runtime feature settings (Round 10 "Settings" UI; see
# app.services.app_config). Unlike the storage/import-token/Bambu/Printables
# sections above, nothing here is secret -- no redaction, no merge-on-blank:
# `PUT` is a full replace of all five fields, each independently validated.
# ---------------------------------------------------------------------------


class AppSettingsIn(BaseModel):
    printer_enabled: bool
    scan_interval_s: int = Field(ge=0)
    collection_sync_interval_s: int = Field(ge=0)
    watch_interval_s: int = Field(ge=0)
    watch_stable_s: float = Field(ge=0)
    filament_cost_per_kg: float = Field(default=20.0, ge=0)
    machine_cost_per_hour: float = Field(default=0.0, ge=0)
    energy_cost_per_kwh: float = Field(default=0.0, ge=0)
    sensor_history_days: int = Field(default=30, ge=1, le=3650)


class AppSettingsOut(BaseModel):
    printer_enabled: bool
    scan_interval_s: int
    collection_sync_interval_s: int
    watch_interval_s: int
    watch_stable_s: float
    filament_cost_per_kg: float
    machine_cost_per_hour: float
    energy_cost_per_kwh: float
    sensor_history_days: int

    @classmethod
    def from_config(cls, config: AppConfig) -> AppSettingsOut:
        return cls(**config.model_dump())


# ---------------------------------------------------------------------------
# Multi-backend storage CRUD (Workstream C task C3; see
# app.services.storage_backends and app.models.storage.StorageBackendRow).
# Unlike `StorageConfigIn`/`StorageConfigOut` above (the legacy single-
# backend shim's flat `{backend, config}` shape), `config` here carries its
# own `backend` discriminator field INSIDE it (the same shape
# `parse_storage_config`/`StorageBackendRow.config` already use), since these
# endpoints operate on a specific backend ROW rather than "the" active
# config.
# ---------------------------------------------------------------------------


class StorageBackendCreateIn(BaseModel):
    name: NonEmptyStr
    config: dict


class StorageBackendUpdateIn(BaseModel):
    """All fields optional; only the ones present are applied. A `config`
    with no stored secret to merge against (see `_merge_backend_secrets` in
    `app.api.settings`) 422s on a blank/`"***"` secret same as the legacy
    `PUT /settings/storage` path.
    """

    name: NonEmptyStr | None = None
    config: dict | None = None


class StorageBackendOut(BaseModel):
    id: int
    name: str
    scheme: str
    is_default: bool
    config: dict  # secret fields masked -- see `app.storage.config.redacted`
    created_at: datetime

    @classmethod
    def from_row(cls, row: StorageBackendRow, settings: Settings) -> StorageBackendOut:
        from app.services.storage_config import decrypt_config_row
        from app.storage.config import parse_storage_config, redacted

        data, _ = decrypt_config_row(settings, dict(row.config))
        parsed = parse_storage_config(data)
        return cls(
            id=row.id,
            name=row.name,
            scheme=row.scheme,
            is_default=row.is_default,
            config=redacted(parsed),
            created_at=row.created_at,
        )


# Bambu Lab account connect flow (Workstream B task B2; SPEC full-design
# line 230 "Connect Bambu account" flow) -- see app.services.bambu_auth for
# the login/MFA/refresh contract and app.api.settings for the endpoints.
# NEITHER schema ever carries a token: login/verify accept only what the
# operator/browser types (account/password/code) or echoes back
# (mfa_context, an opaque continuation payload with no secret value of its
# own); the outputs below never include accessToken/refreshToken.


class BambuLoginIn(BaseModel):
    account: NonEmptyStr
    password: NonEmptyStr
    region: BambuRegion = "global"


class BambuVerifyIn(BaseModel):
    account: NonEmptyStr
    code: NonEmptyStr
    region: BambuRegion = "global"
    mfa_context: dict = {}


class BambuLoginOut(BaseModel):
    status: str  # "connected" | "mfa_required" -- never a token field
    account: str | None = None
    region: str | None = None
    mfa_context: dict | None = None


class BambuStatusOut(BaseModel):
    connected: bool
    account: str | None = None
    region: str = "global"
    # True iff connected AND the stored refresh token's last refresh attempt
    # failed (``bambu_auth.BambuAuthState.refresh_failed_at`` set) -- the
    # expiry-banner UX signal so the frontend can tell "not configured" (task:
    # import-health) apart from "configured but the session died".
    needs_reconnect: bool = False


# Printables account connect flow (Workstream A task A1; M9 saved-collections
# follow-on) -- see app.services.printables_auth for the refresh/rotation
# contract and app.api.settings for the endpoints. NEITHER schema below ever
# carries a token: the input is only what the operator pastes (the
# `auth.refresh_token` cookie value, never echoed back), and the output never
# includes the access or refresh token.


class PrintablesConnectIn(BaseModel):
    refresh_token: NonEmptyStr


class PrintablesStatusOut(BaseModel):
    connected: bool
    username: str | None = None
    user_id: str | None = None


# ---------------------------------------------------------------------------
# Browser-extension API tokens (M10 Workstream A; see app.services.api_tokens
# and app.api.ext). Session-gated management of the SEPARATE bearer-token
# auth plane the extension uses. ``ApiTokenMintOut`` is the ONE place the
# plaintext token is ever present in a response -- ``ApiTokenOut`` (the list
# shape) never carries the token or its hash, same masking discipline as the
# Bambu/Printables status-outs above.
# ---------------------------------------------------------------------------


class ApiTokenCreateIn(BaseModel):
    label: NonEmptyStr


class ApiTokenMintOut(BaseModel):
    id: int
    label: str
    token: str  # shown exactly once, at mint time -- never returned again
    created_at: datetime


class ApiTokenOut(BaseModel):
    id: int
    label: str
    created_at: datetime
    last_used_at: datetime | None = None
    user_id: int
    username: str

    @classmethod
    def from_model(cls, row: ApiToken, username: str) -> ApiTokenOut:
        return cls(
            id=row.id,
            label=row.label,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            user_id=row.user_id,
            username=username,
        )
