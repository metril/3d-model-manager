"""Camera API tests (P4 Task 4): token mint, public stream/snapshot auth
(session cookie OR scoped token), the MJPEG generator, and the warm-up 503.
"""

from __future__ import annotations

import asyncio
import uuid
from urllib.parse import parse_qs, urlparse

import anyio
import httpx
import pytest
import redis.asyncio as aioredis

from app.models import Printer
from app.services import camera_state as cs
from app.services.camera_http import STREAM_IDLE_S, mjpeg_parts

pytestmark = pytest.mark.usefixtures("printer_enabled")

JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"


async def _grant_print(make_group, member, printer: Printer) -> None:
    await make_group(
        "printers",
        permissions=["printers:print"],
        members=[member],
        printer_access={printer.id: (True, False, False)},
    )


def _token_from_urls(data: dict) -> str:
    qs = parse_qs(urlparse(data["stream_url"]).query)
    return qs["token"][0]


async def test_snapshot_503_while_warming(
    authenticated_client: httpx.AsyncClient, printer_row: Printer, redis_client: aioredis.Redis
) -> None:
    r = await authenticated_client.get(f"/api/printers/{printer_row.id}/camera/snapshot")

    assert r.status_code == 503
    assert r.headers["retry-after"] == "2"
    assert await redis_client.zcard(cs.viewers_key(printer_row.id)) == 1


async def test_snapshot_returns_jpeg(
    authenticated_client: httpx.AsyncClient, printer_row: Printer, redis_client: aioredis.Redis
) -> None:
    await redis_client.set(cs.frame_key(printer_row.id), JPEG)

    r = await authenticated_client.get(f"/api/printers/{printer_row.id}/camera/snapshot")

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == JPEG


async def test_snapshot_unsupported_404(
    authenticated_client: httpx.AsyncClient, printer_row: Printer, redis_client: aioredis.Redis
) -> None:
    await redis_client.set(cs.status_key(printer_row.id), "unsupported")

    r = await authenticated_client.get(f"/api/printers/{printer_row.id}/camera/snapshot")

    assert r.status_code == 404


def test_stream_idle_s_matches_final_review_plan() -> None:
    assert STREAM_IDLE_S == 20.0


async def test_stream_unsupported_404(
    authenticated_client: httpx.AsyncClient, printer_row: Printer, redis_client: aioredis.Redis
) -> None:
    """Final-review item 8: an `unsupported` camera 404s the stream route
    immediately, instead of opening the multipart response and spinning
    for `STREAM_IDLE_S` with no frame."""
    await redis_client.set(cs.status_key(printer_row.id), "unsupported")

    r = await authenticated_client.get(f"/api/printers/{printer_row.id}/camera/stream")

    assert r.status_code == 404


async def test_token_mint_requires_printer_acl(
    member_client: httpx.AsyncClient, printer_row: Printer
) -> None:
    # A member has the global printers:print perm but no ACL entry for this printer.
    r = await member_client.post(f"/api/printers/{printer_row.id}/camera/token")

    assert r.status_code == 403


async def test_print_only_user_can_mint_and_view(
    member_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    printer_row: Printer,
    make_group,
    member,
    redis_client: aioredis.Redis,
) -> None:
    await _grant_print(make_group, member, printer_row)
    await redis_client.set(cs.frame_key(printer_row.id), JPEG)

    minted = await member_client.post(f"/api/printers/{printer_row.id}/camera/token")
    assert minted.status_code == 200
    token = _token_from_urls(minted.json())

    via_token = await client.get(
        f"/api/printers/{printer_row.id}/camera/snapshot", params={"token": token}
    )
    via_cookie = await member_client.get(f"/api/printers/{printer_row.id}/camera/snapshot")

    assert via_token.status_code == 200
    assert via_cookie.status_code == 200


async def test_cookie_without_acl_denied(
    member_client: httpx.AsyncClient, printer_row: Printer
) -> None:
    r = await member_client.get(f"/api/printers/{printer_row.id}/camera/snapshot")

    assert r.status_code == 403


async def test_token_of_deactivated_user_401(
    member_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    printer_row: Printer,
    make_group,
    member,
    db_session,
) -> None:
    await _grant_print(make_group, member, printer_row)
    minted = await member_client.post(f"/api/printers/{printer_row.id}/camera/token")
    token = _token_from_urls(minted.json())

    member.is_active = False
    await db_session.commit()

    r = await client.get(f"/api/printers/{printer_row.id}/camera/snapshot", params={"token": token})

    assert r.status_code == 401


async def test_token_prefix_does_not_cross_printer_ids(
    authenticated_client: httpx.AsyncClient, printer_row: Printer, db_session
) -> None:
    """``camera:1:`` is a string prefix of ``camera:1:<uid>`` but a token for
    printer 1 must never open printer 12 (or any id starting with 1)."""
    other = Printer(
        id=printer_row.id * 10 + 2,
        name="P12",
        kind=printer_row.kind,
        host="10.0.0.12",
        serial="SER12",
        access_code_enc="x",
    )
    db_session.add(other)
    await db_session.commit()
    minted = await authenticated_client.post(f"/api/printers/{printer_row.id}/camera/token")
    token = _token_from_urls(minted.json())

    r = await authenticated_client.get(
        f"/api/printers/{other.id}/camera/snapshot", params={"token": token}
    )

    assert r.status_code == 401


async def test_camera_token_is_useless_elsewhere(
    authenticated_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    printer_row: Printer,
) -> None:
    minted = await authenticated_client.post(f"/api/printers/{printer_row.id}/camera/token")
    token = _token_from_urls(minted.json())
    client.cookies.clear()

    download = await client.get("/api/files/1/download", params={"token": token})
    ext = await client.get("/api/ext/ping", headers={"Authorization": f"Bearer {token}"})
    client.cookies.set("tdmm_session", token)
    cookie_route = await client.get("/api/printers")

    assert download.status_code == 401
    assert ext.status_code == 401
    assert cookie_route.status_code == 401


async def test_signed_snapshot_without_cookie(
    client: httpx.AsyncClient,
    authenticated_client: httpx.AsyncClient,
    printer_row: Printer,
    redis_client: aioredis.Redis,
) -> None:
    await redis_client.set(cs.frame_key(printer_row.id), JPEG)
    minted = await authenticated_client.post(f"/api/printers/{printer_row.id}/camera/token")
    assert minted.status_code == 200
    token = _token_from_urls(minted.json())

    r = await client.get(f"/api/printers/{printer_row.id}/camera/snapshot", params={"token": token})

    assert r.status_code == 200
    assert r.content == JPEG


async def test_token_for_other_printer_rejected(
    authenticated_client: httpx.AsyncClient,
    printer_row: Printer,
    db_session,
) -> None:
    other = Printer(
        name="P2", kind=printer_row.kind, host="10.0.0.2", serial="SER2", access_code_enc="x"
    )
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)

    minted = await authenticated_client.post(f"/api/printers/{printer_row.id}/camera/token")
    assert minted.status_code == 200
    token = _token_from_urls(minted.json())

    r = await authenticated_client.get(
        f"/api/printers/{other.id}/camera/snapshot", params={"token": token}
    )

    assert r.status_code == 401


async def test_token_rechecks_acl(
    member_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    printer_row: Printer,
    make_group,
    member,
    db_session,
    redis_client: aioredis.Redis,
) -> None:
    from sqlalchemy import delete

    from app.models import PrinterAccess

    await _grant_print(make_group, member, printer_row)
    await redis_client.set(cs.frame_key(printer_row.id), JPEG)

    minted = await member_client.post(f"/api/printers/{printer_row.id}/camera/token")
    assert minted.status_code == 200
    token = _token_from_urls(minted.json())

    await db_session.execute(
        delete(PrinterAccess).where(PrinterAccess.printer_id == printer_row.id)
    )
    await db_session.commit()

    r = await client.get(f"/api/printers/{printer_row.id}/camera/snapshot", params={"token": token})

    assert r.status_code == 403


async def test_no_credentials_401(client: httpx.AsyncClient, printer_row: Printer) -> None:
    r = await client.get(f"/api/printers/{printer_row.id}/camera/snapshot")

    assert r.status_code == 401


async def test_stream_yields_parts_and_unregisters(
    redis_client: aioredis.Redis, printer_row: Printer
) -> None:
    viewer_id = f"stream:{uuid.uuid4()}"
    gen = mjpeg_parts(redis_client, printer_row.id, viewer_id)

    task = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0.1)
    await redis_client.publish(cs.frame_channel(printer_row.id), JPEG)
    part1 = await task
    expected1 = (
        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(JPEG)
        + JPEG
        + b"\r\n"
    )
    assert part1 == expected1

    frame2 = JPEG + b"x"
    task2 = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0.1)
    await redis_client.publish(cs.frame_channel(printer_row.id), frame2)
    part2 = await task2
    assert frame2 in part2

    assert await redis_client.zcard(cs.viewers_key(printer_row.id)) == 1
    await gen.aclose()
    assert await redis_client.zcard(cs.viewers_key(printer_row.id)) == 0


async def test_mjpeg_parts_cleans_up_when_cancelled_mid_stream(
    redis_client: aioredis.Redis, printer_row: Printer
) -> None:
    """Final-review item 5: cancelling the generator mid-stream (an anyio
    cancel scope, as Starlette uses when a streaming client disconnects --
    not merely `.aclose()`'s GeneratorExit) must still run the viewer
    removal/pubsub teardown. Without `CancelScope(shield=True)` around
    that teardown, the ambient cancellation re-fires on the very first
    awaited cleanup call, leaking the viewer registration."""
    gen = mjpeg_parts(redis_client, printer_row.id, "v-cancel")
    scope = anyio.CancelScope()

    async def _consume() -> None:
        with scope:
            async for _ in gen:
                pass

    async with anyio.create_task_group() as tg:
        tg.start_soon(_consume)
        await asyncio.sleep(0.1)  # let it register the viewer and park on get_message
        assert await redis_client.zcard(cs.viewers_key(printer_row.id)) == 1
        scope.cancel()

    assert await redis_client.zcard(cs.viewers_key(printer_row.id)) == 0


async def test_stream_route_ends_when_idle(
    monkeypatch: pytest.MonkeyPatch, redis_client: aioredis.Redis, printer_row: Printer
) -> None:
    import app.services.camera_http as printers_mod

    monkeypatch.setattr(printers_mod, "STREAM_IDLE_S", 0.5)
    await redis_client.set(cs.frame_key(printer_row.id), JPEG)

    parts = []
    async for part in printers_mod.mjpeg_parts(redis_client, printer_row.id, "v1"):
        parts.append(part)

    assert len(parts) == 1
    assert await redis_client.zcard(cs.viewers_key(printer_row.id)) == 0


# -- Q4: overlay tokens, fps, status, rotation -------------------------------


async def _mint_overlay(c: httpx.AsyncClient, pid: int) -> dict:
    r = await c.post(f"/api/printers/{pid}/overlay-tokens", json={"label": "obs"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_overlay_token_mint_list_revoke(
    authenticated_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    printer_row: Printer,
    redis_client: aioredis.Redis,
) -> None:
    pid = printer_row.id
    await redis_client.set(cs.frame_key(pid), JPEG)
    minted = await _mint_overlay(authenticated_client, pid)
    token = minted["token"]
    assert token.startswith("ovl_") and minted["label"] == "obs"

    listed = (await authenticated_client.get(f"/api/printers/{pid}/overlay-tokens")).json()
    assert len(listed) == 1 and "token" not in listed[0]

    # public overlay route + camera routes accept it, no session
    r = await client.get(f"/api/overlay/{pid}", params={"token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["printer_name"] == "P1" and token in body["stream_url"]
    snap = await client.get(f"/api/printers/{pid}/camera/snapshot", params={"token": token})
    assert snap.status_code == 200

    # scoped to its printer, and unknown tokens fail
    assert (await client.get("/api/overlay/999999", params={"token": token})).status_code == 401
    bad = await client.get(f"/api/overlay/{pid}", params={"token": "ovl_nope"})
    assert bad.status_code == 401

    d = await authenticated_client.delete(f"/api/printers/{pid}/overlay-tokens/{minted['id']}")
    assert d.status_code == 204
    assert (await client.get(f"/api/overlay/{pid}", params={"token": token})).status_code == 401
    assert (
        await client.get(f"/api/printers/{pid}/camera/snapshot", params={"token": token})
    ).status_code == 401


async def test_overlay_token_requires_control(
    member_client: httpx.AsyncClient, printer_row: Printer, make_group, member
) -> None:
    await _grant_print(make_group, member, printer_row)  # print, not control
    r = await member_client.post(f"/api/printers/{printer_row.id}/overlay-tokens", json={})
    assert r.status_code == 403


async def test_stream_fps_bounds(
    authenticated_client: httpx.AsyncClient, printer_row: Printer
) -> None:
    for bad in (0, 16, -1):
        r = await authenticated_client.get(
            f"/api/printers/{printer_row.id}/camera/stream", params={"fps": bad}
        )
        assert r.status_code == 422


async def test_mjpeg_parts_fps_throttle(redis_client: aioredis.Redis, printer_row: Printer) -> None:
    pid = printer_row.id
    await redis_client.set(cs.frame_key(pid), JPEG)
    gen = mjpeg_parts(redis_client, pid, "v-fps", min_interval=60.0)
    got = [await gen.__anext__()]  # the current frame is always sent first
    await redis_client.publish(cs.frame_channel(pid), JPEG)  # throttled away
    with anyio.move_on_after(1.5):
        got.append(await gen.__anext__())
    await gen.aclose()
    assert len(got) == 1


async def test_camera_status_and_options_redaction(
    authenticated_client: httpx.AsyncClient, printer_row: Printer
) -> None:
    pid = printer_row.id
    s = (await authenticated_client.get(f"/api/printers/{pid}/camera/status")).json()
    assert s["source"] == "none" and s["beta"] is False and s["rotation"] == 0

    r = await authenticated_client.patch(
        f"/api/printers/{pid}",
        json={
            "model": "X1C",
            "options": {"camera_rotation": 180},
        },
    )
    assert r.status_code == 200
    s = (await authenticated_client.get(f"/api/printers/{pid}/camera/status")).json()
    assert (s["source"], s["beta"], s["rotation"]) == ("rtsp", True, 180)

    r = await authenticated_client.patch(
        f"/api/printers/{pid}",
        json={"options": {"camera": {"kind": "snapshot", "url": "http://cam/secret.jpg"}}},
    )
    assert r.status_code == 200
    assert "secret" not in r.text and r.json()["options"]["camera"]["has_url"] is True
    s = (await authenticated_client.get(f"/api/printers/{pid}/camera/status")).json()
    assert s["source"] == "external" and s["beta"] is False

    bad = await authenticated_client.patch(
        f"/api/printers/{pid}", json={"options": {"camera_rotation": 7}}
    )
    assert bad.status_code == 422


async def test_snapshot_rotated_server_side(
    authenticated_client: httpx.AsyncClient,
    printer_row: Printer,
    redis_client: aioredis.Redis,
    db_session,
) -> None:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(buf, format="JPEG")
    await redis_client.set(cs.frame_key(printer_row.id), buf.getvalue())
    printer_row.options = {"camera_rotation": 90}
    await db_session.commit()

    r = await authenticated_client.get(f"/api/printers/{printer_row.id}/camera/snapshot")

    assert r.status_code == 200
    assert Image.open(io.BytesIO(r.content)).size == (20, 40)
