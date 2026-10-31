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
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import File, SlicerProfile
from app.models.enums import BlobFormat, BlobKind, ProfileKind
from app.schemas.combine import (
    BedIn,
    CombineIn,
    CombineOut,
    CombinePartOut,
    CombinePlacementOut,
    CombinePoseOut,
)
from app.schemas.jobs import JobOut
from app.services import jobs as jobs_service
from app.services import library, spool
from app.services.bed_fit import parse_printable_area
from app.services.mesh_combine import (
    FIT_MARGIN_MM,
    CombinePart,
    LoadedPart,
    MeshCombineError,
    Pose,
    PoseFit,
    check_poses,
    load_parts,
    placements_to_poses,
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
    auto: bool = Query(False, description="Plan only: run grid/auto and return poses"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> CombineOut:
    """Arrange copies of the given mesh files on one bed and store the result
    as a new multi-object 3MF on the model's current revision (through the
    same spool -> ``store_to_backend`` job path as an upload). With
    ``dry_run`` only the plan is returned. 422 when the copies don't fit the
    bed (``detail.required_bed`` says what they'd need). ``layout: "manual"``
    takes one pose per item instead (see ``mesh_combine.Pose``); a pose off the
    bed is a 422 ``does_not_fit`` naming the offending item indices. ``?auto=1``
    implies ``dry_run`` and returns the grid/auto poses for the plate editor."""
    dry_run = payload.dry_run or auto
    manual = payload.layout == "manual"
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
    rows: list[tuple[File, int, StorageBackend]] = []
    row_of: dict[int, int] = {}  # manual: file id -> index into rows
    for item in payload.items:
        if manual and item.file_id in row_of:
            i = row_of[item.file_id]
            rows[i] = (rows[i][0], rows[i][1] + 1, rows[i][2])
            continue
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
        row_of[item.file_id] = len(rows)
        rows.append(
            (file, 1 if manual else item.copies, await resolve_backend_for_file(db, settings, file))
        )

    spool_root = await anyio.to_thread.run_sync(spool.ensure_spool_dir, settings)
    token = uuid.uuid4()
    out_path = spool.spool_path(settings, token)
    temps: list[Path] = []

    def _work() -> tuple[
        list[LoadedPart], list[Pose], list[PoseFit], tuple[float, float], str, int
    ]:
        parts = []
        for file, copies, backend in rows:
            tmp = _copy_to_temp(backend, file.storage_path, spool_root, Path(file.rel_path).suffix)
            temps.append(tmp)
            parts.append(CombinePart(name=Path(file.rel_path).name, path=tmp, copies=copies))
        bed_t = (bed.x_mm, bed.y_mm)
        if manual:
            loaded = load_parts(parts)
            poses = [
                Pose(row_of[i.file_id], i.x_mm or 0.0, i.y_mm or 0.0, i.rot_z_deg, i.scale)
                for i in payload.items
            ]
            fits = check_poses(loaded, poses, bed_t)
            all_fit = all(f.fits for f in fits)
            required = (
                round(max(max(f.max_x for f in fits), 0.0) + FIT_MARGIN_MM / 2, 2),
                round(max(max(f.max_y for f in fits), 0.0) + FIT_MARGIN_MM / 2, 2),
            )
        else:
            loaded, plan = plan_parts(parts, bed_t, mode=payload.layout, spacing=payload.spacing_mm)
            poses = placements_to_poses(plan)
            fits = [PoseFit(p.x, p.y, p.x + p.w, p.y + p.d, plan.fits) for p in plan.placements]
            all_fit, required = plan.fits, plan.required_bed
        if dry_run or not all_fit:
            return loaded, poses, fits, required, "", 0
        data = write_3mf(loaded, poses=poses) if manual else write_3mf(loaded, plan)
        out_path.write_bytes(data)
        return loaded, poses, fits, required, blake3.blake3(data).hexdigest(), len(data)

    try:
        try:
            loaded, poses, fits, required_t, blob_hash, size = await anyio.to_thread.run_sync(_work)
        except MeshCombineError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        finally:
            for tmp in temps:
                tmp.unlink(missing_ok=True)

        all_fit = all(f.fits for f in fits)
        required = BedIn(x_mm=max(required_t[0], 0.01), y_mm=max(required_t[1], 0.01))
        placements = [
            CombinePlacementOut(
                file_id=rows[po.owner][0].id,
                x_mm=f.min_x,
                y_mm=f.min_y,
                w_mm=f.max_x - f.min_x,
                d_mm=f.max_y - f.min_y,
            )
            for po, f in zip(poses, fits, strict=True)
        ]
        seen: dict[int, int] = {}
        pose_out = []
        for idx, (po, f) in enumerate(zip(poses, fits, strict=True)):
            n = seen.get(po.owner, 0)
            seen[po.owner] = n + 1
            pose_out.append(
                CombinePoseOut(
                    file_id=rows[po.owner][0].id,
                    x_mm=po.x,
                    y_mm=po.y,
                    rot_z_deg=po.rot_deg,
                    scale=po.scale,
                    copy_index=payload.items[idx].copy_index if manual else n,
                    fits=f.fits,
                )
            )
        part_out = [
            CombinePartOut(
                file_id=rows[i][0].id,
                min_x=lp.origin[0],
                min_y=lp.origin[1],
                min_z=lp.origin[2],
                w_mm=lp.width,
                d_mm=lp.depth,
                h_mm=lp.height,
            )
            for i, lp in enumerate(loaded)
        ]
        if dry_run:
            return CombineOut(
                fits=all_fit,
                bed=bed,
                required_bed=required,
                placements=placements,
                poses=pose_out,
                parts=part_out,
            )
        if not all_fit:
            bad = [i for i, f in enumerate(fits) if not f.fits]
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "does_not_fit",
                    "message": (
                        f"The models do not fit a {bed.x_mm:g} x {bed.y_mm:g} mm bed; "
                        f"they need about {required_t[0]:g} x {required_t[1]:g} mm"
                    ),
                    "required_bed": {"x_mm": required_t[0], "y_mm": required_t[1]},
                    "items": bad if manual else [],
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
    try:
        store_to_backend.apply_async(
            args=[str(job.id), file_id, str(out_path)], task_id=str(job.id)
        )
    except Exception:
        # Broker down: drop the never-stored file row and the spooled bytes.
        job.state = jobs_service.STATE_FAILED
        job.error = "failed to enqueue store_to_backend"
        orphan = await db.get(File, file_id)
        if orphan is not None:
            await db.delete(orphan)
        await db.commit()
        await anyio.to_thread.run_sync(lambda: out_path.unlink(missing_ok=True))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "could not queue the combine; retry"
        ) from None
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
        poses=pose_out,
        parts=part_out,
        file=file_out,
        job=JobOut.from_model(job),
    )
