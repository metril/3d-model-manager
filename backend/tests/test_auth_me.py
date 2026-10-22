"""``GET /auth/me`` shape."""

from app.services.permissions import PERMS

pytestmark = []


async def test_me_shape_for_admin(authenticated_client, admin_user):
    body = (await authenticated_client.get("/api/auth/me")).json()
    assert body == {
        "id": admin_user.id,
        "username": "admin",
        "display_name": None,
        "email": None,
        "role": "admin",
        "permissions": sorted(PERMS),
        "groups": [],
        "has_password": True,
        "printer_access": None,
    }


async def test_me_member_with_group(make_user, make_group, printer_row, login_as):
    user = await make_user("member")
    g = await make_group(
        "reviewers",
        permissions=["queue:review"],
        members=[user],
        printer_access={printer_row.id: (True, False, False)},
    )
    body = (await (await login_as(user)).get("/api/auth/me")).json()
    assert body["permissions"] == [
        "inventory:write",
        "library:write",
        "printers:print",
        "queue:review",
    ]
    assert body["groups"] == [{"id": g.id, "name": "reviewers"}]
    assert body["printer_access"] == {str(printer_row.id): {"print": True, "control": False}}
    assert body["role"] == "member"
