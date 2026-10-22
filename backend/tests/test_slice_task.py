"""Tests for `app.tasks.slicing.slice_file` (P6 Task 5): the sliced output
becomes a new File on the source file's revision, with the retry rule for
`SlicerError`/`SlicerUnavailable`/validation failures."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.enums import BlobFormat, BlobKind, Fanout, ProfileKind, QueueState
from app.models.library import File, Model, PrintQueueEntry, Revision
from app.models.slicing import PrintRecipe, SlicerProfile
from app.services.slicer_client import SlicerError, SlicerUnavailable
from app.tasks import base, slicing

pytestmark = pytest.mark.usefixtures("data_dir")


class FakeSlicerClient:
    """Duck-types `SlicerClient.slice` for the task: returns a fixed byte
    payload, or raises whatever `side_effect` is given."""

    def __init__(self, result: bytes | None = None, side_effect: Exception | None = None):
        self.result = result
        self.side_effect = side_effect
        self.calls = 0

    def slice(self, **kwargs) -> bytes:
        self.calls += 1
        if self.side_effect is not None:
            raise self.side_effect
        assert self.result is not None
        return self.result


def _seed(
    session: Session, *, plate: int | None = 1, printer_model: str = "Bambu Lab A1 mini"
) -> PrintQueueEntry:
    model = Model(slug="dotc-part-1", name="DotC Part 1")
    session.add(model)
    session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    session.add(revision)
    session.flush()
    model.current_revision_id = revision.id

    source_bytes = b"fake-3mf-source-bytes"
    from blake3 import blake3

    digest = blake3(source_bytes).hexdigest()
    from app.models.library import Blob

    blob = Blob(hash=digest, size=len(source_bytes), kind=BlobKind.MESH, format=BlobFormat.THREEMF)
    session.add(blob)
    session.flush()
    source_file = File(
        revision_id=revision.id,
        blob_hash=digest,
        rel_path="DotC Part 1.3mf",
        storage_path="x",
        verified_at=None,
    )
    session.add(source_file)
    session.flush()

    printer_profile = SlicerProfile(
        name="Printer", kind=ProfileKind.PRINTER, printer_model=printer_model, data={}
    )
    process_profile = SlicerProfile(name="Process", kind=ProfileKind.PROCESS, data={})
    filament_profile = SlicerProfile(name="Filament", kind=ProfileKind.FILAMENT, data={})
    session.add_all([printer_profile, process_profile, filament_profile])
    session.flush()

    recipe = PrintRecipe(
        name="My Recipe",
        printer_profile_id=printer_profile.id,
        process_profile_id=process_profile.id,
        filament_profile_ids=[filament_profile.id],
        bed_type="textured_pei",
        options={},
        fanout=Fanout.SINGLE,
    )
    session.add(recipe)
    session.flush()

    entry = PrintQueueEntry(
        model_id=model.id,
        position=1,
        file_id=source_file.id,
        recipe_id=recipe.id,
        plate=plate,
        state=QueueState.SLICING,
    )
    session.add(entry)
    session.commit()
    return entry


@pytest.fixture(autouse=True)
def _verify_source_blob(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """`derivatives.fetch_blob_to_temp` requires a verified File row AND a
    real backend to read the bytes from -- stub both so the task's `_slice`
    can fetch the (fake) source model without a real storage backend."""
    from app.services import derivatives

    def _fake_fetch(session, settings, blob_hash, dest_dir, suffix):
        dest = dest_dir / f"blob{suffix}"
        dest.write_bytes(b"fake-3mf-source-bytes")
        return dest

    monkeypatch.setattr(derivatives, "fetch_blob_to_temp", _fake_fetch)
    yield


@pytest.mark.usefixtures("migrated_db")
def test_slice_success_ready(monkeypatch: pytest.MonkeyPatch, sliced_3mf_bytes: bytes):
    with base.sync_session() as session:
        entry = _seed(session)
        entry_id = entry.id

    fake_client = FakeSlicerClient(result=sliced_3mf_bytes)
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake_client)

    slicing.slice_file(entry_id)

    with base.sync_session() as session:
        refreshed = session.get(PrintQueueEntry, entry_id)
        assert refreshed.state == QueueState.READY
        assert refreshed.sliced_file_id is not None
        new_file = session.get(File, refreshed.sliced_file_id)
        assert new_file.rel_path == f"DotC Part 1 - My Recipe (q{entry_id} p1).gcode.3mf"
        source_file = session.get(File, refreshed.file_id)
        assert new_file.revision_id == source_file.revision_id


@pytest.mark.usefixtures("migrated_db")
def test_slice_failure_retries_then_fails(monkeypatch: pytest.MonkeyPatch):
    with base.sync_session() as session:
        entry = _seed(session)
        entry_id = entry.id

    fake_client = FakeSlicerClient(side_effect=SlicerError("boom"))
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake_client)

    for expected_attempts in (1, 2, 3):
        with base.sync_session() as session:
            session.execute(
                PrintQueueEntry.__table__.update()
                .where(PrintQueueEntry.id == entry_id)
                .values(state=QueueState.SLICING.value)
            )
            session.commit()
        slicing.slice_file(entry_id)
        with base.sync_session() as session:
            refreshed = session.get(PrintQueueEntry, entry_id)
            assert refreshed.attempts == expected_attempts
            if expected_attempts < 3:
                assert refreshed.state == QueueState.WAITING
            else:
                assert refreshed.state == QueueState.FAILED


@pytest.mark.usefixtures("migrated_db")
def test_unavailable_no_attempt(monkeypatch: pytest.MonkeyPatch):
    with base.sync_session() as session:
        entry = _seed(session)
        entry_id = entry.id

    fake_client = FakeSlicerClient(side_effect=SlicerUnavailable("down"))
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake_client)

    slicing.slice_file(entry_id)

    with base.sync_session() as session:
        refreshed = session.get(PrintQueueEntry, entry_id)
        assert refreshed.attempts == 0
        assert refreshed.state == QueueState.WAITING
        assert refreshed.last_error == "down"


@pytest.mark.usefixtures("migrated_db")
def test_plate_missing_fails(monkeypatch: pytest.MonkeyPatch, sliced_3mf_bytes: bytes):
    with base.sync_session() as session:
        entry = _seed(session, plate=2)  # fixture 3mf only has plate 1
        entry_id = entry.id

    fake_client = FakeSlicerClient(result=sliced_3mf_bytes)
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake_client)

    slicing.slice_file(entry_id)

    with base.sync_session() as session:
        refreshed = session.get(PrintQueueEntry, entry_id)
        assert refreshed.attempts == 1
        assert refreshed.sliced_file_id is None
        assert "plate" in refreshed.last_error


@pytest.mark.usefixtures("migrated_db")
def test_reslice_never_deletes_existing_file_referenced_by_a_job(
    monkeypatch: pytest.MonkeyPatch, sliced_3mf_bytes: bytes
):
    """C1: two separate queue entries slicing the same source+recipe must
    never delete each other's sliced File -- the first one may already be
    referenced by a PrintJob."""
    from app.models.enums import PrinterKind, PrintJobState
    from app.models.printing import Printer, PrintJob

    with base.sync_session() as session:
        entry_a = _seed(session)
        entry_a_id = entry_a.id
        model_id, file_id, recipe_id, plate = (
            entry_a.model_id,
            entry_a.file_id,
            entry_a.recipe_id,
            entry_a.plate,
        )
        entry_b = PrintQueueEntry(
            model_id=model_id,
            position=2,
            file_id=file_id,
            recipe_id=recipe_id,
            plate=plate,
            state=QueueState.SLICING,
        )
        session.add(entry_b)
        session.commit()
        entry_b_id = entry_b.id

    fake_client = FakeSlicerClient(result=sliced_3mf_bytes)
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake_client)
    slicing.slice_file(entry_a_id)

    with base.sync_session() as session:
        file_a_id = session.get(PrintQueueEntry, entry_a_id).sliced_file_id
        assert file_a_id is not None
        printer = Printer(
            name="P",
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial="s",
            access_code_enc="x",
        )
        session.add(printer)
        session.flush()
        job = PrintJob(printer_id=printer.id, file_id=file_a_id, state=PrintJobState.FINISHED)
        session.add(job)
        session.commit()

    # A different sliced byte payload for the second entry -> a different
    # blob hash, so it's stored as its own new File, not deduped.
    fake_client_b = FakeSlicerClient(result=sliced_3mf_bytes + b"\x00")
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake_client_b)
    slicing.slice_file(entry_b_id)

    with base.sync_session() as session:
        file_b_id = session.get(PrintQueueEntry, entry_b_id).sliced_file_id
        assert file_b_id is not None
        assert file_b_id != file_a_id
        # Neither file was deleted -- the job's own file row still resolves.
        assert session.get(File, file_a_id) is not None
        assert session.get(File, file_b_id) is not None
        assert session.get(PrintJob, job.id).file_id == file_a_id


@pytest.mark.usefixtures("migrated_db")
def test_http_slice_call_holds_no_row_lock(
    monkeypatch: pytest.MonkeyPatch, sliced_3mf_bytes: bytes
):
    """I3: the row is claimed (CAS + timestamp) and the claim transaction
    committed BEFORE the HTTP slice call -- a concurrent writer touching the
    same row (e.g. `queue.move_entry`) must never block on it."""
    from sqlalchemy import text

    with base.sync_session() as session:
        entry = _seed(session)
        entry_id = entry.id

    probe: dict[str, bool] = {}

    class ProbingClient(FakeSlicerClient):
        def slice(self, **kwargs) -> bytes:
            engine = base.get_sync_engine()
            with engine.connect() as conn:
                conn.execute(text("SET lock_timeout = '500ms'"))
                try:
                    conn.execute(
                        text("UPDATE print_queue SET last_error = 'probe' WHERE id = :id"),
                        {"id": entry_id},
                    )
                    conn.commit()
                    probe["blocked"] = False
                except Exception:
                    probe["blocked"] = True
            return super().slice(**kwargs)

    fake_client = ProbingClient(result=sliced_3mf_bytes)
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake_client)

    slicing.slice_file(entry_id)

    assert probe["blocked"] is False


@pytest.mark.usefixtures("migrated_db")
def test_wrong_printer_model_fails(monkeypatch: pytest.MonkeyPatch, sliced_3mf_bytes: bytes):
    with base.sync_session() as session:
        entry = _seed(session, printer_model="Bambu Lab X1 Carbon")
        entry_id = entry.id

    fake_client = FakeSlicerClient(result=sliced_3mf_bytes)
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake_client)

    slicing.slice_file(entry_id)

    with base.sync_session() as session:
        refreshed = session.get(PrintQueueEntry, entry_id)
        assert refreshed.attempts == 1
        assert refreshed.sliced_file_id is None
        assert "printer_model" in refreshed.last_error
