import httpx
import pytest
from fastapi import APIRouter, Depends

from app.api.deps import (
    AuthContext,
    require_perm,
    require_perm_on_write,
    require_printer_access,
    require_session,
    visible_printer_ids,
)
from app.models import Printer
from app.models.enums import PrinterKind

_LIB = Depends(require_perm("library:write"))
_CTL = Depends(require_printer_access("control"))

# Throwaway routes mounted on the real app (module-level patch of create_app).
_r = APIRouter(prefix="/_t", dependencies=[Depends(require_session)])


@_r.get("/lib")
async def _lib(_: AuthContext = _LIB) -> dict[str, bool]:
    return {"ok": True}


_w = APIRouter(prefix="/_t", dependencies=[Depends(require_perm_on_write("settings:write"))])


@_w.get("/settings")
async def _sg() -> dict[str, bool]:
    return {"ok": True}


@_w.post("/settings")
async def _sp() -> dict[str, bool]:
    return {"ok": True}


@_r.post("/printers/{printer_id}/go")
async def _go(_: AuthContext = _CTL) -> dict[str, bool]:
    return {"ok": True}


@_r.get("/visible")
async def _visible(ctx: AuthContext = Depends(require_session)) -> dict[str, list[int] | None]:
    v = visible_printer_ids(ctx)
    return {"ids": None if v is None else sorted(v)}


@pytest.fixture(autouse=True)
def _mount(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main_mod

    orig = main_mod.create_app

    def patched():
        app = orig()
        app.include_router(_r, prefix="/api")
        app.include_router(_w, prefix="/api")
        return app

    monkeypatch.setattr(main_mod, "create_app", patched)
    import tests.auth_fixtures as af

    monkeypatch.setattr(af, "create_app", patched)


async def test_viewer_403_member_200_on_library_write(make_user, login_as):
    viewer = await login_as(await make_user("viewer"))
    r = await viewer.get("/api/_t/lib")
    assert r.status_code == 403
    assert r.json()["detail"] == "missing permission: library:write"
    member = await login_as(await make_user("member"))
    assert (await member.get("/api/_t/lib")).status_code == 200


async def test_perm_on_write_allows_get(make_user, login_as):
    viewer = await login_as(await make_user("viewer"))
    assert (await viewer.get("/api/_t/settings")).status_code == 200
    assert (await viewer.post("/api/_t/settings")).status_code == 403


async def test_printer_access_requires_perm_and_acl(
    make_user, make_group, login_as, printer_row: Printer
):
    pid = printer_row.id
    u1 = await make_user("member")
    await make_group(
        "ops",
        permissions=["printers:control"],
        members=[u1],
        printer_access={pid: (True, True, False)},
    )
    c1 = await login_as(u1)
    assert (await c1.post(f"/api/_t/printers/{pid}/go")).status_code == 200

    u2 = await make_user("member")
    await make_group("acl-only", members=[u2], printer_access={pid: (True, True, False)})
    r = await (await login_as(u2)).post(f"/api/_t/printers/{pid}/go")
    assert r.status_code == 403 and r.json()["detail"] == "printer access denied"

    u3 = await make_user("member")
    await make_group("perm-only", permissions=["printers:control"], members=[u3])
    assert (await (await login_as(u3)).post(f"/api/_t/printers/{pid}/go")).status_code == 403

    admin = await login_as(await make_user("admin"))
    assert (await admin.post(f"/api/_t/printers/{pid}/go")).status_code == 200


def test_require_perm_rejects_unknown_name():
    with pytest.raises(ValueError):
        require_perm("library:wrote")  # type: ignore[arg-type]


async def test_visible_printer_ids(make_user, make_group, login_as, db_session):
    ids = []
    for i in range(2):
        p = Printer(
            name=f"p{i}", kind=PrinterKind.BAMBU_LAN, host="h", serial=f"s{i}", access_code_enc="x"
        )
        db_session.add(p)
        await db_session.flush()
        ids.append(p.id)
    await db_session.commit()

    admin = await login_as(await make_user("admin"))
    assert (await admin.get("/api/_t/visible")).json() == {"ids": None}
    m = await make_user("member")
    await make_group("g", members=[m], printer_access={i: (True, False, False) for i in ids})
    assert (await (await login_as(m)).get("/api/_t/visible")).json() == {"ids": sorted(ids)}
    lone = await login_as(await make_user("member"))
    assert (await lone.get("/api/_t/visible")).json() == {"ids": []}


async def test_fixtures_exist(member_client: httpx.AsyncClient, viewer_client, redis_client):
    assert (await member_client.get("/api/auth/me")).status_code == 200
    assert (await viewer_client.get("/api/auth/me")).status_code == 200
    assert await redis_client.ping()
