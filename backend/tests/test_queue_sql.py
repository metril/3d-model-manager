"""Queue SQL: filters + limit in SQL, front-insert / remove via bulk UPDATE."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.enums import PrinterKind, QueueState
from app.models.library import Model, PrintQueueEntry
from app.models.printing import Printer
from app.services import queue as queue_svc

pytestmark = pytest.mark.usefixtures("library_root")


async def _entry(db: AsyncSession, position: int, **kw) -> PrintQueueEntry:
    model = Model(slug=f"qsql-{position}-{id(kw)}", name=f"m{position}")
    db.add(model)
    await db.flush()
    entry = PrintQueueEntry(model_id=model.id, position=position, **kw)
    db.add(entry)
    await db.flush()
    return entry


async def _printer(db: AsyncSession, name: str) -> Printer:
    printer = Printer(
        name=name,
        kind=PrinterKind.BAMBU_LAN,
        host=f"{name}.local",
        serial=name,
        access_code_enc="x",
        enabled=True,
    )
    db.add(printer)
    await db.flush()
    return printer


async def _list(db, ctx=None, **kw):
    return await queue_svc.list_queue(db, get_settings(), ctx, **kw)


async def test_done_excluded_and_limit_applied_after_filter(db_session: AsyncSession) -> None:
    for pos in (1, 2, 3):
        await _entry(db_session, pos, state=QueueState.DONE)
    waiting = [await _entry(db_session, pos) for pos in (4, 5, 6)]
    await db_session.commit()

    out = await _list(db_session, limit=2)
    assert [e.id for e in out] == [w.id for w in waiting[:2]]
    all_rows = await _list(db_session, include_done=True)
    assert [e.position for e in all_rows] == [1, 2, 3, 4, 5, 6]


async def test_visibility_and_pagination(db_session: AsyncSession) -> None:
    mine = await _printer(db_session, "mine")
    other = await _printer(db_session, "other")
    hidden1 = await _entry(db_session, 1, printer_id=other.id)
    free = await _entry(db_session, 2)
    hidden2 = await _entry(db_session, 3, assigned_printer_id=other.id)
    ok = await _entry(db_session, 4, printer_id=mine.id)
    await db_session.commit()

    ctx = SimpleNamespace(is_admin=False, printer_acl={mine.id: object()})
    out = await _list(db_session, ctx, limit=2)
    assert [e.id for e in out] == [free.id, ok.id]
    admin = SimpleNamespace(is_admin=True, printer_acl={})
    assert len(await _list(db_session, admin)) == 4
    assert hidden1.id and hidden2.id


async def test_front_insert_shifts_all(db_session: AsyncSession) -> None:
    await _entry(db_session, 1)
    await _entry(db_session, 2)
    await db_session.commit()
    assert await queue_svc._append_position(db_session, front=True) == 1
    await db_session.commit()
    rows = (
        (await db_session.execute(select(PrintQueueEntry.position).order_by("position")))
        .scalars()
        .all()
    )
    assert rows == [2, 3]


async def test_remove_compacts(db_session: AsyncSession) -> None:
    a = await _entry(db_session, 1)
    b = await _entry(db_session, 2)
    c = await _entry(db_session, 3)
    await db_session.commit()
    await queue_svc.remove_entry(db_session, b.id)
    rows = (
        await db_session.execute(
            select(PrintQueueEntry.id, PrintQueueEntry.position).order_by("position")
        )
    ).all()
    assert [(r.id, r.position) for r in rows] == [(a.id, 1), (c.id, 2)]
