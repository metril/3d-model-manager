"""Role x group x route matrix against the real handlers."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


@pytest.mark.parametrize(
    "role,group_perms,method,path,expected",
    [
        ("viewer", [], "PUT", "/api/settings/app", 403),
        ("viewer", ["settings:write"], "PUT", "/api/settings/app", 422),
        ("member", [], "POST", "/api/materials", 422),
        ("viewer", [], "POST", "/api/queue", 403),
        ("member", [], "GET", "/api/settings/app", 200),
        ("member", [], "PUT", "/api/settings/app", 403),
        ("member", [], "POST", "/api/scan", 403),
    ],
)
async def test_perm_matrix(
    make_user, make_group, login_as, role, group_perms, method, path, expected
):
    user = await make_user(role)
    if group_perms:
        await make_group("g", permissions=group_perms, members=[user])
    client = await login_as(user)
    r = await client.request(method, path, json={} if method != "GET" else None)
    assert r.status_code == expected, r.text


@pytest.mark.usefixtures("printer_enabled")
async def test_member_without_acl_cannot_print(member_client, printer_row):
    r = await member_client.post(f"/api/printers/{printer_row.id}/print", json={})
    assert r.status_code == 403


@pytest.mark.usefixtures("printer_enabled")
async def test_member_lists_only_visible_printers(
    make_user, make_group, login_as, printer_row, db_session
):
    from app.models import Printer
    from app.models.enums import PrinterKind

    other = Printer(
        name="P2", kind=PrinterKind.BAMBU_LAN, host="10.0.0.2", serial="SER2", access_code_enc="x"
    )
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    user = await make_user("member")
    await make_group("g", members=[user], printer_access={printer_row.id: (True, True, False)})
    client = await login_as(user)
    r = await client.get("/api/printers")
    assert [p["id"] for p in r.json()] == [printer_row.id]
    assert (await client.get(f"/api/printers/{other.id}")).status_code == 404
    assert (await client.get(f"/api/printers/{other.id}/status")).status_code == 404
    assert (await client.get(f"/api/printers/{printer_row.id}")).status_code == 200


@pytest.mark.usefixtures("printer_enabled")
async def test_member_cannot_create_printer(member_client):
    r = await member_client.post("/api/printers", json={})
    assert r.status_code == 403
