"""Camera API tests (P4 Task 4): token mint, public stream/snapshot auth
(session cookie OR scoped token), the MJPEG generator, and the warm-up 503.
"""

from __future__ import annotations

import asyncio
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import redis.asyncio as aioredis

from app.api.printers import mjpeg_parts
from app.models import Printer
from app.services import camera_state as cs

pytestmark = pytest.mark.usefixtures("printer_enabled")

JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"


async def _grant_control(make_group, member, printer: Printer) -> None:
    await make_group(
        "controllers",
        permissions=["printers:control"],
        members=[member],
        printer_access={printer.id: (False, True, False)},
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


async def test_token_mint_requires_control(
    member_client: httpx.AsyncClient, printer_row: Printer
) -> None:
    r = await member_client.post(f"/api/printers/{printer_row.id}/camera/token")

    assert r.status_code == 403


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

    await _grant_control(make_group, member, printer_row)
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


async def test_stream_route_ends_when_idle(
    monkeypatch: pytest.MonkeyPatch, redis_client: aioredis.Redis, printer_row: Printer
) -> None:
    import app.api.printers as printers_mod

    monkeypatch.setattr(printers_mod, "STREAM_IDLE_S", 0.5)
    await redis_client.set(cs.frame_key(printer_row.id), JPEG)

    parts = []
    async for part in printers_mod.mjpeg_parts(redis_client, printer_row.id, "v1"):
        parts.append(part)

    assert len(parts) == 1
    assert await redis_client.zcard(cs.viewers_key(printer_row.id)) == 0
