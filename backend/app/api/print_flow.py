"""Print plan / send flow (P5 spool tray plan, Task 7): ``POST /print/plan``
previews filament/AMS matching + cost before a print is sent;
``POST /print/send`` actually enqueues it (shares ``create_job`` with the
legacy ``POST /printers/{id}/print`` route -- see ``app.services.print_plan``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_printer_enabled, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas.print_flow import PlanIn, PlanOut, SendIn
from app.schemas.printers import PrintJobOut
from app.services.print_plan import build_plan, create_job
from app.services.redis_pool import get_async_redis

router = APIRouter(prefix="/print", tags=["print"], dependencies=[Depends(require_printer_enabled)])


@router.post("/plan", response_model=PlanOut)
async def plan_print(
    payload: PlanIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> PlanOut:
    client = get_async_redis(settings.redis_url)
    try:
        return await build_plan(db, settings, client, ctx, payload)
    finally:
        await client.aclose()


@router.post(
    "/send",
    status_code=201,
    response_model=PrintJobOut,
    dependencies=[Depends(require_perm("printers:print"))],
)
async def send_print(
    payload: SendIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> PrintJobOut:
    ctx.require_printer(payload.printer_id, "print")
    job = await create_job(db, settings, ctx, payload.printer_id, payload)
    return PrintJobOut.from_model(job)
