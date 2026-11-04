"""``extract_embedded_thumbs`` pipeline step tests (Task 4; SPEC pipeline row
2; RESEARCH §3/§4 caveat: assimp can't parse Bambu 3MF at all, which is why
this step -- reading the slicer's own embedded preview PNG(s), not a mesh
render -- is the only thumbnail source `3mf`/`gcode_3mf` blobs get): first
``app.pipeline.thumbs.make_thumbs_from_image`` as a pure function, then the
registered pipeline step itself against the procedural corpus, covering the
multi-plate ``gcode_3mf`` extraction, the plain-``3mf`` "first found
thumbnail" branch, the no-embedded-images no-op, the already-ok skip, and the
corrupted-embedded-PNG failure mode.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Blob, Derivative, Job, Model, Revision
from app.models.enums import BlobFormat, BlobKind, DerivativeKind, DerivativeStatus
from app.pipeline import thumbs
from app.services import derivatives
from app.storage.local import LocalStorageBackend
from app.tasks import pipeline
from app.tasks.base import sync_session
from tests.corpus import CorpusPaths

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# app.pipeline.thumbs.make_thumbs_from_image: pure function, no DB.
# ---------------------------------------------------------------------------


def _png_bytes(size: tuple[int, int], color: tuple[int, int, int], mode: str = "RGB") -> bytes:
    image = Image.new(mode, size, color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_make_thumbs_from_image_writes_both_sizes_with_real_png_magic_bytes() -> None:
    settings = get_settings()
    blob_hash = "a" * 64
    src = _png_bytes((400, 300), (10, 20, 30))

    p1024, p256 = thumbs.make_thumbs_from_image(src, settings, blob_hash)

    assert p1024 == derivatives.derivative_path(settings, blob_hash, DerivativeKind.THUMB_1024)
    assert p256 == derivatives.derivative_path(settings, blob_hash, DerivativeKind.THUMB_256)
    assert p1024.read_bytes().startswith(_PNG_MAGIC)
    assert p256.read_bytes().startswith(_PNG_MAGIC)
    with Image.open(p256) as img:
        assert img.width <= 256
        assert img.height <= 256


def test_make_thumbs_from_image_never_upscales_smaller_than_target() -> None:
    settings = get_settings()
    blob_hash = "b" * 64
    src = _png_bytes((10, 6), (200, 0, 0))

    p1024, p256 = thumbs.make_thumbs_from_image(src, settings, blob_hash)

    with Image.open(p1024) as img:
        assert img.size == (10, 6)
    with Image.open(p256) as img:
        assert img.size == (10, 6)


def test_make_thumbs_from_image_preserves_alpha_channel() -> None:
    settings = get_settings()
    blob_hash = "c" * 64
    src = _png_bytes((40, 40), (255, 0, 0, 128), mode="RGBA")

    _, p256 = thumbs.make_thumbs_from_image(src, settings, blob_hash)

    with Image.open(p256) as img:
        assert img.mode == "RGBA"
        assert img.getpixel((0, 0))[3] == 128


def test_make_thumbs_from_image_accepts_a_path(corpus: CorpusPaths) -> None:
    settings = get_settings()
    blob_hash = "d" * 64

    p1024, p256 = thumbs.make_thumbs_from_image(corpus.red_png, settings, blob_hash)

    assert p1024.exists()
    assert p256.exists()


def test_make_thumbs_from_image_raises_value_error_not_os_error_on_garbage_bytes() -> None:
    settings = get_settings()
    blob_hash = "e" * 64

    with pytest.raises(ValueError, match="cannot read embedded thumbnail image"):
        thumbs.make_thumbs_from_image(b"definitely-not-a-png", settings, blob_hash)


# ---------------------------------------------------------------------------
# The registered pipeline step, called directly (M1/Task 3 convention).
# ---------------------------------------------------------------------------


async def _seed_model_and_revision(db_session: AsyncSession) -> tuple[Model, Revision]:
    model = Model(slug="widget", name="Widget")
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    db_session.add(revision)
    await db_session.flush()
    return model, revision


async def _run_embedded_thumbs_step(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    content: bytes,
    *,
    rel_path: str,
    blob_format: BlobFormat,
    blob_kind: BlobKind,
) -> tuple[str, pipeline.StepOutcome]:
    model, revision = await _seed_model_and_revision(db_session)
    file = await seed_file(
        model, revision, rel_path, content, blob_format=blob_format, blob_kind=blob_kind
    )

    with sync_session() as session:
        blob = session.get(Blob, file.blob_hash)
        outcome = pipeline._extract_embedded_thumbs_step(session, get_settings(), backend, blob)

    return file.blob_hash, outcome


async def test_extract_embedded_thumbs_gcode_3mf_extracts_every_plate_plate_one_becomes_thumbs(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    settings = get_settings()
    content = corpus.sliced_gcode_3mf.read_bytes()
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        expected_plate1 = zf.read("Metadata/plate_1.png")
        expected_plate2 = zf.read("Metadata/plate_2.png")

    blob_hash, outcome = await _run_embedded_thumbs_step(
        db_session,
        backend,
        seed_file,
        content,
        rel_path="print.gcode.3mf",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )

    assert outcome == "done"

    plate1_path = derivatives.plate_thumb_path(settings, blob_hash, 1)
    plate2_path = derivatives.plate_thumb_path(settings, blob_hash, 2)
    assert plate1_path.read_bytes() == expected_plate1
    assert plate2_path.read_bytes() == expected_plate2
    assert plate1_path.read_bytes().startswith(_PNG_MAGIC)
    assert plate2_path.read_bytes().startswith(_PNG_MAGIC)

    with sync_session() as session:
        thumb_1024 = session.execute(
            select(Derivative).where(
                Derivative.blob_hash == blob_hash, Derivative.kind == DerivativeKind.THUMB_1024
            )
        ).scalar_one()
        thumb_256 = session.execute(
            select(Derivative).where(
                Derivative.blob_hash == blob_hash, Derivative.kind == DerivativeKind.THUMB_256
            )
        ).scalar_one()

    assert thumb_1024.status == DerivativeStatus.OK
    assert thumb_1024.tool == "embedded"
    assert thumb_256.status == DerivativeStatus.OK
    assert thumb_256.tool == "embedded"

    p1024_path = Path(thumb_1024.local_path)
    p256_path = Path(thumb_256.local_path)
    assert p1024_path.read_bytes().startswith(_PNG_MAGIC)
    assert p256_path.read_bytes().startswith(_PNG_MAGIC)
    with Image.open(p256_path) as img:
        assert img.width <= 256
        assert img.height <= 256


async def test_extract_embedded_thumbs_3mf_project_file_uses_first_found_thumbnail(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    settings = get_settings()
    content = corpus.box_3mf_bambu_with_thumb.read_bytes()

    blob_hash, outcome = await _run_embedded_thumbs_step(
        db_session,
        backend,
        seed_file,
        content,
        rel_path="part.3mf",
        blob_format=BlobFormat.THREEMF,
        blob_kind=BlobKind.MESH,
    )

    assert outcome == "done"
    with sync_session() as session:
        derivs = (
            session.execute(select(Derivative).where(Derivative.blob_hash == blob_hash))
            .scalars()
            .all()
        )
    ok_kinds = {d.kind for d in derivs if d.status == DerivativeStatus.OK}
    assert ok_kinds == {DerivativeKind.THUMB_1024, DerivativeKind.THUMB_256}
    assert all(d.tool == "embedded" for d in derivs)

    # A plain project 3mf never gets per-plate files -- that's gcode_3mf-only.
    assert not derivatives.plate_thumb_path(settings, blob_hash, 1).exists()


async def test_extract_embedded_thumbs_no_embedded_images_is_done_with_no_rows(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    content = corpus.box_3mf_bambu.read_bytes()

    blob_hash, outcome = await _run_embedded_thumbs_step(
        db_session,
        backend,
        seed_file,
        content,
        rel_path="part.3mf",
        blob_format=BlobFormat.THREEMF,
        blob_kind=BlobKind.MESH,
    )

    assert outcome == "done"
    with sync_session() as session:
        count = session.scalar(
            select(func.count()).select_from(Derivative).where(Derivative.blob_hash == blob_hash)
        )
    assert count == 0


async def test_extract_embedded_thumbs_skips_when_both_thumbs_already_ok(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    settings = get_settings()
    content = corpus.sliced_gcode_3mf.read_bytes()
    model, revision = await _seed_model_and_revision(db_session)
    file = await seed_file(
        model,
        revision,
        "print.gcode.3mf",
        content,
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )

    with sync_session() as session:
        deriv_1024 = derivatives.upsert_derivative(
            session, file.blob_hash, DerivativeKind.THUMB_1024
        )
        derivatives.mark_derivative(
            session,
            deriv_1024,
            status=DerivativeStatus.OK,
            tool="embedded",
            local_path="/x/1024.png",
        )
        deriv_256 = derivatives.upsert_derivative(session, file.blob_hash, DerivativeKind.THUMB_256)
        derivatives.mark_derivative(
            session, deriv_256, status=DerivativeStatus.OK, tool="embedded", local_path="/x/256.png"
        )

    with sync_session() as session:
        blob = session.get(Blob, file.blob_hash)
        outcome = pipeline._extract_embedded_thumbs_step(session, get_settings(), backend, blob)

    assert outcome == "skipped"
    # A skip must not do any of the step's other work either.
    assert not derivatives.plate_thumb_path(settings, file.blob_hash, 1).exists()


def _corrupted_thumb_3mf_bytes() -> bytes:
    """A minimal 3mf whose one embedded thumbnail is not actually a valid
    PNG -- ``model_settings.config`` still points at it, so the step gets
    far enough to try (and fail) decoding it via Pillow.
    """
    model_settings_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<config>"
        "<plate>"
        '<metadata key="plater_id" value="1"/>'
        '<metadata key="thumbnail_file" value="Metadata/plate_1.png"/>'
        "</plate>"
        "</config>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        zf.writestr("Metadata/model_settings.config", model_settings_xml)
        zf.writestr("Metadata/plate_1.png", b"not-actually-a-png")
    return buf.getvalue()


async def test_extract_embedded_thumbs_corrupted_png_is_non_fatal(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
) -> None:
    content = _corrupted_thumb_3mf_bytes()
    model, revision = await _seed_model_and_revision(db_session)
    file = await seed_file(
        model,
        revision,
        "part.3mf",
        content,
        blob_format=BlobFormat.THREEMF,
        blob_kind=BlobKind.MESH,
    )

    job = Job(
        type="extract_embedded_thumbs", subject_type="file", subject_id=file.id, state="queued"
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    pipeline.extract_embedded_thumbs(str(job.id), file.blob_hash)

    await db_session.refresh(job)
    assert job.state == "done"

    with sync_session() as session:
        derivs = (
            session.execute(select(Derivative).where(Derivative.blob_hash == file.blob_hash))
            .scalars()
            .all()
        )
    # The chain continued past the bad thumb (convert_to_glb ran next).
    derivs = [d for d in derivs if d.kind != DerivativeKind.GLB]
    assert len(derivs) == 2
    assert {d.kind for d in derivs} == {DerivativeKind.THUMB_1024, DerivativeKind.THUMB_256}
    assert all(d.status == DerivativeStatus.FAILED for d in derivs)
    assert all(d.tool == "embedded" for d in derivs)
    assert all(d.error for d in derivs)
