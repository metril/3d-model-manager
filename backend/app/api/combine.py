"""``POST /models/{slug}/combine``: put copies of several mesh files on one
bed as a new multi-object 3MF on the model's current revision."""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path

import anyio
import blake3
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import SlicerProfile
from app.models.enums import BlobFormat, BlobKind, ProfileKind
from app.schemas.combine import BedIn, CombineIn, CombineOut, CombinePlacementOut
from app.schemas.jobs import JobOut
from app.services import jobs as jobs_service
from app.services import library, spool
from app.services.mesh_combine import (
    CombinePart,
    LayoutPlan,
    MeshCombineError,
    parse_printable_area,
    plan_parts,
    write_3mf,
)
from app.services.storage_backends import resolve_backend_for_file
from app.storage.base import StorageBackend
from app.tasks.ingest import store_to_backend

router = APIRouter(prefix="/models", tags=["models"])

_COMBINE_FORMATS = {BlobFormat.STL, BlobFormat.OBJ, BlobFormat.THREEMF}


def _copy_to_temp(backend: StorageBackend, storage_path: str, directory: Path, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(dir=directory, suffix=suffix)
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as fh:
            for chunk in backend.read(storage_path):
                fh.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


@router.post(
    "/{slug}/combine",
    response_model=CombineOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def combine_files(
    slug: str,
    payload: CombineIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> CombineOut:
    """Arrange copies of the given mesh files on one bed and store the result
    as a new multi-object 3MF on the model's current revision (through the
    same spool -> ``store_to_backend`` job path as an upload). With
    ``dry_run`` only the plan is returned. 422 when the copies don't fit the
    bed (``detail.required_bed`` says what they'd need)."""
    model = await library.get_model_by_slug(db, slug)
    require_owner_or_admin(ctx, model.created_by_user_id)
    if model.current_revision_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "model has no current revision")
    revision = await library.get_revision_or_404(db, model.current_revision_id)

    bed = payload.bed
    if payload.printer_profile_id is not None:
        profile = await db.get(SlicerProfile, payload.printer_profile_id)
        if profile is None or profile.kind is not ProfileKind.PRINTER:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "printer profile not found")
        dims = parse_printable_area(profile.data)
        if dims is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "profile has no printable area"
            )
        bed = BedIn(x_mm=dims[0], y_mm=dims[1])
    if bed is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "give a printer_profile_id or bed"
        )

    by_id = {f.id: f for f in revision.files}
    rows = []
    for item in payload.items:
        file = by_id.get(item.file_id)
        if file is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"file {item.file_id} is not on the current revision"
            )
        if file.blob.format not in _COMBINE_FORMATS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{file.rel_path} is not an STL/OBJ/3MF mesh"
            )
        if file.verified_at is None:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{file.rel_path} is still processing")
        rows.append((file, item.copies, await resolve_backend_for_file(db, settings, file)))

    spool_root = await anyio.to_thread.run_sync(spool.ensure_spool_dir, settings)
    token = uuid.uuid4()
    out_path = spool.spool_path(settings, token)
    temps: list[Path] = []

    def _work() -> tuple[LayoutPlan, str, int]:
        parts = []
        for file, copies, backend in rows:
            tmp = _copy_to_temp(backend, file.storage_path, spool_root, Path(file.rel_path).suffix)
            temps.append(tmp)
            parts.append(CombinePart(name=Path(file.rel_path).name, path=tmp, copies=copies))
        loaded, plan = plan_parts(
            parts, (bed.x_mm, bed.y_mm), mode=payload.layout, spacing=payload.spacing_mm
        )
        if payload.dry_run or not plan.fits:
            return plan, "", 0
        data = write_3mf(loaded, plan)
        out_path.write_bytes(data)
        return plan, blake3.blake3(data).hexdigest(), len(data)

    try:
        try:
            plan, blob_hash, size = await anyio.to_thread.run_sync(_work)
        except MeshCombineError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        finally:
            for tmp in temps:
                tmp.unlink(missing_ok=True)

        required = BedIn(x_mm=max(plan.required_bed[0], 0.01), y_mm=max(plan.required_bed[1], 0.01))
        placements = [
            CombinePlacementOut(file_id=rows[p.owner][0].id, x_mm=p.x, y_mm=p.y, w_mm=p.w, d_mm=p.d)
            for p in plan.placements
        ]
        if payload.dry_run:
            return CombineOut(fits=plan.fits, bed=bed, required_bed=required, placements=placements)
        if not plan.fits:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "message": (
                        f"The models do not fit a {bed.x_mm:g} x {bed.y_mm:g} mm bed; "
                        f"they need about {plan.required_bed[0]:g} x {plan.required_bed[1]:g} mm"
                    ),
                    "required_bed": {"x_mm": plan.required_bed[0], "y_mm": plan.required_bed[1]},
                },
            )

        stem = re.sub(r"[^\w .()-]", "", (payload.name or "").strip())[:120] or "combined"
        taken = {f.rel_path for f in revision.files}
        rel_path, n = f"{stem}.3mf", 1
        while rel_path in taken:
            n += 1
            rel_path = f"{stem} ({n}).3mf"

        file = await library.finalize_upload(
            db,
            model=model,
            revision=revision,
            rel_path=rel_path,
            blob_hash=blob_hash,
            size=size,
            kind=BlobKind.MESH,
            format_=BlobFormat.THREEMF,
            replace=False,
            created_by_user_id=ctx.user.id,
        )
        job = await jobs_service.create_job(
            db, id=token, type="store_to_backend", subject_type="file", subject_id=file.id
        )
    except BaseException:
        await anyio.to_thread.run_sync(lambda: out_path.unlink(missing_ok=True))
        raise

    file_id, revision_id = file.id, revision.id
    store_to_backend.apply_async(args=[str(job.id), file_id, str(out_path)], task_id=str(job.id))
    # Eager Celery (tests) ran the store through its own session; drop stale state.
    db.expire_all()
    revision = await library.get_revision_or_404(db, revision_id)
    detail = await library.build_revision_detail(db, revision, settings)
    file_out = next((f for f in detail.files if f.id == file_id), None)
    await db.refresh(job)
    return CombineOut(
        fits=True,
        bed=bed,
        required_bed=required,
        placements=placements,
        file=file_out,
        job=JobOut.from_model(job),
    )
