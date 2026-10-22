"""Storage settings API (Task 6 brief; Global Constraints "New endpoint
verbs"): read/set the active storage backend config (secrets redacted on
read), a connection-test probe against a candidate config, and the
migration-helper job that copies the whole library tree onto a new backend
before cutting over.

``PUT`` is a *direct* set -- for pointing at an already-populated or empty
backend with no copy needed. The safe "copy the existing library across,
verify, then cut over" path is ``POST /migrate`` (``app.tasks.migrate``).
"""

from __future__ import annotations

import contextlib
import uuid
from typing import TYPE_CHECKING

import anyio
import pydantic
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm_on_write, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.importers.printables import fetch_identity
from app.schemas.imports import ImportTokensIn, ImportTokensOut
from app.schemas.jobs import JobOut
from app.schemas.settings import (
    ApiTokenCreateIn,
    ApiTokenMintOut,
    ApiTokenOut,
    AppSettingsIn,
    AppSettingsOut,
    BambuLoginIn,
    BambuLoginOut,
    BambuStatusOut,
    BambuVerifyIn,
    ConnectionTestOut,
    PrintablesConnectIn,
    PrintablesStatusOut,
    StorageBackendCreateIn,
    StorageBackendOut,
    StorageBackendUpdateIn,
    StorageConfigIn,
    StorageConfigOut,
)
from app.services import (
    api_tokens,
    app_config,
    bambu_auth,
    import_tokens,
    printables_auth,
    storage_config,
)
from app.services import jobs as jobs_service
from app.services import storage_backends as storage_backends_service
from app.services.storage_probe import probe_backend
from app.storage.config import SECRET_FIELD_BY_BACKEND, StorageConfig, parse_storage_config
from app.storage.registry import get_backend
from app.tasks.migrate import migrate_storage
from app.tasks.relocate import relocate_all_models

if TYPE_CHECKING:
    from app.models import StorageBackendRow

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require_perm_on_write("settings:write"))],
)
# Personal extension tokens: any logged-in user, so no settings:write gate.
api_tokens_router = APIRouter(prefix="/settings", tags=["settings"])

# Redaction sentinel GET emits for a set secret (app.storage.config.redacted)
# -- an incoming request carrying this literal value is read back verbatim
# from a form the client seeded off a redacted GET, never a real secret.
_REDACTED_SENTINEL = "***"


def _parse(backend: str, config: dict) -> StorageConfig:
    """Validate the flat ``{backend, config}`` request shape into the
    matching per-backend pydantic model.

    ``parse_storage_config`` is called directly against a hand-built dict
    here (not through FastAPI's own request-body validation), so a bad or
    incomplete config raises a plain ``pydantic.ValidationError`` rather than
    the ``RequestValidationError`` FastAPI auto-converts to 422 -- translate
    it by hand so an invalid config still 422s instead of 500ing.
    """
    try:
        return parse_storage_config({**config, "backend": backend})
    except pydantic.ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


async def _merge_stored_secrets(
    db: AsyncSession, settings: Settings, backend: str, config: dict
) -> dict:
    """Fill in the stored secret when the incoming one is missing, blank, or
    the ``"***"`` redaction sentinel GET always returns for a set secret.

    The frontend never has the real secret to send back (GET redacts it), so
    editing an already-configured SMB/S3 backend's OTHER fields -- or
    testing/migrating against the currently-active backend -- would
    otherwise 422 (validation) or silently persist the literal ``"***"`` as
    the "secret". Substituting only kicks in when the currently-stored
    active config is the SAME backend type and actually has a secret set; a
    genuinely blank secret with nothing of that backend type stored is left
    alone so ``_parse`` still 422s on it (a real error, not this case).

    The ``"***"`` sentinel itself must never be accepted as a real secret,
    though: if there's no stored secret to substitute (different backend
    type currently active, or the same type but no secret set), reject with
    422 instead of letting the literal placeholder fall through to
    ``_parse`` and get persisted as the "secret".

    Never widens what a caller can learn: this only feeds a value back into
    server-side validation/backend construction, it never appears in a
    response (GET/PUT responses go through ``redacted()``).
    """
    secret_field = SECRET_FIELD_BY_BACKEND.get(backend)
    if secret_field is None:
        return config
    incoming = config.get(secret_field) or ""
    if incoming not in ("", _REDACTED_SENTINEL):
        return config
    stored = await storage_config.get_active_config(db, settings)
    # `stored`'s secret field is a `SecretStr` (M6 A2/T3), whose truthiness is
    # ALWAYS True -- even for `SecretStr("")`. Unwrap to the plain value and
    # gate on THAT, so a stored EMPTY secret doesn't masquerade as "present"
    # (which would skip the `"***"`-sentinel 422 below) and so the merged
    # dict carries a plain str -- matching the incoming JSON config -- into
    # `_parse`, never a stray `SecretStr` (M6-MINOR-1).
    stored_secret = getattr(stored, secret_field, None) if stored.backend == backend else None
    stored_value = stored_secret.get_secret_value() if stored_secret is not None else ""
    if stored_value:
        return {**config, secret_field: stored_value}
    if incoming == _REDACTED_SENTINEL:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f'Cannot set {secret_field!r} to the redaction placeholder "***"; '
            "enter the real secret.",
        )
    return config


@router.get("/storage", response_model=StorageConfigOut)
async def get_storage_settings(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> StorageConfigOut:
    config = await storage_config.get_active_config(db, settings)
    return StorageConfigOut.from_config(config)


@router.put("/storage", response_model=StorageConfigOut)
async def put_storage_settings(
    payload: StorageConfigIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StorageConfigOut:
    merged = await _merge_stored_secrets(db, settings, payload.backend, payload.config)
    config = _parse(payload.backend, merged)
    await storage_config.set_active_config(db, settings, config)
    return StorageConfigOut.from_config(config)


@router.post("/storage/test", response_model=ConnectionTestOut)
async def test_storage_settings(
    payload: StorageConfigIn,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> ConnectionTestOut:
    merged = await _merge_stored_secrets(db, settings, payload.backend, payload.config)
    config = _parse(payload.backend, merged)
    backend = get_backend(settings, config)
    ok, detail, latency_ms = await anyio.to_thread.run_sync(probe_backend, backend)
    return ConnectionTestOut(ok=ok, detail=detail, latency_ms=latency_ms)


@router.post("/storage/migrate", response_model=JobOut)
async def migrate_storage_settings(
    payload: StorageConfigIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobOut:
    merged = await _merge_stored_secrets(db, settings, payload.backend, payload.config)
    config = _parse(payload.backend, merged)
    job = await jobs_service.create_job(
        db, id=uuid.uuid4(), type="migrate_storage", subject_type=None, subject_id=None
    )

    # The target config travels over the internal Celery broker -- encrypt
    # its secret field on the wire (M6 A1.5.2), same as it's encrypted at
    # rest; migrate_storage decrypts it at the top of the task.
    migrate_storage.apply_async(
        args=[str(job.id), storage_config.encrypt_config_secret(settings, config)],
        task_id=str(job.id),
    )

    # Under the test suite's eager Celery mode, the line above already ran
    # the whole migration inline through its own SYNC session -- refresh so
    # this (separate, async) session's identity map doesn't hand back the
    # stale "queued" snapshot from right after the insert (same reasoning as
    # app.api.scan.trigger_scan).
    await db.refresh(job)
    return JobOut.from_model(job)


# ---------------------------------------------------------------------------
# Multi-backend storage CRUD (Workstream C task C3; see
# app.services.storage_backends). The endpoints above manage the LEGACY
# single-backend shim (the default `storage_backends` row); these manage the
# full `storage_backends` table -- add/edit/remove backends, flip which one
# is the write-default, and test/relocate against any of them.
# ---------------------------------------------------------------------------


def _parse_backend_config(config: dict) -> StorageConfig:
    """Like ``_parse`` above, but for a ``config`` dict that already carries
    its own ``backend`` discriminator field (the shape
    ``StorageBackendRow.config``/``parse_storage_config`` use directly)
    rather than the legacy flat ``{backend, config}`` split.
    """
    try:
        return parse_storage_config(config)
    except pydantic.ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _reject_sentinel_secret(config: dict) -> None:
    """A brand-new backend (``POST``) has no stored secret to merge the
    ``"***"`` redaction sentinel against -- unlike ``PUT``
    (``_merge_backend_secrets`` below), there's nothing to substitute, so
    reject it outright rather than silently persisting the literal
    placeholder as a credential.
    """
    secret_field = SECRET_FIELD_BY_BACKEND.get(config.get("backend"))
    if secret_field and config.get(secret_field) == _REDACTED_SENTINEL:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f'Cannot set {secret_field!r} to the redaction placeholder "***"; '
            "enter the real secret.",
        )


def _merge_backend_secrets(settings: Settings, row: StorageBackendRow, config: dict) -> dict:
    """``PUT``-time secret-merge scoped to THIS backend row -- mirrors
    ``_merge_stored_secrets`` above (which is scoped to the legacy "active"
    config) but reads the stored secret straight off ``row`` instead of a
    fresh DB round trip, since the caller already has it loaded (hence a
    plain sync function -- unlike ``_merge_stored_secrets``, there's no
    ``await`` left once the DB round trip is gone). Same reasoning: GET
    never returns a real secret, so editing e.g. an SMB backend's ``host``
    without resending its ``password`` would otherwise 422 (validation) or
    persist the ``"***"`` sentinel as the "secret" unless the stored value
    is substituted back in for a blank/sentinel incoming one -- and the
    sentinel is still rejected outright when there's no stored secret of the
    SAME backend type to substitute.
    """
    backend_name = config.get("backend", row.scheme)
    secret_field = SECRET_FIELD_BY_BACKEND.get(backend_name)
    if secret_field is None:
        return config
    incoming = config.get(secret_field) or ""
    if incoming not in ("", _REDACTED_SENTINEL):
        return config
    stored_value = ""
    if row.scheme == backend_name:
        stored_data, _ = storage_config.decrypt_config_row(settings, dict(row.config))
        stored_value = stored_data.get(secret_field) or ""
    if stored_value:
        return {**config, secret_field: stored_value}
    if incoming == _REDACTED_SENTINEL:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f'Cannot set {secret_field!r} to the redaction placeholder "***"; '
            "enter the real secret.",
        )
    return config


@router.get("/storage/backends", response_model=list[StorageBackendOut])
async def list_storage_backends(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> list[StorageBackendOut]:
    rows = await storage_backends_service.list_backends(db)
    return [StorageBackendOut.from_row(row, settings) for row in rows]


@router.post(
    "/storage/backends", status_code=status.HTTP_201_CREATED, response_model=StorageBackendOut
)
async def create_storage_backend(
    payload: StorageBackendCreateIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StorageBackendOut:
    _reject_sentinel_secret(payload.config)
    config = _parse_backend_config(payload.config)
    row = await storage_backends_service.create_backend(db, settings, payload.name, config)
    return StorageBackendOut.from_row(row, settings)


@router.put("/storage/backends/{backend_id}", response_model=StorageBackendOut)
async def update_storage_backend(
    backend_id: int,
    payload: StorageBackendUpdateIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StorageBackendOut:
    row = await storage_backends_service.get_backend_row(db, backend_id)
    config: StorageConfig | None = None
    if payload.config is not None:
        merged = _merge_backend_secrets(settings, row, payload.config)
        config = _parse_backend_config(merged)
    row = await storage_backends_service.update_backend(
        db, settings, backend_id, name=payload.name, config=config
    )
    return StorageBackendOut.from_row(row, settings)


@router.delete("/storage/backends/{backend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_storage_backend(backend_id: int, db: AsyncSession = Depends(get_db)) -> None:
    # 409s via app.services.storage_backends.delete_backend's own guardrails
    # (last backend, default backend, or one file_locations still
    # references) -- nothing extra to enforce here.
    await storage_backends_service.delete_backend(db, backend_id)


@router.post("/storage/backends/{backend_id}/test", response_model=ConnectionTestOut)
async def test_storage_backend(
    backend_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ConnectionTestOut:
    backend = await storage_backends_service.backend_for_id(db, settings, backend_id)
    ok, detail, latency_ms = await anyio.to_thread.run_sync(probe_backend, backend)
    return ConnectionTestOut(ok=ok, detail=detail, latency_ms=latency_ms)


@router.post("/storage/backends/{backend_id}/default", response_model=StorageBackendOut)
async def set_default_storage_backend(
    backend_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StorageBackendOut:
    row = await storage_backends_service.set_default_backend(db, backend_id)
    return StorageBackendOut.from_row(row, settings)


@router.post("/storage/backends/{backend_id}/migrate", response_model=JobOut)
async def migrate_library_to_backend(
    backend_id: int,
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    """ "Move all models here" (M8 F): set this backend as the write-default AND
    relocate the whole library onto it. The multi-backend replacement for the
    legacy whole-library migrate -- reuses the per-file relocate path so
    ``files.backend_id`` and ``file_locations`` stay accurate."""
    await storage_backends_service.get_backend_row(db, backend_id)  # 404 if unknown
    await storage_backends_service.set_default_backend(db, backend_id)
    job = await jobs_service.create_job(
        db, id=uuid.uuid4(), type="relocate_all", subject_type=None, subject_id=None
    )
    relocate_all_models.apply_async(args=[str(job.id), backend_id, "move"], task_id=str(job.id))
    # Eager Celery (tests) already ran the relocate inline through its own sync
    # session -- refresh so this async session returns the terminal job state.
    await db.refresh(job)
    return JobOut.from_model(job)


def _merge_import_token(incoming_raw: str, stored: str | None) -> str | None:
    """Merge-on-blank/sentinel for ONE import-token field, mirroring
    ``_merge_stored_secrets``: a blank or ``"***"`` submit keeps the stored
    token; a bare ``"***"`` with nothing stored is rejected 422 (never
    persist the placeholder as a credential); a real value replaces it."""
    incoming = (incoming_raw or "").strip()
    if incoming not in ("", _REDACTED_SENTINEL):
        return incoming
    if stored:
        return stored
    if incoming == _REDACTED_SENTINEL:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            'Cannot set the token to the redaction placeholder "***"; enter the real token.',
        )
    return None  # explicit clear when nothing stored + blank submit


@router.get("/import-tokens", response_model=ImportTokensOut)
async def get_import_tokens_settings(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ImportTokensOut:
    tokens = await import_tokens.get_import_tokens(db, settings)
    return ImportTokensOut(
        thingiverse_token=_REDACTED_SENTINEL if tokens.thingiverse_token else "",
        makerworld_token=_REDACTED_SENTINEL if tokens.makerworld_token else "",
    )


@router.put("/import-tokens", response_model=ImportTokensOut)
async def put_import_tokens_settings(
    payload: ImportTokensIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ImportTokensOut:
    """Per-field merge-on-blank/sentinel (``_merge_import_token``) applied
    independently to each token so submitting one never clobbers the other."""
    stored = await import_tokens.get_import_tokens(db, settings)
    thingiverse_value = _merge_import_token(payload.thingiverse_token, stored.thingiverse_token)
    makerworld_value = _merge_import_token(payload.makerworld_token, stored.makerworld_token)
    await import_tokens.set_import_tokens(
        db, settings, thingiverse_token=thingiverse_value, makerworld_token=makerworld_value
    )
    return ImportTokensOut(
        thingiverse_token=_REDACTED_SENTINEL if thingiverse_value else "",
        makerworld_token=_REDACTED_SENTINEL if makerworld_value else "",
    )


# ---------------------------------------------------------------------------
# Runtime feature settings (Round 10 "Settings" UI; see app.services.
# app_config). Five knobs -- printer_enabled, scan_interval_s,
# collection_sync_interval_s, watch_interval_s, watch_stable_s -- that used
# to be env/`.env`-only, now DB-backed and editable without a restart.
# Nothing here is secret, so unlike storage/import-tokens above there's no
# redaction and no merge-on-blank: PUT is a full replace of all five fields.
# ---------------------------------------------------------------------------


@router.get("/app", response_model=AppSettingsOut)
async def get_app_settings(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> AppSettingsOut:
    """Resolved runtime feature settings: the DB row overlaid per-field onto
    env/default (``app.services.app_config.get_app_config``)."""
    config = await app_config.get_app_config(db, settings)
    return AppSettingsOut.from_config(config)


@router.put("/app", response_model=AppSettingsOut)
async def put_app_settings(
    payload: AppSettingsIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AppSettingsOut:
    """Persist all five fields as a full replace; every field is required
    and independently validated (``ge=0``) at the schema boundary, so
    there's no partial-submit case to merge unlike ``PUT /settings/import-
    tokens``."""
    config = await app_config.set_app_config(
        db, settings, app_config.AppConfig(**payload.model_dump())
    )
    return AppSettingsOut.from_config(config)


# ---------------------------------------------------------------------------
# Bambu Lab account connect flow (Workstream B task B2). Unlike the storage/
# import-token settings above, this isn't a raw PUT of an operator-typed
# secret -- login/verify each make a real outbound call to Bambu
# (app.services.bambu_auth), so they run the blocking httpx client on a
# worker thread via anyio.to_thread.run_sync (same pattern as
# test_storage_settings' probe_backend call). No response here EVER carries
# a token (see BambuLoginOut/BambuStatusOut) -- only "connected"/"mfa_
# required" plus the account/region the operator already knows.
# ---------------------------------------------------------------------------


@router.get("/bambu", response_model=BambuStatusOut)
async def get_bambu_status(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> BambuStatusOut:
    state = await bambu_auth.get_bambu_auth(db, settings)
    connected = bool(state.refresh_token)
    return BambuStatusOut(
        connected=connected,
        account=state.account,
        region=state.region,
        # A stamped `refresh_failed_at` alone isn't reconnect-worthy if the
        # stored access token (long-lived JWT -- see bambu_auth's module
        # docstring) is still valid; refresh is best-effort now, so a failed
        # refresh attempt next to a still-good access token is not an outage.
        needs_reconnect=connected
        and bool(state.refresh_failed_at)
        and not state.has_valid_access_token(),
    )


@router.post("/bambu/login", response_model=BambuLoginOut)
async def post_bambu_login(
    payload: BambuLoginIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BambuLoginOut:
    try:
        result = await anyio.to_thread.run_sync(
            bambu_auth.login, payload.account, payload.password, payload.region
        )
    except bambu_auth.BambuAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if result.status == "connected":
        await bambu_auth.set_bambu_auth(
            db,
            settings,
            account=payload.account,
            region=payload.region,
            refresh_token=result.refresh_token or "",
            access_token=result.access_token,
            access_expires_at=result.expires_at,
        )
        return BambuLoginOut(status="connected", account=payload.account, region=payload.region)
    return BambuLoginOut(
        status="mfa_required",
        account=payload.account,
        region=payload.region,
        mfa_context=result.mfa_context,
    )


@router.post("/bambu/verify", response_model=BambuLoginOut)
async def post_bambu_verify(
    payload: BambuVerifyIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BambuLoginOut:
    try:
        result = await anyio.to_thread.run_sync(
            bambu_auth.verify_code,
            payload.account,
            payload.code,
            payload.region,
            payload.mfa_context,
        )
    except bambu_auth.BambuAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await bambu_auth.set_bambu_auth(
        db,
        settings,
        account=payload.account,
        region=payload.region,
        refresh_token=result.refresh_token or "",
        access_token=result.access_token,
        access_expires_at=result.expires_at,
    )
    return BambuLoginOut(status="connected", account=payload.account, region=payload.region)


@router.delete("/bambu", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bambu_auth(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> None:
    await bambu_auth.clear_bambu_auth(db, settings)


# ---------------------------------------------------------------------------
# Printables account connect flow (Workstream A task A1). Unlike Bambu, there
# is no login/MFA -- the operator pastes their browser's `auth.refresh_token`
# cookie value, which `printables_auth.refresh` validates against Printables'
# own refresh endpoint (a real outbound call, so it runs on a worker thread
# via anyio.to_thread.run_sync, same as the Bambu flow above). No response
# here EVER carries a token (see PrintablesConnectIn/PrintablesStatusOut) --
# only "connected"/username/user_id, the identity `fetch_identity` looks up.
# ---------------------------------------------------------------------------


@router.get("/printables", response_model=PrintablesStatusOut)
async def get_printables_status(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> PrintablesStatusOut:
    state = await printables_auth.get_printables_auth(db, settings)
    return PrintablesStatusOut(
        connected=bool(state.refresh_token), username=state.username, user_id=state.user_id
    )


@router.post("/printables/connect", response_model=PrintablesStatusOut)
async def post_printables_connect(
    payload: PrintablesConnectIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PrintablesStatusOut:
    try:
        token = await anyio.to_thread.run_sync(printables_auth.refresh, payload.refresh_token)
    except printables_auth.PrintablesAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # The credential is proven good by the refresh call above -- a failure to
    # look up the (cosmetic) username/user_id must not fail the connect, so
    # the identity lookup is best-effort and the ROTATED token is stored
    # either way (never the one the operator pasted; Printables rotates it
    # on every refresh call -- see printables_auth's module docstring).
    username: str | None = None
    user_id: str | None = None
    with contextlib.suppress(Exception):  # cosmetic lookup only, never fails the connect
        user_id, username = await anyio.to_thread.run_sync(fetch_identity, token.access_token)

    await printables_auth.set_printables_auth(
        db, settings, username=username, user_id=user_id, refresh_token=token.refresh_token
    )
    return PrintablesStatusOut(connected=True, username=username, user_id=user_id)


@router.delete("/printables", status_code=status.HTTP_204_NO_CONTENT)
async def delete_printables_auth(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> None:
    await printables_auth.clear_printables_auth(db, settings)


# ---------------------------------------------------------------------------
# Browser-extension API tokens (M10 Workstream A). Session-gated management
# (these live under `protected_router`, unlike `/ext/*` itself) of the
# SEPARATE bearer-token auth plane `app.api.ext` uses -- mint returns the
# plaintext ONCE, list/delete never touch the plaintext or its hash. See
# `app.services.api_tokens` for the storage/verification contract.
# ---------------------------------------------------------------------------


@api_tokens_router.post(
    "/api-tokens", status_code=status.HTTP_201_CREATED, response_model=ApiTokenMintOut
)
async def create_api_token(
    payload: ApiTokenCreateIn,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_session),
) -> ApiTokenMintOut:
    token, row = await api_tokens.mint(db, user_id=auth.user.id, label=payload.label)
    return ApiTokenMintOut(id=row.id, label=row.label, token=token, created_at=row.created_at)


@api_tokens_router.get("/api-tokens", response_model=list[ApiTokenOut])
async def list_api_tokens(db: AsyncSession = Depends(get_db)) -> list[ApiTokenOut]:
    rows = await api_tokens.list_tokens(db)
    return [ApiTokenOut.from_model(row) for row in rows]


@api_tokens_router.delete("/api-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_token(token_id: int, db: AsyncSession = Depends(get_db)) -> None:
    deleted = await api_tokens.revoke(db, token_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"api token {token_id} not found")
