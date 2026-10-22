"""Per-model print history (Branch 5 Task 1): a user-entered log of print
attempts, distinct from the print queue's worklist (``app.api.queue``) and
``print_jobs``' live send-to-printer telemetry (M4).

Model-scoped listing/creation are keyed by the model's numeric id
(``/models/{model_id}/prints``), mirroring ``app.api.revisions``; flat
PATCH/DELETE address a print row directly by its own id.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models.library import Print
from app.schemas.prints import PrintCreateIn, PrintOut, PrintPatchIn
from app.services import library
from app.services import prints as prints_service
from app.services.app_config import get_app_config
from app.services.costing import CostRates

router = APIRouter(tags=["prints"])


@router.post(
    "/models/{model_id}/prints",
    status_code=status.HTTP_201_CREATED,
    response_model=PrintOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def create_print(
    model_id: int,
    payload: PrintCreateIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> PrintOut:
    await library.get_model_by_id(db, model_id)  # raises 404 if unknown
    rates = CostRates.from_config(await get_app_config(db, settings))
    return await prints_service.create_print(
        db,
        model_id,
        printed_at=payload.printed_at,
        printer_name=payload.printer_name,
        filament=payload.filament,
        filament_g=payload.filament_g,
        material_id=payload.material_id,
        result=payload.result,
        duration_min=payload.duration_min,
        notes=payload.notes,
        created_by_user_id=ctx.user.id,
        rates=rates,
    )


@router.get("/models/{model_id}/prints", response_model=list[PrintOut])
async def list_prints(model_id: int, db: AsyncSession = Depends(get_db)) -> list[PrintOut]:
    await library.get_model_by_id(db, model_id)  # raises 404 if unknown
    return await prints_service.list_prints(db, model_id)


@router.patch(
    "/prints/{print_id}",
    response_model=PrintOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def patch_print(
    print_id: int,
    payload: PrintPatchIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> PrintOut:
    row = await db.get(Print, print_id)
    if row is not None:  # unknown id falls through to the service's 404
        require_owner_or_admin(ctx, row.created_by_user_id)
    rates = CostRates.from_config(await get_app_config(db, settings))
    return await prints_service.patch_print(
        db, print_id, payload.model_dump(exclude_unset=True), rates
    )


@router.delete(
    "/prints/{print_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("library:write"))],
)
async def delete_print(
    print_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    row = await db.get(Print, print_id)
    if row is not None:  # unknown id falls through to the service's 404
        require_owner_or_admin(ctx, row.created_by_user_id)
    await prints_service.delete_print(db, print_id)
