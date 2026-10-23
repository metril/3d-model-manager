"""Virtual printer CRUD (Q11). Every route, GETs included, needs
``settings:write``: the response carries the plaintext access code."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.config import Settings, get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import get_db
from app.models import Printer, VirtualPrinter
from app.models.enums import VirtualPrinterMode
from app.schemas.virtual_printers import (
    VirtualPrinterIn,
    VirtualPrinterOut,
    VirtualPrinterPatchIn,
)
from app.virtual_printer.catalog import MODELS, new_access_code, new_serial

router = APIRouter(
    prefix="/virtual-printers",
    tags=["virtual-printers"],
    dependencies=[Depends(require_perm("settings:write"))],
)


def _out(settings: Settings, row: VirtualPrinter) -> VirtualPrinterOut:
    data = {c: getattr(row, c) for c in VirtualPrinterOut.model_fields if c != "access_code"}
    return VirtualPrinterOut(**data, access_code=decrypt_secret(settings, row.access_code_enc))


async def _validate(
    db: AsyncSession, model: str, mode: VirtualPrinterMode, target: int | None
) -> int | None:
    if model not in MODELS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"unknown model {model!r}; use one of {sorted(MODELS)}",
        )
    if mode is VirtualPrinterMode.PROXY:
        if target is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "proxy mode needs a target_printer_id"
            )
        if await db.get(Printer, target) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "target printer not found")
        return target
    return None


async def _get(db: AsyncSession, vp_id: int) -> VirtualPrinter:
    row = await db.get(VirtualPrinter, vp_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "virtual printer not found")
    return row


@router.get("", response_model=list[VirtualPrinterOut])
async def list_virtual_printers(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> list[VirtualPrinterOut]:
    rows = (await db.scalars(select(VirtualPrinter).order_by(VirtualPrinter.id))).all()
    return [_out(settings, r) for r in rows]


@router.post("", response_model=VirtualPrinterOut, status_code=status.HTTP_201_CREATED)
async def create_virtual_printer(
    body: VirtualPrinterIn,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VirtualPrinterOut:
    target = await _validate(db, body.model, body.mode, body.target_printer_id)
    row = VirtualPrinter(
        name=body.name,
        model=body.model,
        serial=new_serial(body.model),
        access_code_enc=encrypt_secret(settings, new_access_code()),
        mode=body.mode,
        target_printer_id=target,
        enabled=body.enabled,
        created_by_user_id=ctx.user.id,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:  # serial collision: astronomically unlikely
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "serial collision; retry") from exc
    await db.refresh(row)
    return _out(settings, row)


@router.get("/{vp_id}", response_model=VirtualPrinterOut)
async def get_virtual_printer(
    vp_id: int, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> VirtualPrinterOut:
    return _out(settings, await _get(db, vp_id))


@router.patch("/{vp_id}", response_model=VirtualPrinterOut)
async def update_virtual_printer(
    vp_id: int,
    body: VirtualPrinterPatchIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VirtualPrinterOut:
    row = await _get(db, vp_id)
    fields = body.model_fields_set
    model = body.model or row.model
    mode = body.mode or row.mode
    target = body.target_printer_id if "target_printer_id" in fields else row.target_printer_id
    row.target_printer_id = await _validate(db, model, mode, target)
    if model != row.model:
        row.model = model
        row.serial = new_serial(model)  # a new model is a new device to slicers
    row.mode = mode
    if body.name is not None:
        row.name = body.name
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.regenerate_access_code:
        row.access_code_enc = encrypt_secret(settings, new_access_code())
    await db.commit()
    await db.refresh(row)
    return _out(settings, row)


@router.delete("/{vp_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_virtual_printer(vp_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    await db.delete(await _get(db, vp_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
