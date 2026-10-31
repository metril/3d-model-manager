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
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import blobs as blobs_api
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
from app.models import ApiToken, AuditLog, Printer, PrintJob, User
from app.models.enums import BlobFormat
from app.models.library import Blob, File, Model, PrintQueueEntry
from app.schemas.imports import ImportCreate, ImportOut
from app.schemas.library import ModelCreate, RevisionCreate
from app.schemas.printers import (
    CameraUrlsOut,
    PrinterOut,
    PrinterStatusOut,
    PrintJobOut,
    PrintRequest,
)
from app.schemas.queue import QueueEnqueueIn, QueueReorderIn
from app.schemas.slicing import SliceJobIn
from app.services import api_key_limit, api_tokens, camera_http, derivatives
from app.services import queue as queue_service
from app.services.app_config import get_app_config
from app.services.permissions import FULL_ACL, load_grants
from app.services.printer_status import active_job_rows

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

    return KeyAuth(key=key, ctx=ctx, owner_admin=owner_admin)


async def _audit(db: AsyncSession, request: Request, auth: KeyAuth, action: str, **extra) -> None:
    """Audit a non-read call. Written AFTER the scope/permission check by
    ``need`` so a rejected call is never recorded as an accepted one."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    raw = request.path_params.get("printer_id")
    pid = int(raw) if raw is not None and str(raw).isdigit() else None
    db.add(
        AuditLog(
            user_id=auth.ctx.user.id,
            created_by_user_id=auth.ctx.user.id,
            printer_id=pid if pid in auth.ctx.printer_acl else None,
            action=action,
            payload={
                "key_id": auth.key.id,
                "key_label": auth.key.label,
                "path": request.url.path,
                **extra,
            },
        )
    )
    await db.commit()


def need(scope: Literal["read", "control", "queue", "upload"]):
    """Dependency: the key must carry ``scope`` AND its owner must still hold
    the permissions that scope exercises."""
    required = api_tokens.SCOPE_PERMS[scope]

    async def dep(
        request: Request,
        auth: KeyAuth = Depends(require_key),
        db: AsyncSession = Depends(get_db),
    ) -> KeyAuth:
        if scope not in (auth.key.scopes or []):
            await _audit(db, request, auth, "api_key:denied", reason=f"missing scope: {scope}")
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing scope: {scope}")
        if not required <= auth.ctx.perms:
            await _audit(db, request, auth, "api_key:denied", reason="owner lacks permission")
            raise HTTPException(status.HTTP_403_FORBIDDEN, "key owner lacks the permission")
        await _audit(db, request, auth, f"api_key:{request.method.lower()}")
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


@router.get("/printers/{printer_id}/status", response_model=PrinterStatusOut)
async def printer_status(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_READ),
):
    _visible(auth, printer_id)
    return await printers_api.printer_status(printer_id, db, settings, auth.ctx)


# -- printers: camera + plate thumbnail (read) --------------------------------


def _camera_access(auth: KeyAuth, printer_id: int) -> None:
    """Allowlist 404, then the session camera routes' bar: the key owner
    needs per-printer print access (a read scope alone is not enough)."""
    _visible(auth, printer_id)
    if not auth.ctx.printer_acl[printer_id].can_print:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "printer access denied")


async def _camera_options(db: AsyncSession, settings: Settings, printer_id: int) -> dict:
    """Printer ``options`` (rotation etc.) for a camera route; 503 while the
    printer integration is off, 404 for an unknown printer."""
    if not (await get_app_config(db, settings)).printer_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "printer integration is disabled")
    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    return dict(printer.options or {})


@router.get("/printers/{printer_id}/camera/snapshot")
async def camera_snapshot(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_READ),
) -> Response:
    _camera_access(auth, printer_id)
    options = await _camera_options(db, settings, printer_id)
    return await camera_http.snapshot_response(settings, printer_id, options)


@router.get("/printers/{printer_id}/camera/stream")
async def camera_stream(
    printer_id: int,
    fps: int | None = Query(default=None, ge=1, le=camera_http.MAX_FPS),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_READ),
) -> StreamingResponse:
    _camera_access(auth, printer_id)
    options = await _camera_options(db, settings, printer_id)
    # Release the pooled connection now: the stream outlives the request.
    await db.close()
    return await camera_http.stream_response(settings, printer_id, options, fps)


@router.post("/printers/{printer_id}/camera/token", response_model=CameraUrlsOut)
async def camera_token(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_READ),
) -> CameraUrlsOut:
    """Signed, short-lived stream/snapshot URLs for a cookie-less ``<img>``."""
    _camera_access(auth, printer_id)
    return await printers_api.mint_camera_token(printer_id, db, settings, auth.ctx)


@router.get("/printers/{printer_id}/thumbnail")
async def printer_thumbnail(
    printer_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_READ),
) -> Response:
    """Plate image of the printer's in-flight job; 404 when it has none."""
    _visible(auth, printer_id)
    row = (await active_job_rows(db, [printer_id])).get(printer_id)
    if row is None or row[2] is None or not row[2].rel_path.lower().endswith(".3mf"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no print thumbnail")
    job, _model, file = row
    plate = job.plate if job.plate and job.plate > 0 else 1
    return await blobs_api._conditional_file_response(
        request,
        derivatives.plate_thumb_path(settings, file.blob_hash, plate),
        f"{file.blob_hash}:plate{plate}",
        "image/png",
        cache_control="private, no-cache",
        missing_detail="plate thumbnail not found",
    )


# -- print jobs (read) + direct print (queue) ---------------------------------


@router.get("/print-jobs", response_model=list[PrintJobOut])
async def list_print_jobs(
    printer_id: int | None = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    auth: KeyAuth = Depends(_READ),
) -> list[PrintJobOut]:
    if printer_id is not None:
        _visible(auth, printer_id)
    ids = {printer_id} if printer_id is not None else set(auth.ctx.printer_acl)
    if not ids:
        return []
    stmt = (
        select(PrintJob)
        .where(PrintJob.printer_id.in_(ids))
        .order_by(PrintJob.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [PrintJobOut.from_model(j) for j in (await db.scalars(stmt)).all()]


@router.post(
    "/printers/{printer_id}/print",
    status_code=status.HTTP_201_CREATED,
    response_model=PrintJobOut,
)
async def start_print(
    printer_id: int,
    payload: PrintRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    auth: KeyAuth = Depends(_QUEUE),
) -> PrintJobOut:
    _visible(auth, printer_id)
    auth.ctx.require_printer(printer_id, "print")
    return await printers_api.start_print(printer_id, payload, db, settings, auth.ctx)


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

    out = await queue_service.list_queue(db, settings, auth.ctx, include_done=include_done)
    if auth.key.printer_ids:  # match _guard_entry: unassigned entries are not the key's
        uid = auth.ctx.user.id
        out = [
            e
            for e in out
            if e.printer_id is not None or (e.created_by is not None and e.created_by.id == uid)
        ]
    return out


@router.get("/models/{model_id}/printables")
async def model_printables(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    auth: KeyAuth = Depends(_READ),
) -> list[dict]:
    """Queue-eligible files of a model's current revision: already-sliced
    ``.gcode.3mf`` files (the only kind enqueueable without a slicer recipe).

    Each element maps straight into ``POST /ext/v2/queue``:
    ``{"file_id": el["file_id"], "model_id": <model_id>, "plate": el["plate"],
    "printer_id": <required when the key has a printer allowlist>}`` (the
    ``plate`` key may be omitted when null). ``kind`` is always ``"sliced"``.
    """
    model = await db.get(Model, model_id)
    if model is None or model.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"model {model_id} not found")
    if model.current_revision_id is None:
        return []
    rows = (
        await db.execute(
            select(File)
            .join(Blob, Blob.hash == File.blob_hash)
            .where(
                File.revision_id == model.current_revision_id,
                Blob.format == BlobFormat.GCODE_3MF,
            )
            .order_by(File.rel_path)
        )
    ).scalars()
    return [
        {"file_id": f.id, "name": f.rel_path.rsplit("/", 1)[-1], "kind": "sliced", "plate": None}
        for f in rows
    ]


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
    if (
        auth.key.printer_ids
        and entry.printer_id is None
        and entry.created_by_user_id != auth.ctx.user.id  # same rule as list_queue
    ):
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
    from app.services.imports import start_import_with_options

    imp, created = await start_import_with_options(db, payload, auth.ctx.user.id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return ImportOut.from_model(imp)
