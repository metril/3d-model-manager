"""P7 review/billing plan Task 6: the review API -- inbox + count,
``POST /queue/{id}/review`` transitions, printer-scoped reviewers, and the
``review`` flag on ``/auth/me``'s per-printer access.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Model, Printer, PrintQueueEntry, Revision, User
from app.models.enums import PrinterKind, ReviewState
from app.tasks import base

pytestmark = pytest.mark.usefixtures("library_root", "printer_enabled")


async def _printer(db_session: AsyncSession, name: str = "ReviewP1") -> int:
    p = Printer(
        name=name, kind=PrinterKind.BAMBU_LAN, host="10.0.0.1", serial=name, access_code_enc="x"
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p.id


def _pending_item(
    *, printer_id: int | None = None, created_by_user_id: int | None = None
) -> PrintQueueEntry:
    with base.sync_session() as s:
        # A per-call unique slug -- `id(object())` is NOT reliable here:
        # CPython can (and in a full-suite run, reliably does) reuse the
        # id of a just-freed temporary object across two back-to-back
        # calls in the same test, colliding on `uq_models_slug`.
        slug = f"review-model-{uuid.uuid4().hex}"
        model = Model(slug=slug, name=slug)
        s.add(model)
        s.flush()
        revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
        s.add(revision)
        s.flush()
        from sqlalchemy import func, select

        max_position = s.scalar(select(func.max(PrintQueueEntry.id))) or 0
        entry = PrintQueueEntry(
            model_id=model.id,
            position=max_position + 1,
            printer_id=printer_id,
            review_state=ReviewState.PENDING,
            created_by_user_id=created_by_user_id,
        )
        s.add(entry)
        s.commit()
        s.refresh(entry)
        return entry


async def _reviewer_setup(
    make_user: Callable[..., Awaitable[User]],
    make_group,
    login_as: Callable[[User], Awaitable[httpx.AsyncClient]],
    pid: int,
) -> tuple[httpx.AsyncClient, User, User]:
    """A reviewer (``queue:review`` + ``can_print`` on ``pid``) and a
    requester whose own ACL on ``pid`` requires review -- the combination
    ``review_printers``/``can_review`` need for a pending item targeting
    ``pid`` to be reviewable by the reviewer at all.
    """
    import uuid

    reviewer = await make_user(username=f"reviewer-{uuid.uuid4().hex[:8]}")
    requester = await make_user(username=f"requester-{uuid.uuid4().hex[:8]}")
    await make_group(
        f"reviewers-{uuid.uuid4().hex[:8]}",
        permissions=["queue:review", "printers:print"],
        members=[reviewer],
        printer_access={pid: (True, False, False)},
    )
    await make_group(
        f"requesters-{uuid.uuid4().hex[:8]}",
        permissions=["printers:print"],
        members=[requester],
        printer_access={pid: (True, False, True)},
    )
    client = await login_as(reviewer)
    return client, reviewer, requester


# ---------------------------------------------------------------------------
# transitions / 409
# ---------------------------------------------------------------------------


async def test_review_transitions_409(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_group,
    login_as: Callable[[User], Awaitable[httpx.AsyncClient]],
) -> None:
    pid = await _printer(db_session)
    client, _, requester = await _reviewer_setup(make_user, make_group, login_as, pid)
    entry = _pending_item(printer_id=pid, created_by_user_id=requester.id)

    r = await client.post(f"/api/queue/{entry.id}/review", json={"action": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["review_state"] == "approved"

    r = await client.post(f"/api/queue/{entry.id}/review", json={"action": "approve"})
    assert r.status_code == 409

    r = await client.post(f"/api/queue/{entry.id}/review", json={"action": "reject"})
    assert r.status_code == 409

    entry2 = _pending_item(printer_id=pid, created_by_user_id=requester.id)
    with base.sync_session() as s:
        current = s.get(PrintQueueEntry, entry2.id)
        current.review_state = ReviewState.NONE
        s.commit()

    r = await client.post(f"/api/queue/{entry2.id}/review", json={"action": "approve"})
    assert r.status_code == 409


async def test_review_unknown_entry_404(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_group,
    login_as: Callable[[User], Awaitable[httpx.AsyncClient]],
) -> None:
    pid = await _printer(db_session)
    client, _, _requester = await _reviewer_setup(make_user, make_group, login_as, pid)

    r = await client.post("/api/queue/999999/review", json={"action": "approve"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# reject releases
# ---------------------------------------------------------------------------


async def test_reject_releases(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_group,
    login_as: Callable[[User], Awaitable[httpx.AsyncClient]],
) -> None:
    from sqlalchemy import select

    from app.models import CostCenter, LedgerEntry
    from app.models.enums import LedgerKind
    from app.services import billing

    pid = await _printer(db_session)
    client, _, requester = await _reviewer_setup(make_user, make_group, login_as, pid)
    owner = await make_user(username="cc-owner-reject")

    # Build the reservation + queue item in one sync session so they share
    # the same cost center.
    with base.sync_session() as s:
        cc = CostCenter(name="reject-cc", owner_user_id=owner.id)
        s.add(cc)
        s.flush()
        slug = "reject-release-model"
        model = Model(slug=slug, name=slug)
        s.add(model)
        s.flush()
        entry = PrintQueueEntry(
            model_id=model.id,
            position=1,
            printer_id=pid,
            review_state=ReviewState.PENDING,
            cost_center_id=cc.id,
            created_by_user_id=requester.id,
        )
        s.add(entry)
        s.flush()
        billing.append(s, cc.id, LedgerKind.RESERVATION, -10, queue_id=entry.id)
        entry.reserved_cost = 10
        s.commit()
        entry_id, cc_id = entry.id, cc.id

    r = await client.post(f"/api/queue/{entry_id}/review", json={"action": "reject"})
    assert r.status_code == 200, r.text
    assert r.json()["review_state"] == "rejected"

    with base.sync_session() as s:
        releases = (
            s.execute(
                select(LedgerEntry).where(
                    LedgerEntry.cost_center_id == cc_id, LedgerEntry.kind == LedgerKind.RELEASE
                )
            )
            .scalars()
            .all()
        )
        assert len(releases) == 1


# ---------------------------------------------------------------------------
# approved becomes eligible
# ---------------------------------------------------------------------------


async def test_approved_becomes_eligible(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_group,
    login_as: Callable[[User], Awaitable[httpx.AsyncClient]],
) -> None:
    from app.models.enums import QueueState
    from app.services import dispatcher

    pid = await _printer(db_session)
    client, _, requester = await _reviewer_setup(make_user, make_group, login_as, pid)

    with base.sync_session() as s:
        from blake3 import blake3

        from app.models.enums import BlobFormat, BlobKind
        from app.models.library import Blob, File

        slug = "approved-eligible-model"
        model = Model(slug=slug, name=slug)
        s.add(model)
        s.flush()
        revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
        s.add(revision)
        s.flush()
        content = b"fake-gcode-3mf-approved"
        digest = blake3(content).hexdigest()
        blob = Blob(
            hash=digest, size=len(content), kind=BlobKind.SLICED, format=BlobFormat.GCODE_3MF
        )
        s.add(blob)
        s.flush()
        file = File(
            revision_id=revision.id,
            blob_hash=digest,
            rel_path=f"{slug}.gcode.3mf",
            storage_path="x",
            verified_at=None,
        )
        s.add(file)
        s.flush()
        entry = PrintQueueEntry(
            model_id=model.id,
            file_id=file.id,
            sliced_file_id=file.id,
            plate=1,
            copies=1,
            state=QueueState.READY,
            position=1,
            printer_id=pid,
            review_state=ReviewState.PENDING,
            created_by_user_id=requester.id,
        )
        s.add(entry)
        s.commit()
        entry_id = entry.id

        ineligible_before = {e.id for e in dispatcher.eligible_queue_items(s)}
        assert entry_id not in ineligible_before

    r = await client.post(f"/api/queue/{entry_id}/review", json={"action": "approve"})
    assert r.status_code == 200, r.text

    with base.sync_session() as s:
        eligible_ids = {e.id for e in dispatcher.eligible_queue_items(s)}
    assert entry_id in eligible_ids


# ---------------------------------------------------------------------------
# reviewer limited to their printers
# ---------------------------------------------------------------------------


async def test_reviewer_limited_to_printers(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_group,
    login_as: Callable[[User], Awaitable[httpx.AsyncClient]],
) -> None:
    allowed_pid = await _printer(db_session, "AllowedPrinter")
    other_pid = await _printer(db_session, "OtherPrinter")
    client, _, requester = await _reviewer_setup(make_user, make_group, login_as, allowed_pid)
    # The requester's own ACL must also trigger review on `other_pid` --
    # otherwise `other_entry` below would need no review from ANYONE
    # (review_printers intersects the creator's trigger set with the
    # item's target), which would mask the printer-scoping this test
    # checks.
    await make_group(
        "other-printer-trigger",
        permissions=["printers:print"],
        members=[requester],
        printer_access={other_pid: (True, False, True)},
    )

    other_entry = _pending_item(printer_id=other_pid, created_by_user_id=requester.id)
    r = await client.post(f"/api/queue/{other_entry.id}/review", json={"action": "approve"})
    assert r.status_code == 403

    r = await client.get("/api/queue/review-inbox")
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()}
    assert other_entry.id not in ids

    allowed_entry = _pending_item(printer_id=allowed_pid, created_by_user_id=requester.id)
    r = await client.get("/api/queue/review-inbox")
    ids = {e["id"] for e in r.json()}
    assert allowed_entry.id in ids


async def test_null_target_visible_via_creator_review_printers(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_group,
    login_as: Callable[[User], Awaitable[httpx.AsyncClient]],
) -> None:
    pid = await _printer(db_session, "CreatorPrinter")
    creator = await make_user(username="creator")
    await make_group(
        "creator-review-group",
        permissions=["printers:print"],
        members=[creator],
        printer_access={pid: (True, False, True)},
    )
    client, _, _requester = await _reviewer_setup(make_user, make_group, login_as, pid)

    entry = _pending_item(printer_id=None, created_by_user_id=creator.id)

    r = await client.get("/api/queue/review-inbox")
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()}
    assert entry.id in ids


# ---------------------------------------------------------------------------
# inbox count
# ---------------------------------------------------------------------------


async def test_inbox_count(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_group,
    login_as: Callable[[User], Awaitable[httpx.AsyncClient]],
) -> None:
    pid = await _printer(db_session)
    client, _, requester = await _reviewer_setup(make_user, make_group, login_as, pid)

    r = await client.get("/api/queue/review-inbox/count")
    assert r.status_code == 200
    assert r.json()["count"] == 0

    _pending_item(printer_id=pid, created_by_user_id=requester.id)
    _pending_item(printer_id=pid, created_by_user_id=requester.id)

    r = await client.get("/api/queue/review-inbox/count")
    assert r.json()["count"] == 2


# ---------------------------------------------------------------------------
# /auth/me review flag
# ---------------------------------------------------------------------------


async def test_me_printer_access_review_flag(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_group,
    login_as: Callable[[User], Awaitable[httpx.AsyncClient]],
) -> None:
    pid = await _printer(db_session)

    reviewer = await make_user(username="flag-reviewer")
    await make_group(
        "flag-reviewers",
        permissions=["queue:review", "printers:print"],
        members=[reviewer],
        printer_access={pid: (True, False, False)},
    )
    reviewer_client = await login_as(reviewer)
    r = await reviewer_client.get("/api/auth/me")
    assert r.json()["printer_access"][str(pid)]["review"] is True

    plain = await make_user(username="flag-plain")
    await make_group(
        "flag-plain",
        permissions=["printers:print"],
        members=[plain],
        printer_access={pid: (True, False, False)},
    )
    plain_client = await login_as(plain)
    r = await plain_client.get("/api/auth/me")
    assert r.json()["printer_access"][str(pid)]["review"] is False
