"""Admin-only OIDC provider management. ``client_secret`` is write-only."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perm
from app.config import get_settings
from app.crypto import encrypt_secret
from app.db import get_db
from app.models import Group, OidcProvider
from app.schemas.admin import OidcProviderCreateIn, OidcProviderOut, OidcProviderPatchIn

router = APIRouter(
    prefix="/admin/oidc-providers",
    tags=["admin"],
    dependencies=[Depends(require_perm("admin"))],
)


def _out(request: Request, p: OidcProvider) -> OidcProviderOut:
    base = (get_settings().public_url or str(request.base_url)).rstrip("/")
    return OidcProviderOut(
        id=p.id,
        name=p.name,
        slug=p.slug,
        discovery_url=p.discovery_url,
        client_id=p.client_id,
        has_client_secret=bool(p.client_secret_enc),
        scopes=p.scopes,
        groups_claim=p.groups_claim,
        group_mapping=p.group_mapping,
        admin_group=p.admin_group,
        auto_create_users=p.auto_create_users,
        link_by_email=p.link_by_email,
        enabled=p.enabled,
        callback_url=f"{base}/api/auth/oidc/{p.slug}/callback",
    )


async def _check_mapping(db: AsyncSession, mapping: dict[str, int]) -> None:
    ids = set(mapping.values())
    if not ids:
        return
    found = set((await db.scalars(select(Group.id).where(Group.id.in_(ids)))).all())
    if missing := ids - found:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown group ids: {sorted(missing)}"
        )


async def _get(db: AsyncSession, provider_id: int) -> OidcProvider:
    p = await db.get(OidcProvider, provider_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "provider not found")
    return p


def _dup() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "slug already in use")


@router.get("", response_model=list[OidcProviderOut])
async def list_providers(
    request: Request, db: AsyncSession = Depends(get_db)
) -> list[OidcProviderOut]:
    rows = (await db.scalars(select(OidcProvider).order_by(OidcProvider.name))).all()
    return [_out(request, p) for p in rows]


@router.post("", response_model=OidcProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: OidcProviderCreateIn, request: Request, db: AsyncSession = Depends(get_db)
) -> OidcProviderOut:
    await _check_mapping(db, body.group_mapping)
    data = body.model_dump(exclude={"client_secret"})
    p = OidcProvider(**data, client_secret_enc=encrypt_secret(get_settings(), body.client_secret))
    db.add(p)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _dup() from None
    await db.refresh(p)
    return _out(request, p)


@router.patch("/{provider_id}", response_model=OidcProviderOut)
async def patch_provider(
    provider_id: int,
    body: OidcProviderPatchIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OidcProviderOut:
    p = await _get(db, provider_id)
    data = body.model_dump(exclude_unset=True)
    secret = data.pop("client_secret", None)
    # Non-nullable columns: an explicit null means "unchanged" except admin_group.
    for k, v in data.items():
        if v is None and k != "admin_group":
            continue
        setattr(p, k, v)
    if "group_mapping" in data and data["group_mapping"] is not None:
        await _check_mapping(db, data["group_mapping"])
    if secret is not None:
        p.client_secret_enc = encrypt_secret(get_settings(), secret)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _dup() from None
    await db.refresh(p)
    return _out(request, p)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    await db.delete(await _get(db, provider_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
