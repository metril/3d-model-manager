import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Printer
from app.models.auth import Group, GroupMember, PrinterAccess, User
from app.models.enums import PrinterKind, UserRole
from app.services.permissions import (
    FULL_ACL,
    PERMS,
    PrinterAcl,
    can_control_sync,
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


def test_can_control_sync():
    from app.tasks import base

    with base.sync_session() as s:
        printer = Printer(
            name="p", kind=PrinterKind.BAMBU_LAN, host="h", serial="S", access_code_enc="x"
        )
        allowed = User(username="a", role=UserRole.VIEWER)
        no_perm = User(username="c", role=UserRole.MEMBER)
        print_only = User(username="e", role=UserRole.VIEWER)
        admin = User(username="d", role=UserRole.ADMIN)
        inactive = User(username="f", role=UserRole.ADMIN, is_active=False)
        g_ok = Group(name="ok", permissions=["printers:control"])
        g_noperm = Group(name="noperm", permissions=[])
        g_print = Group(name="print", permissions=["printers:control"])
        s.add_all([printer, allowed, no_perm, print_only, admin, inactive])
        s.add_all([g_ok, g_noperm, g_print])
        s.flush()
        s.add_all(
            [
                GroupMember(group_id=g_ok.id, user_id=allowed.id),
                GroupMember(group_id=g_noperm.id, user_id=no_perm.id),
                GroupMember(group_id=g_print.id, user_id=print_only.id),
                PrinterAccess(group_id=g_ok.id, printer_id=printer.id, can_control=True),
                PrinterAccess(group_id=g_noperm.id, printer_id=printer.id, can_control=True),
                PrinterAccess(group_id=g_print.id, printer_id=printer.id, can_print=True),
            ]
        )
        s.commit()
        ids = {
            k: u.id
            for k, u in dict(
                allowed=allowed,
                no_perm=no_perm,
                print_only=print_only,
                admin=admin,
                inactive=inactive,
            ).items()
        }
        pid = printer.id

    with base.sync_session() as s:
        assert can_control_sync(s, ids["allowed"], pid)
        assert not can_control_sync(s, ids["allowed"], pid + 1)
        assert not can_control_sync(s, ids["no_perm"], pid)  # ACL flag, no global perm
        assert not can_control_sync(s, ids["print_only"], pid)  # can_print != can_control
        assert can_control_sync(s, ids["admin"], pid)
        assert not can_control_sync(s, ids["inactive"], pid)
        assert not can_control_sync(s, None, pid)
        assert not can_control_sync(s, 99999, pid)
