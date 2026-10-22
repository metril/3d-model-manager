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
    assert body["printer_access"] == {
        str(printer_row.id): {
            "print": True,
            "control": False,
            "review": True,
            "requires_review": False,
        }
    }
    assert body["role"] == "member"


async def test_me_printer_access_exposes_requires_review(
    make_user, make_group, printer_row, login_as
):
    """I2: a printer that requires review surfaces `requires_review: true`
    in `printer_access` so the web client's `needsReview()` has something
    to key off of."""
    user = await make_user(username="needs-review-member")
    await make_group(
        "needs-review-folks",
        permissions=["printers:print"],
        members=[user],
        printer_access={printer_row.id: (True, False, True)},
    )
    body = (await (await login_as(user)).get("/api/auth/me")).json()
    assert body["printer_access"] == {
        str(printer_row.id): {
            "print": True,
            "control": False,
            "review": False,
            "requires_review": True,
        }
    }
