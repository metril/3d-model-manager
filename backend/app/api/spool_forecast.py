"""Stock forecast API: per-SKU usage rate, days until empty, reorder point,
queued-work demand, plus the global config and per-SKU overrides. Registered
before ``spools.router`` so ``/spools/forecast`` beats ``/spools/{spool_id}``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.db import get_db
from app.models.inventory import StockSkuSettings
from app.services import stock_forecast_db as fdb

router = APIRouter(prefix="/spools/forecast", tags=["spools"])


class SkuRowOut(BaseModel):
    material: str
    brand: str | None
    color: str | None
    spool_count: int
    remaining_g: float
    demand_g: float
    daily_rate_g: float | None
    days_remaining: int | None
    days_until_reorder: int | None
    reorder_point_g: float
    lead_time_days: int
    status: str
    snoozed: bool


class DemandOut(BaseModel):
    material: str
    grams: float


class ShortfallOut(BaseModel):
    material: str
    demand_g: float
    stock_g: float


class ForecastOut(BaseModel):
    config: fdb.ForecastConfig
    skus: list[SkuRowOut]
    queue_demand: list[DemandOut]
    queue_shortfalls: list[ShortfallOut]
    alert_count: int
    generated_at: str


class ConfigPatch(BaseModel):
    enabled: bool | None = None
    lead_time_days: int | None = Field(None, ge=0, le=365)
    safety_margin_value: float | None = Field(None, ge=0, le=100000)
    safety_margin_unit: str | None = Field(None, pattern="^(days|g)$")
    include_queue: bool | None = None
    notify: bool | None = None


class SkuPatch(BaseModel):
    material: str = Field(min_length=1)
    brand: str | None = None
    color: str | None = None
    lead_time_days: int | None = Field(None, ge=0, le=365)
    safety_margin_value: float | None = Field(None, ge=0, le=100000)
    safety_margin_unit: str | None = Field(None, pattern="^(days|g)$")
    alerts_snoozed: bool | None = None


@router.get("", response_model=ForecastOut)
async def get_forecast(
    db: AsyncSession = Depends(get_db), ctx: AuthContext = Depends(require_session)
) -> dict:
    return await db.run_sync(fdb.build_report)


@router.put(
    "/config",
    response_model=fdb.ForecastConfig,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def put_config(body: ConfigPatch, db: AsyncSession = Depends(get_db)) -> fdb.ForecastConfig:
    return await db.run_sync(lambda s: fdb.set_config(s, body.model_dump(exclude_none=True)))


@router.put(
    "/sku",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def put_sku(
    body: SkuPatch,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> Response:
    brand, color = body.brand or "", body.color or ""
    row = (
        await db.execute(
            select(StockSkuSettings).where(
                StockSkuSettings.material_type == body.material,
                StockSkuSettings.brand == brand,
                StockSkuSettings.color_key == color,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = StockSkuSettings(
            material_type=body.material,
            brand=brand,
            color_key=color,
            created_by_user_id=ctx.user.id,
        )
        db.add(row)
    for k, v in body.model_dump(exclude={"material", "brand", "color"}, exclude_none=True).items():
        setattr(row, k, v)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
