"""P7 review/billing plan Task 3: the enqueue/dispatch/delete/retry billing
hooks -- reservation at enqueue, after slicing, transferred to clones, and
released on delete/over-budget-retry. Zero-impact when billing is off.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal

import httpx
import pytest
import redis as redis_lib
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import CostCenter, LedgerEntry, Model, Printer, PrintQueueEntry, Revision, Setting
from app.models.enums import (
    BlobFormat,
    BlobKind,
    LedgerKind,
    PrinterKind,
    QueueState,
)
from app.models.processing import BlobMeta
from app.printers.base import state_key
from app.services import billing
from app.services import queue as queue_service
from app.tasks import base

pytestmark = pytest.mark.usefixtures("library_root", "printer_enabled")


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


async def _enable_billing(db_session: AsyncSession) -> None:
    row = await db_session.get(Setting, "app")
    if row is None:
        db_session.add(Setting(key="app", value={"billing_enabled": True}))
    else:
        row.value = {**(row.value or {}), "billing_enabled": True}
    await db_session.commit()


async def _ledger_count(db_session: AsyncSession) -> int:
    return await db_session.scalar(select(func.count()).select_from(LedgerEntry))


# ---------------------------------------------------------------------------
# billing off -> no rows, no columns
# ---------------------------------------------------------------------------


async def test_billing_off_no_rows(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable],
) -> None:
    model, revision = await _model_and_revision(db_session, "billing-off")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    pid = await _printer(db_session, "BillingOffPrinter")

    r = await authenticated_client.post("/api/queue", json={"file_id": f.id, "printer_id": pid})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cost_center_id"] is None
    assert body["reserved_cost"] is None
    assert body["hold_reason"] is None
    entry_id = body["id"]

    delete = await authenticated_client.delete(f"/api/queue/{entry_id}")
    assert delete.status_code == 204

    assert await _ledger_count(db_session) == 0


# ---------------------------------------------------------------------------
# enqueue reserves the private center
# ---------------------------------------------------------------------------


async def test_enqueue_reserves_private_center(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable],
) -> None:
    await _enable_billing(db_session)
    model, revision = await _model_and_revision(db_session, "billing-private")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    db_session.add(
        BlobMeta(
            blob_hash=f.blob_hash,
            raw={
                "plates": [
                    {
                        "index": 1,
                        "prediction_s": 0,
                        "weight_g": 100,
                        "filaments": [
                            {"filament_id": 1, "type": "PLA", "color": "#FF0000", "used_g": 100}
                        ],
                    }
                ]
            },
        )
    )
    await db_session.commit()

    r = await authenticated_client.post("/api/queue", json={"file_id": f.id})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cost_center_id"] is not None
    assert body["reserved_cost"] == pytest.approx(2.0)

    rows = (
        (
            await db_session.execute(
                select(LedgerEntry).where(
                    LedgerEntry.cost_center_id == body["cost_center_id"],
                    LedgerEntry.kind == LedgerKind.RESERVATION,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].amount == Decimal("-2.00")

    cc = await db_session.get(CostCenter, body["cost_center_id"])
    assert cc.owner_user_id is not None


# ---------------------------------------------------------------------------
# group center membership/active checks
# ---------------------------------------------------------------------------


async def test_enqueue_group_center_requires_membership(
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable],
    make_user,
    make_group,
    login_as,
) -> None:
    await _enable_billing(db_session)
    model, revision = await _model_and_revision(db_session, "billing-group")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )

    member = await make_user("member")
    outsider = await make_user("member", username="outsider")
    group = await make_group(
        "billing-group-folks", permissions=["printers:print"], members=[member]
    )
    cc = CostCenter(name="group cc", group_id=group.id, active=True)
    db_session.add(cc)
    await db_session.commit()
    await db_session.refresh(cc)

    outsider_client = await login_as(outsider)
    r = await outsider_client.post("/api/queue", json={"file_id": f.id, "cost_center_id": cc.id})
    assert r.status_code == 403

    cc.active = False
    await db_session.commit()
    member_client = await login_as(member)
    r2 = await member_client.post("/api/queue", json={"file_id": f.id, "cost_center_id": cc.id})
    assert r2.status_code == 422


# ---------------------------------------------------------------------------
# recipe item reserves after slice
# ---------------------------------------------------------------------------


class _FakeSlicerClient:
    """Duck-types `SlicerClient.slice` (mirrors `tests.test_slice_task`)."""

    def __init__(self, result: bytes) -> None:
        self.result = result

    def slice(self, **kwargs) -> bytes:
        return self.result


def _seed_recipe_entry(session, *, cost_center_id: int, created_by_user_id: int) -> int:
    from app.models.enums import ProfileKind
    from app.models.library import Blob, File
    from app.models.slicing import PrintRecipe, SlicerProfile

    model = Model(slug="billing-recipe", name="billing-recipe")
    session.add(model)
    session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    session.add(revision)
    session.flush()

    source_bytes = b"fake-3mf-source-bytes"
    from blake3 import blake3

    digest = blake3(source_bytes).hexdigest()
    blob = Blob(hash=digest, size=len(source_bytes), kind=BlobKind.MESH, format=BlobFormat.THREEMF)
    session.add(blob)
    session.flush()
    source_file = File(
        revision_id=revision.id,
        blob_hash=digest,
        rel_path="billing-recipe.3mf",
        storage_path="x",
        verified_at=None,
    )
    session.add(source_file)
    session.flush()

    printer_profile = SlicerProfile(name="p-printer", kind=ProfileKind.PRINTER, data={})
    process_profile = SlicerProfile(name="p-process", kind=ProfileKind.PROCESS, data={})
    session.add_all([printer_profile, process_profile])
    session.flush()
    recipe = PrintRecipe(
        name="billing-recipe-r",
        printer_profile_id=printer_profile.id,
        process_profile_id=process_profile.id,
        filament_profile_ids=[None],
        bed_type="textured_plate",
        options={},
    )
    session.add(recipe)
    session.flush()

    entry = PrintQueueEntry(
        model_id=model.id,
        file_id=source_file.id,
        recipe_id=recipe.id,
        plate=1,
        copies=1,
        state=QueueState.SLICING,
        position=1,
        cost_center_id=cost_center_id,
        created_by_user_id=created_by_user_id,
    )
    session.add(entry)
    session.commit()
    return entry.id


@pytest.mark.usefixtures("data_dir", "migrated_db")
def test_recipe_item_reserves_after_slice(
    monkeypatch: pytest.MonkeyPatch, sliced_3mf_bytes: bytes
) -> None:
    from app.services import derivatives
    from app.tasks import slicing

    # `_slice` fetches the source model's bytes through this -- stubbed the
    # same way `tests.test_slice_task` does, since there's no real storage
    # backend/verified blob here.
    def _fake_fetch(session, settings, blob_hash, dest_dir, suffix):
        dest = dest_dir / f"blob{suffix}"
        dest.write_bytes(b"fake-3mf-source-bytes")
        return dest

    monkeypatch.setattr(derivatives, "fetch_blob_to_temp", _fake_fetch)
    monkeypatch.setattr(
        slicing, "client_from_config", lambda cfg: _FakeSlicerClient(sliced_3mf_bytes)
    )

    with base.sync_session() as s:
        from app.models import User
        from app.models.enums import UserRole

        user = User(username="recipe-owner", role=UserRole.MEMBER)
        s.add(user)
        s.flush()
        cc = billing.ensure_private_center(s, user.id)
        s.commit()
        entry_id = _seed_recipe_entry(s, cost_center_id=cc.id, created_by_user_id=user.id)
        cc_id = cc.id

    with base.sync_session() as s:
        row = s.get(Setting, "app")
        if row is None:
            s.add(Setting(key="app", value={"billing_enabled": True}))
        else:
            row.value = {**(row.value or {}), "billing_enabled": True}
        s.commit()

    slicing.slice_file(entry_id)

    with base.sync_session() as s:
        refreshed = s.get(PrintQueueEntry, entry_id)
        assert refreshed.state == QueueState.READY
        assert refreshed.reserved_cost is not None
        res = s.execute(
            select(LedgerEntry).where(
                LedgerEntry.print_queue_id == entry_id, LedgerEntry.kind == LedgerKind.RESERVATION
            )
        ).scalar_one_or_none()
        assert res is not None
        assert res.cost_center_id == cc_id


# ---------------------------------------------------------------------------
# dispatch clone transfer
# ---------------------------------------------------------------------------


def _seed_dispatch_model_file(session, *, slug: str):
    from blake3 import blake3

    from app.models.library import Blob, File

    model = Model(slug=slug, name=slug)
    session.add(model)
    session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    session.add(revision)
    session.flush()
    content = b"fake-gcode-3mf-" + slug.encode()
    digest = blake3(content).hexdigest()
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
    session.flush()
    session.add(BlobMeta(blob_hash=digest, raw={"plates": [{"index": 1, "filaments": []}]}))
    session.commit()
    return model, file


def _seed_dispatch_printer(session, *, name: str) -> Printer:
    from app.crypto import encrypt_secret

    p = Printer(
        name=name,
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.1",
        serial=name,
        access_code_enc=encrypt_secret(get_settings(), "12345678"),
        model="A1 mini",
        enabled=True,
    )
    session.add(p)
    session.commit()
    return p


def _seed_redis_state(redis_url: str, printer_id: int, state: dict) -> None:
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(printer_id), __import__("json").dumps(state))
    c.close()


@pytest.mark.usefixtures("data_dir", "printer_enabled")
def test_clone_transfer(redis_url: str, fake_adapter, billing_cfg) -> None:
    """Copies=3 under single fanout: the original keeps its full 3u
    reservation untouched; the remainder clone (2 copies) gets its own,
    brand-new 2u reservation transferred onto the same center."""
    from app.models import User
    from app.models.enums import UserRole

    with base.sync_session() as s:
        model, file = _seed_dispatch_model_file(s, slug="clone-transfer")
        printer = _seed_dispatch_printer(s, name="CloneTransferP")
        user = User(username="clone-transfer-owner", role=UserRole.ADMIN)
        s.add(user)
        s.flush()
        cc = billing.ensure_private_center(s, user.id)
        s.commit()

        from sqlalchemy import func as sa_func

        max_position = s.scalar(select(sa_func.max(PrintQueueEntry.position))) or 0
        entry = PrintQueueEntry(
            model_id=model.id,
            file_id=file.id,
            sliced_file_id=file.id,
            plate=1,
            copies=3,
            state=QueueState.READY,
            position=max_position + 1,
            created_by_user_id=user.id,
            cost_center_id=cc.id,
        )
        s.add(entry)
        s.flush()
        billing.reserve(s, billing_cfg, entry, Decimal("3.00"), user_id=user.id)
        s.commit()
        entry_id, pid, cc_id = entry.id, printer.id, cc.id

    _seed_redis_state(redis_url, pid, {"gcode_state": "IDLE", "trays": []})

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        from app.services import dispatcher

        settings = get_settings()
        dispatcher.dispatch_once(s, client, settings)
    client.close()

    with base.sync_session() as s:
        sent = s.get(PrintQueueEntry, entry_id)
        assert sent.copies == 1
        assert sent.reserved_cost == Decimal("3.00")  # untouched

        clone = s.scalars(
            select(PrintQueueEntry).where(
                PrintQueueEntry.id != entry_id, PrintQueueEntry.model_id == sent.model_id
            )
        ).first()
        assert clone is not None
        assert clone.copies == 2
        assert clone.cost_center_id == cc_id
        assert clone.reserved_cost == Decimal("2.00")

        clone_reservation = s.execute(
            select(LedgerEntry).where(
                LedgerEntry.print_queue_id == clone.id, LedgerEntry.kind == LedgerKind.RESERVATION
            )
        ).scalar_one()
        assert clone_reservation.amount == Decimal("-2.00")


# ---------------------------------------------------------------------------
# delete releases
# ---------------------------------------------------------------------------


async def test_delete_releases(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable],
) -> None:
    await _enable_billing(db_session)
    model, revision = await _model_and_revision(db_session, "delete-releases")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    db_session.add(
        BlobMeta(
            blob_hash=f.blob_hash,
            raw={
                "plates": [
                    {
                        "index": 1,
                        "prediction_s": 0,
                        "weight_g": 50,
                        "filaments": [
                            {"filament_id": 1, "type": "PLA", "color": "#FF0000", "used_g": 50}
                        ],
                    }
                ]
            },
        )
    )
    await db_session.commit()

    r = await authenticated_client.post("/api/queue", json={"file_id": f.id})
    assert r.status_code == 201, r.text
    body = r.json()
    cc_id = body["cost_center_id"]
    assert cc_id is not None

    delete = await authenticated_client.delete(f"/api/queue/{body['id']}")
    assert delete.status_code == 204

    rows = (
        (
            await db_session.execute(
                select(LedgerEntry)
                .where(LedgerEntry.cost_center_id == cc_id)
                .order_by(LedgerEntry.id)
            )
        )
        .scalars()
        .all()
    )
    kinds = [row.kind for row in rows]
    assert kinds == [LedgerKind.RESERVATION, LedgerKind.RELEASE]
    assert rows[0].amount == -rows[1].amount


# ---------------------------------------------------------------------------
# over-budget retry re-reserves
# ---------------------------------------------------------------------------


async def test_retry_over_budget_rereserves(db_session: AsyncSession) -> None:
    await _enable_billing(db_session)
    from app.models import User
    from app.models.enums import UserRole

    user = User(username="retry-owner", role=UserRole.MEMBER)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    cc = CostCenter(name="retry cc", owner_user_id=user.id, budget_total=Decimal("1.00"))
    db_session.add(cc)
    await db_session.commit()
    await db_session.refresh(cc)

    model = Model(slug="retry-over-budget", name="retry-over-budget")
    db_session.add(model)
    await db_session.flush()
    entry = PrintQueueEntry(
        model_id=model.id,
        position=1,
        state=QueueState.READY,
        cost_center_id=cc.id,
        created_by_user_id=user.id,
        hold_reason="over_budget",
        reserved_cost=Decimal("5.00"),
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    # Top up the center so the retry now fits.
    db_session.add(
        LedgerEntry(cost_center_id=cc.id, kind=LedgerKind.DEPOSIT, amount=Decimal("10.00"))
    )
    await db_session.commit()

    settings = get_settings()
    out = await queue_service.retry_entry(db_session, settings, entry.id)
    assert out.hold_reason is None
    assert out.reserved_cost == pytest.approx(5.0)

    rows = (
        (
            await db_session.execute(
                select(LedgerEntry).where(
                    LedgerEntry.print_queue_id == entry.id,
                    LedgerEntry.kind == LedgerKind.RESERVATION,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].amount == Decimal("-5.00")
