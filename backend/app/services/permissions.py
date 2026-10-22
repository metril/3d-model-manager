"""Central permission enum and grant resolver.

Effective permissions = role base set | union of the user's groups' permissions;
any ``admin`` grant makes the user a full admin.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.auth import Group, GroupMember, PrinterAccess, User
from app.models.enums import UserRole

Perm = Literal[
    "admin",
    "library:write",
    "inventory:write",
    "printers:print",
    "printers:control",
    "queue:review",
    "settings:write",
    "billing:manage",
]

PERMS: tuple[str, ...] = (
    "admin",
    "library:write",
    "inventory:write",
    "printers:print",
    "printers:control",
    "queue:review",
    "settings:write",
    "billing:manage",
)

_MEMBER_BASE = frozenset({"library:write", "inventory:write", "printers:print"})


def role_base(role: UserRole) -> frozenset[str]:
    if role == UserRole.ADMIN:
        return frozenset(PERMS)
    if role == UserRole.MEMBER:
        return _MEMBER_BASE
    return frozenset()


def effective_perms(user: User, groups: Sequence[Group]) -> frozenset[str]:
    perms = set(role_base(user.role))
    for g in groups:
        perms.update(g.permissions)
    if "admin" in perms:
        return frozenset(PERMS)
    return frozenset(perms)


def validate_perms(perms: Iterable[str]) -> list[str]:
    out: set[str] = set()
    for p in perms:
        if p not in PERMS:
            raise ValueError(f"unknown permission: {p}")
        out.add(p)
    return sorted(out)


@dataclass(frozen=True)
class PrinterAcl:
    can_print: bool
    can_control: bool
    requires_review: bool


FULL_ACL = PrinterAcl(True, True, False)


def merge_printer_acl(rows: Iterable[PrinterAccess]) -> dict[int, PrinterAcl]:
    merged: dict[int, PrinterAcl] = {}
    for r in rows:
        cur = merged.get(r.printer_id)
        merged[r.printer_id] = (
            PrinterAcl(r.can_print, r.can_control, r.requires_review)
            if cur is None
            else PrinterAcl(
                cur.can_print or r.can_print,
                cur.can_control or r.can_control,
                cur.requires_review or r.requires_review,
            )
        )
    return merged


def printer_perms(
    perms: frozenset[str], acl: Mapping[int, PrinterAcl], printer_id: int
) -> PrinterAcl | None:
    if "admin" in perms:
        return FULL_ACL
    return acl.get(printer_id)


async def load_grants(
    db: AsyncSession, user: User
) -> tuple[list[Group], frozenset[str], dict[int, PrinterAcl]]:
    groups = list(
        (
            await db.scalars(
                select(Group)
                .join(GroupMember, GroupMember.group_id == Group.id)
                .where(GroupMember.user_id == user.id)
                .order_by(Group.id)
            )
        ).all()
    )
    rows: Sequence[PrinterAccess] = []
    if groups:
        rows = (
            await db.scalars(
                select(PrinterAccess).where(PrinterAccess.group_id.in_([g.id for g in groups]))
            )
        ).all()
    return groups, effective_perms(user, groups), merge_printer_acl(rows)


def printer_acl_sync(session: Session, user_id: int | None) -> dict[int, PrinterAcl]:
    """Worker-side (SYNC world) twin of ``load_grants`` + ``merge_printer_acl``:
    ``user_id``'s own merged per-printer ACL. A ``None``/inactive/deleted
    user (e.g. a queue entry whose creator was deactivated or hard-deleted
    after enqueue) always answers ``{}`` -- callers (``can_print_sync``,
    ``app.services.review.review_printers``) then treat that as "no access
    anywhere" rather than silently dropping the owner check. An admin's
    grant isn't backed by any ``PrinterAccess`` row at all, so it's
    materialized as ``FULL_ACL`` over every printer that currently exists.
    """
    if user_id is None:
        return {}
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return {}
    groups = list(
        session.scalars(
            select(Group)
            .join(GroupMember, GroupMember.group_id == Group.id)
            .where(GroupMember.user_id == user.id)
            .order_by(Group.id)
        ).all()
    )
    perms = effective_perms(user, groups)
    if "admin" in perms:
        from app.models.printing import Printer

        printer_ids = session.scalars(select(Printer.id)).all()
        return {pid: FULL_ACL for pid in printer_ids}
    if "printers:print" not in perms or not groups:
        return {}
    rows = session.scalars(
        select(PrinterAccess).where(PrinterAccess.group_id.in_([g.id for g in groups]))
    ).all()
    return merge_printer_acl(rows)


def can_print_sync(session: Session, user_id: int | None, printer_id: int) -> bool:
    """Worker-side (SYNC world) twin of ``load_grants`` + ``printer_perms``
    collapsed to the one question the P6 dispatcher needs: may ``user_id``
    send to ``printer_id`` right now?"""
    acl = printer_acl_sync(session, user_id)
    return printer_id in acl and acl[printer_id].can_print
