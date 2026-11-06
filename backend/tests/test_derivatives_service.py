"""The derivative store (``app.services.derivatives``): exact path scheme,
atomic 0644 publish, ``derivatives``-row upsert/mark round-trips, and
verified-copy blob fetching -- the seams every pipeline step (Tasks 2+)
builds on.

DB helpers run through the worker's REAL sync engine (``app.tasks.base``)
against the migrated testcontainer Postgres, exactly as the Celery steps
will call them; rows are seeded through the API-side async session, which
mirrors production (API writes, worker reads).
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Blob, Derivative, File, Model, Revision
from app.models.enums import BlobFormat, BlobKind, DerivativeKind, DerivativeStatus
from app.services import derivatives
from app.storage.local import LocalStorageBackend
from app.tasks.base import sync_session

BLOB_HASH = "0123abcd" * 8  # 64 hex chars; shard dirs are "01"/"23"


def _settings() -> Settings:
    return Settings(data_dir=Path("/data"))


# -- path scheme --------------------------------------------------------


def test_derivative_path_scheme_exact() -> None:
    settings = _settings()
    shard = Path("/data/derivatives/01/23")

    assert derivatives.derivative_path(settings, BLOB_HASH, DerivativeKind.THUMB_256) == (
        shard / f"{BLOB_HASH}.thumb_256.png"
    )
    assert derivatives.derivative_path(settings, BLOB_HASH, DerivativeKind.THUMB_1024) == (
        shard / f"{BLOB_HASH}.thumb_1024.png"
    )
    assert derivatives.derivative_path(settings, BLOB_HASH, DerivativeKind.GLB) == (
        shard / f"{BLOB_HASH}.glb"
    )
    assert derivatives.derivative_path(settings, BLOB_HASH, DerivativeKind.GLB_PREVIEW) == (
        shard / f"{BLOB_HASH}.glb_preview.glb"
    )


def test_rowless_artifact_paths_share_the_derivative_shard_dir() -> None:
    settings = _settings()
    shard = Path("/data/derivatives/01/23")

    assert derivatives.plate_thumb_path(settings, BLOB_HASH, 1) == (
        shard / f"{BLOB_HASH}.plate_1.png"
    )
    assert derivatives.plate_thumb_path(settings, BLOB_HASH, 2) == (
        shard / f"{BLOB_HASH}.plate_2.png"
    )
    assert derivatives.glb_web_path(settings, BLOB_HASH) == shard / f"{BLOB_HASH}.glb_web.glb"


def test_assembly_thumb_path_is_keyed_by_revision_id() -> None:
    settings = _settings()

    assert derivatives.assembly_thumb_path(settings, 42) == Path(
        "/data/derivatives/assembly/42.png"
    )


# -- publish_file --------------------------------------------------------


def test_publish_file_creates_parents_and_relaxes_mode_to_0644(tmp_path: Path) -> None:
    tmp = tmp_path / "staging.png"
    tmp.write_bytes(b"png bytes")
    # What mkstemp-style staging really produces (M1's 0600-spool lesson).
    os.chmod(tmp, 0o600)
    dest = tmp_path / "derivatives" / "01" / "23" / "x.png"

    derivatives.publish_file(tmp, dest)

    assert dest.read_bytes() == b"png bytes"
    assert not tmp.exists()
    assert dest.stat().st_mode & 0o777 == 0o644


def test_publish_file_atomically_replaces_existing_dest(tmp_path: Path) -> None:
    dest = tmp_path / "x.png"
    dest.write_bytes(b"stale")
    tmp = tmp_path / "staging.png"
    tmp.write_bytes(b"fresh")

    derivatives.publish_file(tmp, dest)

    assert dest.read_bytes() == b"fresh"
    assert not tmp.exists()


# -- upsert / mark round trip ---------------------------------------------


async def test_upsert_creates_then_resets_and_mark_persists(db_session: AsyncSession) -> None:
    db_session.add(Blob(hash=BLOB_HASH, size=1, kind=BlobKind.MESH, format=BlobFormat.STL))
    await db_session.commit()

    with sync_session() as session:
        deriv = derivatives.upsert_derivative(session, BLOB_HASH, DerivativeKind.GLB)
        first_id = deriv.id
        assert deriv.status == DerivativeStatus.PENDING
        assert deriv.error is None

        derivatives.mark_derivative(
            session, deriv, status=DerivativeStatus.FAILED, tool="cascadio", error="boom"
        )

    # Re-run (retry semantics): same row, reset back to pending/error=None.
    with sync_session() as session:
        again = derivatives.upsert_derivative(session, BLOB_HASH, DerivativeKind.GLB)
        assert again.id == first_id
        assert again.status == DerivativeStatus.PENDING
        assert again.error is None

        derivatives.mark_derivative(
            session,
            again,
            status=DerivativeStatus.OK,
            local_path="/data/derivatives/01/23/x.glb",
            tool="cascadio",
        )

    row = (
        await db_session.execute(select(Derivative).where(Derivative.blob_hash == BLOB_HASH))
    ).scalar_one()
    assert row.id == first_id
    assert row.status == DerivativeStatus.OK
    assert row.local_path == "/data/derivatives/01/23/x.glb"
    assert row.tool == "cascadio"
    assert row.error is None


# -- shared-blob concurrency (whole-branch review, Important #3) -----------


async def test_upsert_derivative_does_not_reset_ok_row_to_pending(db_session: AsyncSession) -> None:
    """An ``ok`` row is TERMINAL for its ``(blob_hash, kind)``: content-
    addressed output can't change, so a later ``upsert_derivative`` call (a
    second file sharing this blob running the same step) must return the
    already-``ok`` row as-is rather than resetting it back to ``pending`` out
    from under whatever's currently reading/serving it.
    """
    db_session.add(Blob(hash=BLOB_HASH, size=1, kind=BlobKind.MESH, format=BlobFormat.STL))
    await db_session.commit()

    with sync_session() as session:
        deriv = derivatives.upsert_derivative(session, BLOB_HASH, DerivativeKind.GLB)
        derivatives.mark_derivative(
            session, deriv, status=DerivativeStatus.OK, local_path="/good.glb", tool="cascadio"
        )

    with sync_session() as session:
        again = derivatives.upsert_derivative(session, BLOB_HASH, DerivativeKind.GLB)

    assert again.status == DerivativeStatus.OK
    assert again.local_path == "/good.glb"
    assert again.tool == "cascadio"
    assert again.error is None


async def test_upsert_derivative_survives_lost_insert_race(db_session: AsyncSession) -> None:
    """Two workers running the same step for two files sharing a blob can
    both miss the SELECT (no row yet) and both attempt the INSERT; the
    loser's ``UNIQUE(blob_hash, kind)`` violation must be caught, rolled
    back, and resolved by re-selecting the winner's row -- not surfaced as a
    raw, uncaught ``IntegrityError`` failing the loser's job.

    Simulated by monkeypatching this session's own ``add`` so that, exactly
    when ``upsert_derivative`` adds its new (about to lose) row, a SEPARATE
    session wins the race first: it inserts and commits the SAME
    ``(blob_hash, kind)`` row, so this session's own ``commit()`` genuinely
    raises ``IntegrityError`` against real Postgres.
    """
    db_session.add(Blob(hash=BLOB_HASH, size=1, kind=BlobKind.MESH, format=BlobFormat.STL))
    await db_session.commit()

    with sync_session() as session:
        original_add = session.add

        def _add_then_let_other_session_win_the_race(instance, *args, **kwargs):
            with sync_session() as other:
                other.add(
                    Derivative(
                        blob_hash=BLOB_HASH,
                        kind=DerivativeKind.GLB,
                        status=DerivativeStatus.PENDING,
                        local_path="/winner.glb",
                        tool="cascadio",
                    )
                )
                other.commit()
            original_add(instance, *args, **kwargs)

        session.add = _add_then_let_other_session_win_the_race

        deriv = derivatives.upsert_derivative(session, BLOB_HASH, DerivativeKind.GLB)

    # The row upsert_derivative hands back is the OTHER session's winning
    # insert, re-SELECTed after this session's own commit lost the race.
    assert deriv.local_path == "/winner.glb"
    assert deriv.tool == "cascadio"

    with sync_session() as session:
        count = session.scalar(
            select(func.count()).select_from(Derivative).where(Derivative.blob_hash == BLOB_HASH)
        )
    assert count == 1


async def test_mark_derivative_refuses_to_downgrade_ok_row_marked_by_concurrent_run(
    db_session: AsyncSession,
) -> None:
    """The "late-failure stomp" corner: run B calls ``upsert_derivative``
    first and holds a ``pending`` row in its own session -- exactly what two
    workers racing the SAME ``(blob_hash, kind)`` job for a shared blob would
    both do. Run A, in a wholly separate session, then wins: it does the
    work and marks the row ``ok``. When run B later fails nondeterministically
    and tries to stomp the row with ``failed`` using its own (now-stale)
    handle, ``mark_derivative`` must re-check the row's current state and
    refuse the downgrade rather than overwriting A's good, published result.
    """
    db_session.add(Blob(hash=BLOB_HASH, size=1, kind=BlobKind.MESH, format=BlobFormat.STL))
    await db_session.commit()

    with sync_session() as session_b:
        deriv_b = derivatives.upsert_derivative(session_b, BLOB_HASH, DerivativeKind.GLB)
        assert deriv_b.status == DerivativeStatus.PENDING

        with sync_session() as session_a:
            deriv_a = derivatives.upsert_derivative(session_a, BLOB_HASH, DerivativeKind.GLB)
            derivatives.mark_derivative(
                session_a,
                deriv_a,
                status=DerivativeStatus.OK,
                local_path="/good.glb",
                tool="cascadio",
            )

        derivatives.mark_derivative(
            session_b, deriv_b, status=DerivativeStatus.FAILED, error="boom: run B lost the race"
        )

    with sync_session() as session:
        final = session.execute(
            select(Derivative).where(
                Derivative.blob_hash == BLOB_HASH, Derivative.kind == DerivativeKind.GLB
            )
        ).scalar_one()
    assert final.status == DerivativeStatus.OK
    assert final.local_path == "/good.glb"
    assert final.tool == "cascadio"
    assert final.error is None


# -- fetch_blob_to_temp ----------------------------------------------------


async def _seed_model_and_revision(db_session: AsyncSession) -> tuple[Model, Revision]:
    model = Model(slug="widget", name="Widget")
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    db_session.add(revision)
    await db_session.flush()
    return model, revision


async def test_fetch_blob_to_temp_streams_verified_copy(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    tmp_path: Path,
) -> None:
    model, revision = await _seed_model_and_revision(db_session)
    content = b"solid box bytes"
    file = await seed_file(model, revision, "box.stl", content)

    with sync_session() as session:
        fetched = derivatives.fetch_blob_to_temp(
            session, get_settings(), file.blob_hash, tmp_path, ".stl"
        )

    assert fetched == tmp_path / "blob.stl"
    assert fetched.read_bytes() == content


async def test_fetch_blob_to_temp_raises_when_only_unverified_copies_exist(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    tmp_path: Path,
) -> None:
    _, revision = await _seed_model_and_revision(db_session)
    db_session.add(Blob(hash=BLOB_HASH, size=1, kind=BlobKind.MESH, format=BlobFormat.STL))
    db_session.add(
        File(
            revision_id=revision.id,
            blob_hash=BLOB_HASH,
            rel_path="box.stl",
            storage_path="widget/rev-001/box.stl",
            verified_at=None,  # still mid-ingest: never a safe read source
        )
    )
    await db_session.commit()

    with sync_session() as session, pytest.raises(LookupError, match="no stored copy of blob"):
        derivatives.fetch_blob_to_temp(session, get_settings(), BLOB_HASH, tmp_path, ".stl")


class _FakeBackend:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def stat(self, key):
        class _S:
            size = len(self.data)

        return _S()

    def read(self, key, start=None, end=None):
        # over-returns on purpose: the reader must slice to what it asked for
        yield self.data[start : (end + 7 if end is not None else None)]


def test_ranged_reader_seek_modes_and_eof() -> None:
    import io

    r = derivatives._RangedReader(_FakeBackend(b"0123456789"), "k")
    assert r.seek(3) == 3 and r.read(2) == b"34"
    assert r.seek(-2, io.SEEK_CUR) == 3
    assert r.seek(-4, io.SEEK_END) == 6 and r.read() == b"6789"
    assert r.read(5) == b""  # at EOF
    assert r.seek(20) == 20 and r.read(1) == b""  # past EOF reads nothing
    with pytest.raises(ValueError):
        r.seek(-1)
    with pytest.raises(ValueError):
        r.seek(-11, io.SEEK_END)
    with pytest.raises(ValueError):
        r.seek(0, 9)


def test_ranged_reader_serves_a_zip() -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", "hello " * 500)
        zf.writestr("b.txt", "world")
    reader = io.BufferedReader(derivatives._RangedReader(_FakeBackend(buf.getvalue()), "k"))
    with zipfile.ZipFile(reader) as zf:
        assert zf.read("b.txt") == b"world"
        assert zf.read("a.txt") == b"hello " * 500
