"""Admin-only user management (``/admin/*``, gated by the ``admin`` perm)."""

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.db import get_db
from app.models import AuditLog, Group, GroupMember, Printer, PrinterAccess, User
from app.models import Session as SessionModel
from app.models.enums import MembershipSource
from app.schemas.admin import (
    GroupCreateIn,
    GroupMemberOut,
    GroupMembersIn,
    GroupOut,
    GroupPatchIn,
    GroupRef,
    PasswordResetIn,
    PrinterAccessIO,
    UserAdminOut,
    UserCreateIn,
    UserPatchIn,
)
from app.security import hash_password
from app.services import two_factor
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
        password_hash=(
            await anyio.to_thread.run_sync(hash_password, payload.password)
            if payload.password
            else None
        ),
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
    user.password_hash = await anyio.to_thread.run_sync(hash_password, payload.new_password)
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


# --- groups -----------------------------------------------------------------


async def _group_out(db: AsyncSession, group: Group) -> GroupOut:
    members = (
        await db.execute(
            select(GroupMember, User.username)
            .join(User, User.id == GroupMember.user_id)
            .where(GroupMember.group_id == group.id)
            .order_by(GroupMember.user_id)
        )
    ).all()
    access = (
        await db.scalars(
            select(PrinterAccess)
            .where(PrinterAccess.group_id == group.id)
            .order_by(PrinterAccess.printer_id)
        )
    ).all()
    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        permissions=list(group.permissions),
        members=[
            GroupMemberOut(user_id=m.user_id, username=name, source=m.source) for m, name in members
        ],
        printer_access=[
            PrinterAccessIO(
                printer_id=a.printer_id,
                can_print=a.can_print,
                can_control=a.can_control,
                requires_review=a.requires_review,
            )
            for a in access
        ],
    )


async def _get_group(db: AsyncSession, group_id: int) -> Group:
    group = await db.get(Group, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")
    return group


def _dup_group() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "group name already in use")


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(db: AsyncSession = Depends(get_db)) -> list[GroupOut]:
    groups = (await db.scalars(select(Group).order_by(Group.name))).all()
    return [await _group_out(db, g) for g in groups]


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(payload: GroupCreateIn, db: AsyncSession = Depends(get_db)) -> GroupOut:
    group = Group(
        name=payload.name, description=payload.description, permissions=payload.permissions
    )
    db.add(group)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _dup_group() from None
    await db.refresh(group)
    return await _group_out(db, group)


@router.patch("/groups/{group_id}", response_model=GroupOut)
async def patch_group(
    group_id: int, payload: GroupPatchIn, db: AsyncSession = Depends(get_db)
) -> GroupOut:
    group = await _get_group(db, group_id)
    fields = payload.model_dump(exclude_unset=True)
    if fields.get("name") is None:
        fields.pop("name", None)
    if fields.get("permissions") is None:
        fields.pop("permissions", None)
    for k, v in fields.items():
        setattr(group, k, v)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise _dup_group() from None
    if "permissions" in fields:
        await _guard_last_admin(db)
    await db.commit()
    await db.refresh(group)
    return await _group_out(db, group)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: int, db: AsyncSession = Depends(get_db)) -> None:
    group = await _get_group(db, group_id)
    await db.delete(group)
    await db.flush()
    await _guard_last_admin(db)
    await db.commit()


@router.put("/groups/{group_id}/members", response_model=GroupOut)
async def put_members(
    group_id: int, payload: GroupMembersIn, db: AsyncSession = Depends(get_db)
) -> GroupOut:
    group = await _get_group(db, group_id)
    ids = set(payload.user_ids)
    found = set((await db.scalars(select(User.id).where(User.id.in_(ids)))).all()) if ids else set()
    if found != ids:
        raise HTTPException(422, "unknown user id")
    await db.execute(
        delete(GroupMember).where(
            GroupMember.group_id == group.id, GroupMember.source == MembershipSource.LOCAL
        )
    )
    existing = set(
        (
            await db.scalars(select(GroupMember.user_id).where(GroupMember.group_id == group.id))
        ).all()
    )
    for uid in sorted(ids - existing):
        db.add(GroupMember(group_id=group.id, user_id=uid, source=MembershipSource.LOCAL))
    await db.flush()
    await _guard_last_admin(db)
    await db.commit()
    return await _group_out(db, group)


@router.put("/groups/{group_id}/printer-access", response_model=GroupOut)
async def put_printer_access(
    group_id: int, payload: list[PrinterAccessIO], db: AsyncSession = Depends(get_db)
) -> GroupOut:
    group = await _get_group(db, group_id)
    rows = {r.printer_id: r for r in payload if r.can_print or r.can_control or r.requires_review}
    pids = {r.printer_id for r in payload}
    found = (
        set((await db.scalars(select(Printer.id).where(Printer.id.in_(pids)))).all())
        if pids
        else set()
    )
    if found != pids:
        raise HTTPException(422, "unknown printer id")
    await db.execute(delete(PrinterAccess).where(PrinterAccess.group_id == group.id))
    for r in rows.values():
        db.add(PrinterAccess(group_id=group.id, **r.model_dump()))
    await db.commit()
    return await _group_out(db, group)


@router.post("/users/{user_id}/2fa/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_2fa(
    user_id: int,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove every second factor for ``user_id`` (lost device). Another admin
    must do it: an admin cannot reset their own."""
    if user_id == ctx.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "ask another admin to reset your 2FA")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    await two_factor.wipe_user_2fa(db, user)
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            action="user.2fa_reset",
            payload={"target_user_id": user_id},
        )
    )
    await db.commit()
