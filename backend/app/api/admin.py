"""Admin-only user management (``/admin/*``, gated by the ``admin`` perm)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.db import get_db
from app.models import Group, GroupMember, User
from app.models import Session as SessionModel
from app.schemas.admin import GroupRef, PasswordResetIn, UserAdminOut, UserCreateIn, UserPatchIn
from app.security import hash_password
from app.services.permissions import load_grants

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_perm("admin"))])


async def _out(db: AsyncSession, user: User) -> UserAdminOut:
    groups = (
        await db.scalars(
            select(Group)
            .join(GroupMember, GroupMember.group_id == Group.id)
            .where(GroupMember.user_id == user.id)
            .order_by(Group.name)
        )
    ).all()
    return UserAdminOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        has_password=user.password_hash is not None,
        oidc_linked=user.oidc_subject is not None,
        last_login_at=user.last_login_at,
        groups=[GroupRef(id=g.id, name=g.name) for g in groups],
    )


async def _get(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return user


async def _active_admin_count(db: AsyncSession) -> int:
    """Active users holding the ``admin`` perm (role- or group-granted)."""
    users = (await db.scalars(select(User).where(User.is_active.is_(True)))).all()
    n = 0
    for u in users:
        _, perms, _ = await load_grants(db, u)
        n += "admin" in perms
    return n


async def _guard_last_admin(db: AsyncSession) -> None:
    """Call after flushing a change; roll back and 409 if no admin remains."""
    if await _active_admin_count(db) == 0:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot remove the last admin")


def _dup() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "username or email already in use")


@router.get("/users", response_model=list[UserAdminOut])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[UserAdminOut]:
    users = (await db.scalars(select(User).order_by(User.id))).all()
    return [await _out(db, u) for u in users]


@router.post("/users", response_model=UserAdminOut, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreateIn, db: AsyncSession = Depends(get_db)) -> UserAdminOut:
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password) if payload.password else None,
        display_name=payload.display_name,
        email=payload.email or None,
        role=payload.role,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _dup() from None
    await db.refresh(user)
    return await _out(db, user)


@router.patch("/users/{user_id}", response_model=UserAdminOut)
async def patch_user(
    user_id: int, payload: UserPatchIn, db: AsyncSession = Depends(get_db)
) -> UserAdminOut:
    user = await _get(db, user_id)
    fields = payload.model_dump(exclude_unset=True)
    if "email" in fields:
        fields["email"] = fields["email"] or None
    if fields.get("role") is None:
        fields.pop("role", None)
    if fields.get("is_active") is None:
        fields.pop("is_active", None)
    for k, v in fields.items():
        setattr(user, k, v)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise _dup() from None
    if "role" in fields or "is_active" in fields:
        await _guard_last_admin(db)
    if fields.get("is_active") is False:
        await db.execute(delete(SessionModel).where(SessionModel.user_id == user.id))
    await db.commit()
    await db.refresh(user)
    return await _out(db, user)


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: int, payload: PasswordResetIn, db: AsyncSession = Depends(get_db)
) -> None:
    user = await _get(db, user_id)
    user.password_hash = hash_password(payload.new_password)
    await db.execute(delete(SessionModel).where(SessionModel.user_id == user.id))
    await db.commit()


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    user = await _get(db, user_id)
    if user.id == ctx.user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot delete yourself")
    await db.delete(user)
    await db.flush()
    await _guard_last_admin(db)
    await db.commit()
