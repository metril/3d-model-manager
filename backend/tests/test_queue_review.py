"""P7 review/billing plan Task 3: the review gate (`app.services.review`) --
`needs_review`'s matrix, the enqueue hook, dispatcher eligibility, and the
`create_job` 409 `queue_required` gate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext
from app.models import Model, Printer, PrintQueueEntry, Revision, Setting
from app.models.enums import BlobFormat, BlobKind, PrinterKind, QueueState, ReviewState
from app.services import dispatcher, review
from app.services.permissions import PrinterAcl
from app.tasks import base

pytestmark = pytest.mark.usefixtures("library_root", "printer_enabled")


def _ctx(perms: frozenset[str], printer_acl: dict[int, PrinterAcl]) -> AuthContext:
    return AuthContext(user=None, session=None, perms=perms, printer_acl=printer_acl, groups=[])


# ---------------------------------------------------------------------------
# needs_review matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "perms,printer_acl,targets,expected",
    [
        # explicit target requiring review, plain member.
        (
            frozenset({"printers:print"}),
            {1: PrinterAcl(True, True, True)},
            {1},
            True,
        ),
        # explicit target NOT requiring review.
        (
            frozenset({"printers:print"}),
            {1: PrinterAcl(True, True, False)},
            {1},
            False,
        ),
        # recipe target requiring review.
        (
            frozenset({"printers:print"}),
            {2: PrinterAcl(True, False, True)},
            {2},
            True,
        ),
        # NULL target -- fanout over every printer the member can print to.
        (
            frozenset({"printers:print"}),
            {1: PrinterAcl(True, True, False), 2: PrinterAcl(True, True, True)},
            None,
            True,
        ),
        (
            frozenset({"printers:print"}),
            {1: PrinterAcl(True, True, False)},
            None,
            False,
        ),
        # a reviewer never needs review, even on a requires_review target.
        (
            frozenset({"printers:print", "queue:review"}),
            {1: PrinterAcl(True, True, True)},
            {1},
            False,
        ),
        # admin never needs review.
        (
            frozenset({"admin"}),
            {},
            {1},
            False,
        ),
    ],
)
def test_needs_review_matrix(perms, printer_acl, targets, expected):
    ctx = _ctx(perms, printer_acl)
    assert review.needs_review(ctx, targets) is expected


# ---------------------------------------------------------------------------
# enqueue -> review_state
# ---------------------------------------------------------------------------


async def _model_and_revision(db_session: AsyncSession, slug: str) -> tuple[Model, Revision]:
    model = Model(slug=slug, name=slug)
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db_session.add(revision)
    await db_session.commit()
    await db_session.refresh(revision)
    return model, revision


async def _printer(db_session: AsyncSession, name: str = "P1") -> int:
    p = Printer(
        name=name, kind=PrinterKind.BAMBU_LAN, host="10.0.0.1", serial=name, access_code_enc="x"
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p.id


async def test_enqueue_on_review_printer_is_pending(
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable],
    make_user,
    make_group,
    login_as,
) -> None:
    model, revision = await _model_and_revision(db_session, "review-pending")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    pid = await _printer(db_session, "ReviewPrinter")
    member = await make_user("member")
    await make_group(
        "review-folks",
        permissions=["printers:print"],
        members=[member],
        printer_access={pid: (True, False, True)},
    )
    client = await login_as(member)

    r = await client.post("/api/queue", json={"file_id": f.id, "printer_id": pid})
    assert r.status_code == 201, r.text
    assert r.json()["review_state"] == "pending"


async def test_enqueue_on_plain_printer_is_none(
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable],
    make_user,
    make_group,
    login_as,
) -> None:
    model, revision = await _model_and_revision(db_session, "review-none")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    pid = await _printer(db_session, "PlainPrinter")
    member = await make_user("member")
    await make_group(
        "plain-folks",
        permissions=["printers:print"],
        members=[member],
        printer_access={pid: (True, False, False)},
    )
    client = await login_as(member)

    r = await client.post("/api/queue", json={"file_id": f.id, "printer_id": pid})
    assert r.status_code == 201, r.text
    assert r.json()["review_state"] == "none"


# ---------------------------------------------------------------------------
# dispatcher eligibility
# ---------------------------------------------------------------------------


def _seed_model_file(session, *, slug: str):
    from blake3 import blake3

    model = Model(slug=slug, name=slug)
    session.add(model)
    session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    session.add(revision)
    session.flush()
    content = b"fake-gcode-3mf-" + slug.encode()
    digest = blake3(content).hexdigest()
    from app.models.library import Blob, File

    blob = Blob(hash=digest, size=len(content), kind=BlobKind.SLICED, format=BlobFormat.GCODE_3MF)
    session.add(blob)
    session.flush()
    file = File(
        revision_id=revision.id,
        blob_hash=digest,
        rel_path=f"{slug}.gcode.3mf",
        storage_path="x",
        verified_at=None,
    )
    session.add(file)
    session.commit()
    return model, revision, file


def _seed_entry(session, *, model, file, state, review_state=ReviewState.NONE, hold_reason=None):
    from sqlalchemy import func, select

    max_position = session.scalar(select(func.max(PrintQueueEntry.position))) or 0
    entry = PrintQueueEntry(
        model_id=model.id,
        file_id=file.id,
        sliced_file_id=file.id,
        plate=1,
        copies=1,
        state=state,
        position=max_position + 1,
        review_state=review_state,
        hold_reason=hold_reason,
    )
    session.add(entry)
    session.commit()
    return entry


@pytest.mark.usefixtures("data_dir", "printer_enabled")
def test_pending_and_rejected_not_eligible():
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="pending-not-eligible")
        pending = _seed_entry(
            s, model=model, file=file, state=QueueState.READY, review_state=ReviewState.PENDING
        )
        model2, _, file2 = _seed_model_file(s, slug="rejected-not-eligible")
        rejected = _seed_entry(
            s, model=model2, file=file2, state=QueueState.READY, review_state=ReviewState.REJECTED
        )
        eligible_ids = {e.id for e in dispatcher.eligible_queue_items(s)}
    assert pending.id not in eligible_ids
    assert rejected.id not in eligible_ids


@pytest.mark.usefixtures("data_dir", "printer_enabled")
def test_over_budget_not_eligible():
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="over-budget-not-eligible")
        held = _seed_entry(
            s, model=model, file=file, state=QueueState.READY, hold_reason="over_budget"
        )
        eligible_ids = {e.id for e in dispatcher.eligible_queue_items(s)}
    assert held.id not in eligible_ids


# ---------------------------------------------------------------------------
# create_job 409 queue_required
# ---------------------------------------------------------------------------


async def _enable_billing(db_session: AsyncSession) -> None:
    row = await db_session.get(Setting, "app")
    if row is None:
        db_session.add(Setting(key="app", value={"billing_enabled": True}))
    else:
        row.value = {**(row.value or {}), "billing_enabled": True}
    await db_session.commit()


async def test_create_job_queue_required_for_member_on_review_printer(
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable],
    make_user,
    make_group,
    login_as,
) -> None:
    model, revision = await _model_and_revision(db_session, "send-review-gate")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    pid = await _printer(db_session, "SendReviewPrinter")
    member = await make_user("member")
    await make_group(
        "send-review-folks",
        permissions=["printers:print"],
        members=[member],
        printer_access={pid: (True, False, True)},
    )
    client = await login_as(member)

    r = await client.post("/api/print/send", json={"file_id": f.id, "plate": 1, "printer_id": pid})
    assert r.status_code == 409
    assert r.json()["detail"] == "queue_required"


async def test_create_job_queue_required_for_everyone_when_billing_on(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable],
) -> None:
    await _enable_billing(db_session)
    model, revision = await _model_and_revision(db_session, "send-billing-gate")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    pid = await _printer(db_session, "SendBillingPrinter")

    r = await authenticated_client.post(
        "/api/print/send", json={"file_id": f.id, "plate": 1, "printer_id": pid}
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "queue_required"
