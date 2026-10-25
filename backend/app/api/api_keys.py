"""Session-gated management of scoped API keys (``/settings/api-keys``). The
keys themselves authenticate on ``/ext/v2`` (``app.api.ext_v2``). Mint returns
the plaintext once; list never carries it or its hash. A key can only be
granted scopes its creator's permissions cover."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_session
from app.db import get_db
from app.models import ApiToken, Printer, User
from app.schemas.imports import NonEmptyStr
from app.services import api_tokens

router = APIRouter(prefix="/settings", tags=["settings"])

Scope = Literal["read", "control", "queue", "upload"]


class ApiKeyCreateIn(BaseModel):
    label: NonEmptyStr
    scopes: list[Scope] = Field(min_length=1)
    printer_ids: list[int] | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=api_tokens.MAX_RATE_LIMIT)


class ApiKeyOut(BaseModel):
    id: int
    label: str
    key_prefix: str | None
    scopes: list[str]
    printer_ids: list[int] | None
    expires_at: datetime | None
    rate_limit_per_min: int
    created_at: datetime
    last_used_at: datetime | None
    user_id: int
    username: str

    @classmethod
    def from_model(cls, row: ApiToken, username: str) -> ApiKeyOut:
        return cls(
            id=row.id,
            label=row.label,
            key_prefix=row.key_prefix,
            scopes=list(row.scopes or []),
            printer_ids=row.printer_ids,
            expires_at=row.expires_at,
            rate_limit_per_min=row.rate_limit_per_min or api_tokens.DEFAULT_RATE_LIMIT,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            user_id=row.user_id,
            username=username,
        )


class ApiKeyMintOut(ApiKeyOut):
    key: str  # shown exactly once


@router.post("/api-keys", status_code=status.HTTP_201_CREATED, response_model=ApiKeyMintOut)
async def create_api_key(
    payload: ApiKeyCreateIn,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_session),
) -> ApiKeyMintOut:
    for scope in payload.scopes:
        missing = api_tokens.SCOPE_PERMS[scope] - auth.perms
        if missing:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"scope '{scope}' needs permission: {', '.join(sorted(missing))}",
            )
    if payload.printer_ids:
        ids = set(payload.printer_ids)
        known = set((await db.execute(select(Printer.id).where(Printer.id.in_(ids)))).scalars())
        unknown = ids - known
        reachable = ids if auth.is_admin else ids & set(auth.printer_acl)
        if unknown or ids - reachable:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "printer_ids contains an unknown printer"
            )
    expires_at = (
        datetime.now(UTC) + timedelta(days=payload.expires_in_days)
        if payload.expires_in_days
        else None
    )
    key, row = await api_tokens.mint_key(
        db,
        user_id=auth.user.id,
        label=payload.label,
        scopes=list(payload.scopes),
        printer_ids=payload.printer_ids,
        expires_at=expires_at,
        rate_limit_per_min=payload.rate_limit_per_min,
    )
    out = ApiKeyOut.from_model(row, auth.user.username)
    return ApiKeyMintOut(**out.model_dump(), key=key)


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    db: AsyncSession = Depends(get_db), auth: AuthContext = Depends(require_session)
) -> list[ApiKeyOut]:
    rows = await api_tokens.list_tokens(
        db, user_id=None if auth.is_admin else auth.user.id, keys=True
    )
    names = dict(
        (
            await db.execute(
                select(User.id, User.username).where(User.id.in_({r.user_id for r in rows}))
            )
        )
        .tuples()
        .all()
    )
    return [ApiKeyOut.from_model(r, names.get(r.user_id, "")) for r in rows]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_session),
) -> None:
    ok = await api_tokens.revoke(
        db, key_id, user_id=None if auth.is_admin else auth.user.id, keys=True
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"api key {key_id} not found")
