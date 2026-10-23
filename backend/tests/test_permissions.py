import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Printer
from app.models.auth import Group, GroupMember, PrinterAccess, User
from app.models.enums import PrinterKind, UserRole
from app.services.permissions import (
    FULL_ACL,
    PERMS,
    PrinterAcl,
    can_print_async,
    effective_perms,
    load_grants,
    merge_printer_acl,
    printer_perms,
    role_base,
    validate_perms,
)


def test_role_bases():
    assert role_base(UserRole.VIEWER) == frozenset()
    assert role_base(UserRole.MEMBER) == {"library:write", "inventory:write", "printers:print"}
    assert role_base(UserRole.ADMIN) == frozenset(PERMS)


def test_effective_perms_unions_groups():
    u = User(username="v", role=UserRole.VIEWER)
    assert effective_perms(u, [Group(name="g", permissions=["queue:review"])]) == {"queue:review"}


def test_group_granting_admin_yields_all():
    u = User(username="m", role=UserRole.MEMBER)
    assert effective_perms(u, [Group(name="a", permissions=["admin"])]) == frozenset(PERMS)


def test_validate_perms_rejects_unknown():
    with pytest.raises(ValueError, match="unknown permission: printers:fly"):
        validate_perms(["printers:fly"])


def test_validate_perms_dedupes_and_sorts():
    assert validate_perms(["queue:review", "admin", "queue:review"]) == ["admin", "queue:review"]


def test_merge_printer_acl_ors_across_groups():
    rows = [
        PrinterAccess(
            group_id=1, printer_id=7, can_print=True, can_control=False, requires_review=False
        ),
        PrinterAccess(
            group_id=2, printer_id=7, can_print=False, can_control=True, requires_review=True
        ),
    ]
    assert merge_printer_acl(rows)[7] == PrinterAcl(True, True, True)


def test_printer_perms_admin_full_member_none():
    assert printer_perms(frozenset(PERMS), {}, 3) == FULL_ACL
    assert printer_perms(frozenset({"printers:print"}), {}, 3) is None


async def test_load_grants_reads_memberships(db_session: AsyncSession):
    user = User(username="v", role=UserRole.VIEWER)
    group = Group(name="g", permissions=["library:write"])
    printer = Printer(
        name="p", kind=PrinterKind.BAMBU_LAN, host="h", serial="S", access_code_enc="x"
    )
    db_session.add_all([user, group, printer])
    await db_session.flush()
    db_session.add_all(
        [
            GroupMember(group_id=group.id, user_id=user.id),
            PrinterAccess(group_id=group.id, printer_id=printer.id, can_print=True),
        ]
    )
    await db_session.commit()

    groups, perms, acl = await load_grants(db_session, user)
    assert [g.name for g in groups] == ["g"]
    assert perms == {"library:write"}
    assert acl == {printer.id: PrinterAcl(True, False, False)}


async def test_can_print_async(db_session: AsyncSession):
    printer = Printer(
        name="p", kind=PrinterKind.BAMBU_LAN, host="h", serial="S", access_code_enc="x"
    )
    allowed = User(username="a", role=UserRole.MEMBER)
    no_acl = User(username="b", role=UserRole.MEMBER)
    no_perm = User(username="c", role=UserRole.VIEWER)
    admin = User(username="d", role=UserRole.ADMIN)
    group = Group(name="g", permissions=[])
    db_session.add_all([printer, allowed, no_acl, no_perm, admin, group])
    await db_session.flush()
    db_session.add_all(
        [
            GroupMember(group_id=group.id, user_id=allowed.id),
            GroupMember(group_id=group.id, user_id=no_perm.id),
            PrinterAccess(group_id=group.id, printer_id=printer.id, can_print=True),
        ]
    )
    await db_session.commit()

    assert await can_print_async(db_session, allowed, printer.id)
    assert not await can_print_async(db_session, no_acl, printer.id)
    assert not await can_print_async(db_session, no_perm, printer.id)  # ACL but no global perm
    assert await can_print_async(db_session, admin, printer.id)
