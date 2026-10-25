"""V4 projects: BOM, progress/cost rollups, timeline, templates, export and
"queue remaining". Reads need a session; writes need ``library:write`` (same
as followed collections); queueing additionally needs ``printers:print``.

Literal segments (``/templates``, ``/from-template``) are declared BEFORE
``/{project_id}`` so they aren't parsed as an id.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas import projects as s
from app.services import projects as svc
from app.services import projects_export, zip_export
from app.services.http_names import content_disposition_attachment

router = APIRouter(prefix="/projects", tags=["projects"])
_W = [Depends(require_perm("library:write"))]


@router.get("", response_model=list[s.ProjectSummary])
async def list_projects(
    status_: Annotated[s.ProjectStatus | None, Query(alias="status")] = None,
    q: str | None = None,
    tag: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[s.ProjectSummary]:
    return await svc.list_projects(db, status_=status_, q=q, template=False, tag=tag)


@router.post("", status_code=201, response_model=s.ProjectOut, dependencies=_W)
async def create_project(
    payload: s.ProjectIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> s.ProjectOut:
    return await svc.to_out(db, await svc.create_project(db, ctx, payload))


@router.get("/templates", response_model=list[s.ProjectSummary])
async def list_templates(db: AsyncSession = Depends(get_db)) -> list[s.ProjectSummary]:
    return await svc.list_projects(db, status_=None, q=None, template=True, tag=None)


@router.post(
    "/from-template/{template_id}", status_code=201, response_model=s.ProjectOut, dependencies=_W
)
async def from_template(
    template_id: int,
    payload: s.FromTemplateIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> s.ProjectOut:
    return await svc.to_out(db, await svc.from_template(db, ctx, template_id, payload))


@router.get("/{project_id}", response_model=s.ProjectOut)
async def get_project(project_id: int, db: AsyncSession = Depends(get_db)) -> s.ProjectOut:
    return await svc.to_out(db, await svc.get_or_404(db, project_id))


@router.patch("/{project_id}", response_model=s.ProjectOut, dependencies=_W)
async def patch_project(
    project_id: int,
    payload: s.ProjectPatch,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> s.ProjectOut:
    return await svc.to_out(db, await svc.patch_project(db, ctx, project_id, payload))


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_W)
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> None:
    await svc.delete_project(db, settings, ctx, project_id)


# ---- BOM


@router.post("/{project_id}/items", status_code=201, response_model=s.ItemOut, dependencies=_W)
async def add_item(
    project_id: int,
    payload: s.ItemIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> s.ItemOut:
    item = await svc.add_item(db, ctx, project_id, payload)
    return await svc.item_out(db, project_id, item.id)


@router.post("/{project_id}/models", response_model=s.ProjectOut, dependencies=_W)
async def add_models(
    project_id: int,
    payload: s.AddModelsIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> s.ProjectOut:
    """Library multi-select / model page "add to project"."""
    await svc.add_models(db, ctx, project_id, payload)
    return await svc.to_out(db, await svc.get_or_404(db, project_id))


@router.patch("/{project_id}/items/{item_id}", response_model=s.ItemOut, dependencies=_W)
async def patch_item(
    project_id: int,
    item_id: int,
    payload: s.ItemPatch,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> s.ItemOut:
    await svc.patch_item(db, ctx, project_id, item_id, payload)
    return await svc.item_out(db, project_id, item_id)


@router.delete(
    "/{project_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_W
)
async def delete_item(
    project_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    await svc.delete_item(db, ctx, project_id, item_id)


# ---- archives / timeline


@router.get("/{project_id}/archives", response_model=list[s.ProjectArchiveOut])
async def list_archives(
    project_id: int, db: AsyncSession = Depends(get_db)
) -> list[s.ProjectArchiveOut]:
    return await svc.list_archives(db, project_id)


@router.post("/{project_id}/archives", dependencies=_W)
async def link_archives(
    project_id: int,
    payload: s.ArchiveIdsIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> dict[str, int]:
    return {
        "linked": await svc.link_archives(db, ctx, project_id, payload.archive_ids, unlink=False)
    }


@router.post("/{project_id}/archives/remove", dependencies=_W)
async def unlink_archives(
    project_id: int,
    payload: s.ArchiveIdsIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> dict[str, int]:
    return {
        "unlinked": await svc.link_archives(db, ctx, project_id, payload.archive_ids, unlink=True)
    }


@router.get("/{project_id}/timeline", response_model=list[s.TimelineEntry])
async def get_timeline(
    project_id: int, db: AsyncSession = Depends(get_db)
) -> list[s.TimelineEntry]:
    return await svc.timeline(db, project_id)


# ---- attachments


@router.post(
    "/{project_id}/attachments", status_code=201, response_model=s.AttachmentOut, dependencies=_W
)
async def upload_attachment(
    project_id: int,
    request: Request,
    filename: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> s.AttachmentOut:
    """Raw-body upload (``?filename=``), like the model cover endpoint."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > svc.MAX_ATTACHMENT:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "attachment too large")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > svc.MAX_ATTACHMENT:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "attachment too large")
    a = await svc.add_attachment(
        db, settings, ctx, project_id, filename, request.headers.get("content-type"), bytes(data)
    )
    return s.AttachmentOut(
        id=a.id,
        filename=a.filename,
        content_type=a.content_type,
        size=a.size,
        created_at=a.created_at,
    )


@router.get("/{project_id}/attachments/{attachment_id}")
async def download_attachment(
    project_id: int,
    attachment_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    a, path = await svc.get_attachment(db, settings, project_id, attachment_id)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={"Content-Disposition": content_disposition_attachment(a.filename)},
    )


@router.delete(
    "/{project_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_W,
)
async def delete_attachment(
    project_id: int,
    attachment_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> None:
    await svc.delete_attachment(db, settings, ctx, project_id, attachment_id)


# ---- templates / queue / export


@router.post(
    "/{project_id}/create-template", status_code=201, response_model=s.ProjectOut, dependencies=_W
)
async def create_template(
    project_id: int,
    payload: s.TemplateIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> s.ProjectOut:
    return await svc.to_out(db, await svc.create_template(db, ctx, project_id, payload.name))


@router.post(
    "/{project_id}/queue-remaining",
    response_model=s.QueueRemainingOut,
    dependencies=[Depends(require_perm("printers:print"))],
)
async def queue_remaining(
    project_id: int,
    payload: s.QueueRemainingIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> s.QueueRemainingOut:
    """Enqueue every outstanding part (target - done - already queued)."""
    return await svc.queue_remaining(db, settings, ctx, project_id, payload)


@router.get("/{project_id}/export")
async def export_project(
    project_id: int,
    format: str = "json",
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    p = await svc.get_or_404(db, project_id)
    base = zip_export._safe_path_segment(p.name, fallback=f"project-{p.id}")
    if format == "json":
        body, media, ext = svc.export_json(await svc.to_out(db, p)), "application/json", "json"
    elif format == "csv":
        body, media, ext = svc.bom_csv(await svc.to_out(db, p)), "text/csv", "csv"
    elif format == "zip":
        gen = projects_export.iter_project_zip(db, settings, p)
        return StreamingResponse(
            gen,
            media_type="application/zip",
            headers={"Content-Disposition": content_disposition_attachment(f"{base}.zip")},
        )
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "format must be json|csv|zip")
    return Response(
        body,
        media_type=media,
        headers={"Content-Disposition": content_disposition_attachment(f"{base}.{ext}")},
    )
