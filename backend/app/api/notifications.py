"""Notification channels, templates and log (Q6). Management routes need
``settings:write``; the event catalog only needs a session."""

from __future__ import annotations

from dataclasses import asdict

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from jinja2 import TemplateError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models.notify import NotificationChannel, NotificationLog, NotificationTemplate
from app.schemas.notifications import (
    ChannelIn,
    ChannelOut,
    ChannelPatch,
    LogOut,
    LogPage,
    NotifySettingsOut,
    NotifySettingsPatch,
    PreviewOut,
    TemplateIn,
    TemplateOut,
    TemplatePatch,
    TestResult,
)
from app.services.notify import PROVIDERS, Message, ProviderError, hms_dedup
from app.services.notify import catalog as cat
from app.services.notify.delivery import (
    dump_config,
    load_config,
    mask_config,
    merge_config,
    send_message,
    validate_cron,
)
from app.services.notify.templates import base_context, render_strict

public_router = APIRouter(prefix="/notifications", tags=["notifications"])
router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_perm("settings:write"))],
)


def _out(settings: Settings, row: NotificationChannel) -> ChannelOut:
    data = {f: getattr(row, f) for f in ChannelOut.model_fields if f != "config"}
    return ChannelOut(**data, config=mask_config(row.kind, load_config(settings, row)))


def _check(kind: str, events: list[str] | None, cron: str | None, config: dict | None) -> None:
    if kind not in PROVIDERS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown kind {kind!r}")
    unknown = [e for e in events or [] if e not in cat.EVENT_IDS]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown events {unknown}")
    if cron and not validate_cron(cron):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid digest_cron")
    if config is not None:
        missing = [
            f["name"] for f in PROVIDERS[kind].fields if f["required"] and not config.get(f["name"])
        ]
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"missing required config: {missing}"
            )


async def _channel(db: AsyncSession, channel_id: int) -> NotificationChannel:
    row = await db.get(NotificationChannel, channel_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel not found")
    return row


@public_router.get("/events")
async def events_catalog(_: AuthContext = Depends(require_session)) -> dict:
    return {
        "events": cat.catalog(),
        "kinds": [
            {
                "kind": p.kind,
                "label": p.label,
                "supports_photo": p.supports_photo,
                "fields": p.fields,
            }
            for p in PROVIDERS.values()
        ],
        "template_vars": cat.TEMPLATE_VARS,
    }


@router.get("/settings", response_model=NotifySettingsOut)
async def get_notify_settings(db: AsyncSession = Depends(get_db)) -> NotifySettingsOut:
    return NotifySettingsOut(**asdict(await hms_dedup.get_settings_async(db)))


@router.patch("/settings", response_model=NotifySettingsOut)
async def patch_notify_settings(
    body: NotifySettingsPatch, db: AsyncSession = Depends(get_db)
) -> NotifySettingsOut:
    cfg = await hms_dedup.set_settings(db, body.model_dump(exclude_none=True))
    return NotifySettingsOut(**asdict(cfg))


@router.get("/channels", response_model=list[ChannelOut])
async def list_channels(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> list[ChannelOut]:
    rows = await db.scalars(select(NotificationChannel).order_by(NotificationChannel.id))
    return [_out(settings, r) for r in rows]


@router.post("/channels", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: ChannelIn,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ChannelOut:
    _check(body.kind, body.events, body.digest_cron, body.config)
    row = NotificationChannel(
        kind=body.kind,
        name=body.name,
        config_enc=dump_config(settings, merge_config(body.kind, {}, body.config)),
        enabled=body.enabled,
        events=body.events,
        quiet_hours=body.quiet_hours.model_dump() if body.quiet_hours else None,
        digest_cron=body.digest_cron or None,
        attach_photo=body.attach_photo,
        created_by_user_id=ctx.user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _out(settings, row)


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: int,
    body: ChannelPatch,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ChannelOut:
    row = await _channel(db, channel_id)
    fields = body.model_fields_set
    config = None
    if body.config is not None:
        config = merge_config(row.kind, load_config(settings, row), body.config)
    _check(row.kind, body.events, body.digest_cron, config)
    if config is not None:
        row.config_enc = dump_config(settings, config)
    if body.name is not None:
        row.name = body.name
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.events is not None:
        row.events = body.events
    if "quiet_hours" in fields:
        row.quiet_hours = body.quiet_hours.model_dump() if body.quiet_hours else None
    if "digest_cron" in fields:
        row.digest_cron = body.digest_cron or None
    if body.attach_photo is not None:
        row.attach_photo = body.attach_photo
    await db.commit()
    await db.refresh(row)
    return _out(settings, row)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    await db.delete(await _channel(db, channel_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/channels/{channel_id}/test", response_model=TestResult)
async def test_channel(
    channel_id: int,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TestResult:
    row = await _channel(db, channel_id)
    msg = Message("Test notification", "This is a test from 3D Model Manager.", "test")
    error: str | None = None
    try:
        await anyio.to_thread.run_sync(lambda: send_message(row, msg, settings=settings))
    except ProviderError as exc:
        error = str(exc)
    db.add(
        NotificationLog(
            channel_id=row.id,
            event="test",
            status="failed" if error else "sent",
            error=error,
            created_by_user_id=ctx.user.id,
        )
    )
    await db.commit()
    return TestResult(ok=error is None, error=error)


# -- templates -------------------------------------------------------------


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(db: AsyncSession = Depends(get_db)) -> list[NotificationTemplate]:
    return list(await db.scalars(select(NotificationTemplate).order_by(NotificationTemplate.id)))


@router.get("/templates/defaults")
async def template_defaults() -> dict:
    return {
        e: dict(zip(("title_tpl", "body_tpl"), cat.default_template(e), strict=True))
        for e in cat.EVENT_IDS
    }


def _validate_tpl(event: str, title: str, body: str) -> None:
    try:
        render_strict(title, body, base_context(event, None, "Printer", cat.sample_payload(event)))
    except TemplateError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"template: {exc}") from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"template: {type(exc).__name__}"
        ) from exc


@router.post("/templates/preview", response_model=PreviewOut)
async def preview_template(body: TemplateIn) -> PreviewOut:
    if body.event not in cat.EVENT_IDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown event")
    ctx = base_context(body.event, None, "A1 Mini", cat.sample_payload(body.event))
    try:
        title, text = render_strict(body.title_tpl, body.body_tpl, ctx)
    except Exception as exc:
        return PreviewOut(title="", body="", error=str(exc) or type(exc).__name__)
    return PreviewOut(title=title, body=text)


@router.post("/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateIn,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> NotificationTemplate:
    if body.event not in cat.EVENT_IDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown event")
    _validate_tpl(body.event, body.title_tpl, body.body_tpl)
    row = NotificationTemplate(
        event=body.event,
        title_tpl=body.title_tpl,
        body_tpl=body.body_tpl,
        created_by_user_id=ctx.user.id,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "event already has a template") from exc
    await db.refresh(row)
    return row


@router.patch("/templates/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: int, body: TemplatePatch, db: AsyncSession = Depends(get_db)
) -> NotificationTemplate:
    row = await db.get(NotificationTemplate, template_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    title = body.title_tpl if body.title_tpl is not None else row.title_tpl
    text = body.body_tpl if body.body_tpl is not None else row.body_tpl
    _validate_tpl(row.event, title, text)
    row.title_tpl, row.body_tpl = title, text
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    row = await db.get(NotificationTemplate, template_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- log -------------------------------------------------------------------


@router.get("/log", response_model=LogPage)
async def notification_log(
    status_filter: str | None = Query(default=None, alias="status"),
    channel_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> LogPage:
    conds = []
    if status_filter:
        conds.append(NotificationLog.status == status_filter)
    if channel_id is not None:
        conds.append(NotificationLog.channel_id == channel_id)
    total = await db.scalar(select(func.count()).select_from(NotificationLog).where(*conds)) or 0
    rows = await db.execute(
        select(NotificationLog, NotificationChannel.name)
        .join(
            NotificationChannel, NotificationChannel.id == NotificationLog.channel_id, isouter=True
        )
        .where(*conds)
        .order_by(NotificationLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [
        LogOut(
            id=r.id,
            channel_id=r.channel_id,
            channel_name=name,
            event=r.event,
            printer_id=r.printer_id,
            status=r.status,
            error=r.error,
            sent_at=r.sent_at,
        )
        for r, name in rows
    ]
    return LogPage(items=items, total=total)
