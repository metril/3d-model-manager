"""``/api/ext/v2`` -- the scoped API-key surface (remote control / automation).

Auth: ``X-API-Key: <key>`` or ``Authorization: Bearer <key>`` carrying a scoped
key minted via ``POST /settings/api-keys``. Legacy browser-extension tokens
(no scopes) are refused here, and scoped keys are refused on ``/ext`` v1.

A key acts as its creator with permissions capped to ``owner perms & scope
perms`` (never ``admin``/``settings:write``), optionally restricted to a
printer allowlist, and is rate-limited per key. Every mutating call is
audit-logged. Handlers are the session API's own, called with the key's
capped ``AuthContext``, so validation, capability gating and refuse-while-
printing behave exactly as in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import models as models_api
from app.api import printer_control
from app.api import printers as printers_api
from app.api import queue as queue_api
from app.api import revisions as revisions_api
from app.api import slice_jobs as slice_api
from app.api import uploads as uploads_api
from app.api.deps import (
    AuthContext,
    _unauthenticated_bearer,
    extract_credential,
    get_storage_backend,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.models import ApiToken, AuditLog, Printer, User
from app.models.library import PrintQueueEntry
from app.schemas.imports import ImportCreate, ImportOut
from app.schemas.library import ModelCreate, RevisionCreate
from app.schemas.printers import PrinterOut
from app.schemas.queue import QueueEnqueueIn, QueueReorderIn
from app.schemas.slicing import SliceJobIn
from app.services import api_key_limit, api_tokens
from app.services import queue as queue_service
from app.services.permissions import FULL_ACL, load_grants

router = APIRouter(prefix="/ext/v2", tags=["ext-v2"])

# Control commands a key may issue through the command bus (generic
# ``gcode``/``jog``/``extrude``/AMS/k-profile/firmware stay session-only).
CONTROL_COMMANDS: dict[str, str] = {
    "temperature": "set_temperature",
    "fan": "set_fan",
    "light": "set_light",
    "speed": "set_speed",
    "home": "home",
    "airduct": "set_airduct",
    "clear-hms": "clear_hms",
}


@dataclass(slots=True)
class KeyAuth:
    key: ApiToken
    ctx: AuthContext
    owner_admin: bool

    def allowed_printer(self, printer_id: int) -> bool:
        return not self.key.printer_ids or printer_id in self.key.printer_ids


async def require_key(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> KeyAuth:
    token = extract_credential(authorization, x_api_key)
    key = await api_tokens.verify(db, token)
    if key is None:
        raise _unauthenticated_bearer()
    if key.scopes is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "legacy extension tokens are not API keys")
    owner = await db.get(User, key.user_id)
    if owner is None or not owner.is_active:
        raise _unauthenticated_bearer()

    limit = key.rate_limit_per_min or api_tokens.DEFAULT_RATE_LIMIT
    allowed, remaining, retry = await api_key_limit.hit(settings.redis_url, key.id, limit)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate limit exceeded",
            headers={"Retry-After": str(retry), "X-RateLimit-Limit": str(limit)},
        )

    groups, owner_perms, acl = await load_grants(db, owner)
    owner_admin = "admin" in owner_perms
    if owner_admin:  # admins have no PrinterAccess rows; materialise full access
        ids = (await db.execute(select(Printer.id))).scalars().all()
        acl = dict.fromkeys(ids, FULL_ACL)
    if key.printer_ids:
        acl = {pid: a for pid, a in acl.items() if pid in key.printer_ids}
    perms = (owner_perms & api_tokens.scope_perms(key.scopes)) - {"admin"}
    ctx = AuthContext(user=owner, session=None, perms=perms, printer_acl=acl, groups=groups)

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        raw = request.path_params.get("printer_id")
        pid = int(raw) if raw is not None and str(raw).isdigit() else None
        db.add(
            AuditLog(
                user_id=owner.id,
                created_by_user_id=owner.id,
                printer_id=pid if pid in acl else None,
                action=f"api_key:{request.method.lower()}",
                payload={
                    "key_id": key.id,
                    "key_label": key.label,
                    "path": request.url.path,
                },
            )
        )
        await db.commit()
    return KeyAuth(key=key, ctx=ctx, owner_admin=owner_admin)


def need(scope: Literal["read", "control", "queue", "upload"]):
    """Dependency: the key must carry ``scope`` AND its owner must still hold
    the permissions that scope exercises."""
    required = api_tokens.SCOPE_PERMS[scope]

    async def dep(auth: KeyAuth = Depends(require_key)) -> KeyAuth:
        if scope not in (auth.key.scopes or []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing scope: {scope}")
        if not required <= auth.ctx.perms:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "key owner lacks the permission")
        return auth

    return dep


_BODY = Body(default_factory=dict)
_READ = need("read")
_CONTROL = need("control")
_QUEUE = need("queue")
_UPLOAD = need("upload")


def _visible(auth: KeyAuth, printer_id: int) -> None:
    """404 for a printer outside the key's reach (ids stay unenumerable)."""
    if printer_id not in auth.ctx.printer_acl:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")


def _own_or_admin(auth: KeyAuth, created_by: int | None) -> None:
    if auth.owner_admin or (created_by is not None and created_by == auth.ctx.user.id):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "only the creator or an admin can change this")


# -- meta ---------------------------------------------------------------------


@router.get("/ping")
async def ping(auth: KeyAuth = Depends(require_key)) -> dict:
    k = auth.key
    return {
        "ok": True,
        "key": {
            "id": k.id,
            "label": k.label,
            "scopes": k.scopes,
            "printer_ids": k.printer_ids,
            "expires_at": k.expires_at,
        },
    }


# -- printers: status (read) + control ----------------------------------------


@router.get("/printers", response_model=list[PrinterOut])
async def list_printers(
    db: AsyncSession = Depends(get_db), auth: KeyAuth = Depends(_READ)
) -> list[PrinterOut]:
    ids = set(auth.ctx.printer_acl)
    rows = (
        (await db.execute(select(Printer).where(Printer.id.in_(ids)).order_by(Printer.id)))
        .scalars()
        .all()
        if ids
        else []
    )
    return [PrinterOut.from_model(p) for p in rows]


@router.get("/printers/{printer_id}/status")
async def printer_status(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_READ),
):
    _visible(auth, printer_id)
    return await printers_api.printer_status(printer_id, db, settings, auth.ctx)


async def _command(
    db: AsyncSession,
    settings: Settings,
    auth: KeyAuth,
    printer_id: int,
    command: str,
    body: dict | None,
) -> JSONResponse:
    _visible(auth, printer_id)
    auth.ctx.require_printer(printer_id, "control")
    printer = await printers_api._get_enabled_or_404(db, printer_id)
    return await printer_control.run_command(
        db,
        settings,
        auth.ctx,
        printer,
        command,
        body,
        audit_extra={"api_key_id": auth.key.id},
    )


def _simple(command: str):
    async def endpoint(
        printer_id: int,
        db: AsyncSession = Depends(get_db),
        settings: Settings = Depends(get_settings),
        auth: KeyAuth = Depends(_CONTROL),
    ) -> JSONResponse:
        return await _command(db, settings, auth, printer_id, command, {})

    endpoint.__name__ = f"key_{command}"
    return endpoint


for _cmd in ("pause", "resume", "stop"):
    router.add_api_route(f"/printers/{{printer_id}}/{_cmd}", _simple(_cmd), methods=["POST"])


@router.post("/printers/{printer_id}/control/{name}")
async def control(
    printer_id: int,
    name: str,
    body: dict = _BODY,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_CONTROL),
) -> JSONResponse:
    command = CONTROL_COMMANDS.get(name)
    if command is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"unknown control {name!r}; one of {', '.join(sorted(CONTROL_COMMANDS))}",
        )
    return await _command(db, settings, auth, printer_id, command, body)


# -- queue --------------------------------------------------------------------


@router.get("/queue")
async def list_queue(
    include_done: bool = False,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_READ),
):

    return await queue_service.list_queue(db, settings, auth.ctx, include_done=include_done)


@router.post("/queue", status_code=status.HTTP_201_CREATED)
async def enqueue(
    payload: QueueEnqueueIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_QUEUE),
):
    if auth.key.printer_ids:
        if payload.printer_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "printer_id is required for a key with a printer allowlist",
            )
        _visible(auth, payload.printer_id)
    elif payload.printer_id is not None:
        _visible(auth, payload.printer_id)
    return await queue_api.enqueue(payload, response, db, settings, auth.ctx)


async def _guard_entry(db: AsyncSession, auth: KeyAuth, entry_id: int) -> None:
    entry = await db.get(PrintQueueEntry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"queue entry {entry_id} not found")
    for pid in (entry.printer_id, entry.assigned_printer_id):
        if pid is not None and pid not in auth.ctx.printer_acl:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"queue entry {entry_id} not found")
    if auth.key.printer_ids and entry.printer_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"queue entry {entry_id} not found")
    _own_or_admin(auth, entry.created_by_user_id)


@router.patch("/queue/{entry_id}")
async def reorder(
    entry_id: int,
    payload: QueueReorderIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_QUEUE),
):
    await _guard_entry(db, auth, entry_id)
    return await queue_service.update_entry(db, settings, entry_id, payload, auth.ctx)


@router.delete("/queue/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    auth: KeyAuth = Depends(_QUEUE),
) -> None:
    await _guard_entry(db, auth, entry_id)
    await queue_service.remove_entry(db, entry_id)


# -- slice-only jobs -----------------------------------------------------------


@router.post("/files/{file_id}/slice", status_code=status.HTTP_201_CREATED)
async def start_slice(
    file_id: int,
    payload: SliceJobIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_QUEUE),
):
    return await slice_api.start_slice(file_id, payload, db, settings, auth.ctx)


@router.get("/slice-jobs/{job_id}")
async def get_slice_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    auth: KeyAuth = Depends(_READ),
):
    import uuid

    try:
        jid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "slice job not found") from None
    return await slice_api.get_slice_job(jid, db, auth.ctx)


# -- library: models, upload, import ---------------------------------------------


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def create_model(
    payload: ModelCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_UPLOAD),
):
    backend = await get_storage_backend(db, settings)
    return await models_api.create_model(payload, db, backend, settings, auth.ctx)


@router.post("/models/{model_id}/revisions", status_code=status.HTTP_201_CREATED)
async def create_revision(
    model_id: int,
    payload: RevisionCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_UPLOAD),
):
    backend = await get_storage_backend(db, settings)
    return await revisions_api.create_revision(model_id, payload, db, backend, settings, auth.ctx)


@router.put("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    model_id: int,
    revision_id: int,
    rel_path: str,
    replace: bool = False,
    allow_duplicate: bool = False,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_UPLOAD),
):
    return await uploads_api.upload_file(
        request, model_id, revision_id, rel_path, replace, allow_duplicate, db, settings, auth.ctx
    )


@router.post("/imports", status_code=status.HTTP_201_CREATED, response_model=ImportOut)
async def create_import(
    payload: ImportCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    auth: KeyAuth = Depends(_UPLOAD),
) -> ImportOut:
    from app.services.imports import start_import

    imp, created = await start_import(db, payload.url, created_by_user_id=auth.ctx.user.id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return ImportOut.from_model(imp)
