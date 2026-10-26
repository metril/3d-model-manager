"""Cloud preset sync API (``/cloud-presets``): pair Orca Cloud, list a cloud
account's presets with their sync status against the local ``slicer_profiles``,
diff one, and import/resync. Bambu Cloud reuses the Bambu account connected in
Settings (``/settings/bambu``). Connecting/disconnecting Orca needs
``settings:write``; importing needs ``inventory:write``."""

from __future__ import annotations

import time
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.services import bambu_auth, cloud_presets, orca_cloud
from app.tasks import base

router = APIRouter(prefix="/cloud-presets", tags=["cloud-presets"])

Source = Literal["orca", "bambu"]


class SourceStatus(BaseModel):
    connected: bool
    account: str | None = None
    needs_reconnect: bool = False
    pending: bool = False  # Orca: a device-code approval is awaiting the user


class CloudStatusOut(BaseModel):
    orca: SourceStatus
    bambu: SourceStatus


class DeviceStartOut(BaseModel):
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int
    interval: int


class DevicePollOut(BaseModel):
    status: Literal["pending", "complete", "denied", "expired"]
    interval: int = 5


class PresetOut(BaseModel):
    preset_id: str
    name: str
    kind: str
    updated_time: str | None = None
    status: str
    profile_id: int | None = None


class PresetListOut(BaseModel):
    presets: list[PresetOut]
    # Local rows synced from this account whose cloud preset no longer exists.
    removed: list[PresetOut] = []


class DiffChange(BaseModel):
    key: str
    local: object | None = None
    remote: object | None = None


class DiffOut(BaseModel):
    preset_id: str
    name: str
    status: str
    profile_id: int | None = None
    changes: list[DiffChange]


class SyncIn(BaseModel):
    # None = every new or remotely-changed preset.
    preset_ids: list[str] | None = None
    # Also overwrite presets edited locally (otherwise they are skipped).
    overwrite_local: bool = False


class SyncOut(BaseModel):
    created: int
    updated: int
    skipped: list[dict]
    failed: list[dict]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, orca_cloud.OrcaCloudAuthError):
        return HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
    if isinstance(exc, bambu_auth.BambuAuthError):
        code = (
            status.HTTP_401_UNAUTHORIZED
            if exc.kind in ("expired", "not_configured")
            else status.HTTP_400_BAD_REQUEST
        )
        return HTTPException(code, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.get("/status", response_model=CloudStatusOut)
async def get_status(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> CloudStatusOut:
    orca = await orca_cloud.get_state(db, settings)
    bambu = await bambu_auth.get_bambu_auth(db, settings)
    connected = bool(bambu.refresh_token)
    return CloudStatusOut(
        orca=SourceStatus(
            connected=orca.connected,
            pending=bool(
                orca.pending_device_code
                and orca.pending_expires_at
                and orca.pending_expires_at > time.time()
            ),
        ),
        bambu=SourceStatus(
            connected=connected,
            account=bambu.account,
            needs_reconnect=connected
            and bool(bambu.refresh_failed_at)
            and not bambu.has_valid_access_token(),
        ),
    )


# --- Orca Cloud pairing (RFC 8628 device flow) -----------------------------


@router.post(
    "/orca/device/start",
    response_model=DeviceStartOut,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def orca_device_start(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> DeviceStartOut:
    try:
        data = await anyio.to_thread.run_sync(
            orca_cloud.request_device_code, settings, "3D Model Manager"
        )
    except orca_cloud.OrcaCloudError as exc:
        raise _http(exc) from None
    st = await orca_cloud.get_state(db, settings)
    expires_in = int(data.get("expires_in") or orca_cloud.DEVICE_CODE_TTL_S)
    st.pending_device_code = data["device_code"]
    st.pending_interval = int(data.get("interval") or 5)
    st.pending_expires_at = time.time() + expires_in
    await orca_cloud.save_state(db, settings, st)
    return DeviceStartOut(
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        verification_uri_complete=data.get("verification_uri_complete"),
        expires_in=expires_in,
        interval=st.pending_interval,
    )


@router.post(
    "/orca/device/poll",
    response_model=DevicePollOut,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def orca_device_poll(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> DevicePollOut:
    st = await orca_cloud.get_state(db, settings)
    if not st.pending_device_code or (st.pending_expires_at or 0) <= time.time():
        st.pending_device_code = None
        await orca_cloud.save_state(db, settings, st)
        return DevicePollOut(status="expired")
    try:
        result, data = await anyio.to_thread.run_sync(
            orca_cloud.poll_token, settings, st.pending_device_code
        )
    except orca_cloud.OrcaCloudError as exc:
        raise _http(exc) from None
    if result == orca_cloud.COMPLETE and data is not None:
        orca_cloud.apply_token_response(st, data)
        st.connected_at = time.time()
        st.pending_device_code = None
        await orca_cloud.save_state(db, settings, st)
        return DevicePollOut(status="complete")
    if result in (orca_cloud.DENIED, orca_cloud.EXPIRED):
        st.pending_device_code = None
        await orca_cloud.save_state(db, settings, st)
        return DevicePollOut(status="denied" if result == orca_cloud.DENIED else "expired")
    if result == orca_cloud.SLOW_DOWN:
        st.pending_interval += 5
        await orca_cloud.save_state(db, settings, st)
    return DevicePollOut(status="pending", interval=st.pending_interval)


@router.delete(
    "/orca",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def orca_disconnect(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> None:
    await orca_cloud.save_state(db, settings, orca_cloud.OrcaAuthState())


# --- presets ---------------------------------------------------------------


async def _remote_list(
    db: AsyncSession, settings: Settings, source: str
) -> list[cloud_presets.RemotePreset]:
    try:
        if source == "orca":
            token = await orca_cloud.ensure_access_token(db, settings)
            raw = await anyio.to_thread.run_sync(orca_cloud.list_profiles, settings, token)
            return cloud_presets.parse_orca(raw)

        def _bambu() -> list[cloud_presets.RemotePreset]:
            with base.sync_session() as session:
                return cloud_presets.fetch_bambu_list(session, settings)

        return await anyio.to_thread.run_sync(_bambu)
    except (
        orca_cloud.OrcaCloudError,
        bambu_auth.BambuAuthError,
        cloud_presets.CloudPresetError,
    ) as exc:
        raise _http(exc) from None


def _rows_by_cloud_id(source: str) -> dict:
    with base.sync_session() as session:
        rows = cloud_presets.local_rows(session, source)
        session.expunge_all()
        return rows


def _classify(remote_list, rows) -> list[PresetOut]:
    out = []
    for r in remote_list:
        row = rows.get(r.preset_id)
        if r.content is not None:
            st = cloud_presets.status_of(row, cloud_presets.content_hash(r.content))
        elif row is None:
            st = cloud_presets.NEW
        else:
            # Bambu lists metadata only: a changed ``updated_time`` is the
            # remote-changed signal; the hash is confirmed on sync/diff.
            local_mod = row.data_hash != cloud_presets.content_hash(row.data or {})
            changed = r.updated_time is not None and row.cloud_updated != r.updated_time
            st = (
                cloud_presets.CONFLICT
                if changed and local_mod
                else cloud_presets.REMOTE_CHANGED
                if changed
                else cloud_presets.LOCAL_MODIFIED
                if local_mod
                else cloud_presets.UP_TO_DATE
            )
        out.append(
            PresetOut(
                preset_id=r.preset_id,
                name=r.name,
                kind=r.kind.value,
                updated_time=r.updated_time,
                status=st,
                profile_id=row.id if row is not None else None,
            )
        )
    return out


@router.get("/{source}/presets", response_model=PresetListOut)
async def list_presets(
    source: Source,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> PresetListOut:
    remote_list = await _remote_list(db, settings, source)
    rows = await anyio.to_thread.run_sync(_rows_by_cloud_id, source)
    presets = _classify(remote_list, rows)
    seen = {r.preset_id for r in remote_list}
    removed = [
        PresetOut(
            preset_id=cid,
            name=row.name,
            kind=row.kind.value,
            status="removed",
            profile_id=row.id,
        )
        for cid, row in rows.items()
        if cid not in seen
    ]
    return PresetListOut(presets=presets, removed=removed)


async def _content_for(
    db: AsyncSession, settings: Settings, source: str, remote: cloud_presets.RemotePreset
) -> dict:
    if remote.content is not None:
        return remote.content

    def _fetch() -> dict:
        with base.sync_session() as session:
            return cloud_presets.fetch_bambu_content(session, settings, remote.preset_id)

    try:
        return await anyio.to_thread.run_sync(_fetch)
    except (bambu_auth.BambuAuthError, cloud_presets.CloudPresetError) as exc:
        raise _http(exc) from None


@router.get("/{source}/presets/{preset_id}/diff", response_model=DiffOut)
async def diff_preset(
    source: Source,
    preset_id: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> DiffOut:
    remote_list = await _remote_list(db, settings, source)
    remote = next((r for r in remote_list if r.preset_id == preset_id), None)
    if remote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"cloud preset {preset_id!r} not found")
    content = await _content_for(db, settings, source, remote)
    rows = await anyio.to_thread.run_sync(_rows_by_cloud_id, source)
    row = rows.get(preset_id)
    remote_data = cloud_presets.normalize(remote.kind, remote.name, content)
    st = cloud_presets.status_of(row, cloud_presets.content_hash(content))
    return DiffOut(
        preset_id=preset_id,
        name=remote.name,
        status=st,
        profile_id=row.id if row is not None else None,
        changes=[
            DiffChange(**c)
            for c in cloud_presets.diff_data(row.data if row is not None else {}, remote_data)
        ],
    )


@router.post(
    "/{source}/sync",
    response_model=SyncOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def sync_presets(
    source: Source,
    payload: SyncIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> SyncOut:
    remote_list = await _remote_list(db, settings, source)
    rows = await anyio.to_thread.run_sync(_rows_by_cloud_id, source)
    wanted = set(payload.preset_ids) if payload.preset_ids is not None else None
    if wanted is not None:
        unknown = wanted - {r.preset_id for r in remote_list}
        if unknown:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"cloud preset(s) not found: {', '.join(sorted(unknown))}",
            )
    result = cloud_presets.SyncResult()
    user_id = ctx.user.id

    for remote in remote_list:
        if wanted is not None and remote.preset_id not in wanted:
            continue
        if wanted is None:
            # Bulk resync: only presets that are new or changed upstream.
            row = rows.get(remote.preset_id)
            if remote.content is not None:
                pre = cloud_presets.status_of(row, cloud_presets.content_hash(remote.content))
                if pre == cloud_presets.UP_TO_DATE:
                    continue
        try:
            content = await _content_for(db, settings, source, remote)
        except HTTPException as exc:
            result.failed.append(
                {"preset_id": remote.preset_id, "name": remote.name, "error": exc.detail}
            )
            continue

        def _apply(remote=remote, content=content) -> str:
            with base.sync_session() as session:
                outcome = cloud_presets.upsert_remote(
                    session, remote, content, user_id, overwrite_local=payload.overwrite_local
                )
                session.commit()
                return outcome

        outcome = await anyio.to_thread.run_sync(_apply)
        if outcome == "created":
            result.created += 1
        elif outcome == "updated":
            result.updated += 1
        elif outcome != cloud_presets.UP_TO_DATE:
            result.skipped.append(
                {"preset_id": remote.preset_id, "name": remote.name, "reason": outcome}
            )
    await db.commit()  # release this request's own txn before the caller sees fresh rows
    return SyncOut(
        created=result.created, updated=result.updated, skipped=result.skipped, failed=result.failed
    )
