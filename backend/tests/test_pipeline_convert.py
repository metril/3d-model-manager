"""``convert_to_glb`` pipeline step tests (Task 5; SPEC pipeline rows 3-4;
RESEARCH §2/§5): ``app.pipeline.convert``/``app.pipeline.cad`` as pure
functions against the procedural corpus (every mesh/CAD format branch,
including the cascadio STEP mm-correction -- see ``convert.py``'s own
docstring for why that's needed), then the registered pipeline step itself,
covering skip-if-ok and the truncated-STL failure mode.

Global Constraints "Two GLB artifacts per blob": every assertion here is
about the raw, uncompressed ``glb`` derivative -- the one artifact
f3d/trimesh/OCCT ever read back. ``optimize_glb``'s meshopt-compressed
artifacts are covered separately in ``test_pipeline_optimize.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Blob, Derivative, Job, Model, Revision
from app.models.enums import BlobFormat, BlobKind, DerivativeKind, DerivativeStatus
from app.pipeline import cad, convert, meshload
from app.storage.local import LocalStorageBackend
from app.tasks import pipeline
from app.tasks.base import sync_session
from tests import corpus as corpus_module
from tests.corpus import CorpusPaths

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")

_GLB_MAGIC = b"glTF"
EXPECTED_EXTENTS_MM = (20.0, 10.0, 5.0)  # print space (Z-up)
# glTF is Y-up: (x, y, z) -> (x, z, -y), so the 5 mm height lands on Y.
EXPECTED_GLB_EXTENTS = (20.0, 5.0, 10.0)


def _round_trip(path: Path) -> trimesh.Trimesh:
    return meshload.to_single_mesh(trimesh.load(path))


# ---------------------------------------------------------------------------
# app.pipeline.convert.convert_to_glb_file: pure function, no DB.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name, fmt, expected_tool",
    [
        ("box_stl", BlobFormat.STL, "trimesh"),
        ("box_obj", BlobFormat.OBJ, "trimesh"),
        ("box_3mf_generic", BlobFormat.THREEMF, "trimesh"),
        ("box_3mf_meter", BlobFormat.THREEMF, "trimesh"),
        ("box_3mf_bambu", BlobFormat.THREEMF, "lib3mf"),
        ("box_step", BlobFormat.STEP, "cascadio"),
        ("box_iges", BlobFormat.IGES, "cadquery-ocp"),
    ],
)
def test_convert_to_glb_file_every_format_branch(
    tmp_path: Path,
    corpus: CorpusPaths,
    fixture_name: str,
    fmt: BlobFormat,
    expected_tool: str,
) -> None:
    src = getattr(corpus, fixture_name)
    dst = tmp_path / "out.glb"

    tool = convert.convert_to_glb_file(src, fmt, dst)

    assert tool == expected_tool
    assert dst.read_bytes()[:4] == _GLB_MAGIC
    mesh = _round_trip(dst)
    assert len(mesh.faces) == 12
    assert mesh.extents == pytest.approx(EXPECTED_GLB_EXTENTS, abs=1e-3)
    assert convert.to_z_up(mesh).extents == pytest.approx(EXPECTED_EXTENTS_MM, abs=1e-3)


def test_convert_to_glb_file_stl_raises_clearly_on_unparseable_content(tmp_path: Path) -> None:
    src = tmp_path / "blob.stl"
    src.write_bytes(b"not-actually-an-stl-file")
    dst = tmp_path / "out.glb"

    with pytest.raises(ValueError, match="no geometry found"):
        convert.convert_to_glb_file(src, BlobFormat.STL, dst)


def test_convert_to_glb_file_unsupported_format_raises(tmp_path: Path) -> None:
    src = tmp_path / "blob.png"
    src.write_bytes(b"not-a-real-png")
    dst = tmp_path / "out.glb"

    with pytest.raises(ValueError, match="unsupported format"):
        convert.convert_to_glb_file(src, BlobFormat.PNG, dst)


# ---------------------------------------------------------------------------
# app.pipeline.cad.iges_to_glb: pure function, no DB.
# ---------------------------------------------------------------------------


def test_iges_to_glb_direct(tmp_path: Path, corpus: CorpusPaths) -> None:
    dst = tmp_path / "out.glb"

    cad.iges_to_glb(corpus.box_iges, dst)

    assert dst.read_bytes()[:4] == _GLB_MAGIC
    mesh = _round_trip(dst)
    assert len(mesh.faces) == 12
    assert mesh.extents == pytest.approx(EXPECTED_EXTENTS_MM, abs=1e-3)
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(1000.0, abs=1e-2)


def test_iges_to_glb_raises_clearly_on_garbage_content(tmp_path: Path) -> None:
    """Unlike a hard read failure (below), OCCT's ``IGESControl_Reader`` is
    lenient about outright garbage: ``ReadFile`` still reports
    ``IFSelect_RetDone`` for content it can't recognize as IGES at all (0
    entities loaded, verified directly) -- same shape as
    ``meshload.load_mesh``'s trimesh-doesn't-raise-on-garbage-STL case. The
    "no triangulated faces" branch is what actually catches this.
    """
    src = tmp_path / "blob.iges"
    src.write_bytes(b"not-actually-an-iges-file")
    dst = tmp_path / "out.glb"

    with pytest.raises(ValueError, match="no triangulated faces found"):
        cad.iges_to_glb(src, dst)


def test_iges_to_glb_raises_clearly_on_read_failure(tmp_path: Path) -> None:
    """``ReadFile`` itself only reports a non-``RetDone`` status for
    something more fundamental than bad content, e.g. a path that doesn't
    exist at all (verified directly) -- this is what the brief's
    ``ReadFile != IFSelect_RetDone`` branch actually guards.
    """
    dst = tmp_path / "out.glb"

    with pytest.raises(ValueError, match="IGES read failed"):
        cad.iges_to_glb(tmp_path / "does-not-exist.iges", dst)


# ---------------------------------------------------------------------------
# The registered pipeline step, called directly (Task 3/4 convention).
# ---------------------------------------------------------------------------


async def _seed_model_and_revision(db_session: AsyncSession) -> tuple[Model, Revision]:
    model = Model(slug="widget", name="Widget")
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    db_session.add(revision)
    await db_session.flush()
    return model, revision


async def _run_convert_to_glb(
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
        outcome = pipeline._convert_to_glb_step(session, get_settings(), backend, blob)

    return file.blob_hash, outcome


@pytest.mark.parametrize(
    "fixture_name, blob_format, blob_kind, expected_tool",
    [
        ("box_stl", BlobFormat.STL, BlobKind.MESH, "trimesh"),
        ("box_obj", BlobFormat.OBJ, BlobKind.MESH, "trimesh"),
        ("box_3mf_generic", BlobFormat.THREEMF, BlobKind.MESH, "trimesh"),
        ("box_3mf_bambu", BlobFormat.THREEMF, BlobKind.MESH, "lib3mf"),
        ("box_step", BlobFormat.STEP, BlobKind.CAD, "cascadio"),
        ("box_iges", BlobFormat.IGES, BlobKind.CAD, "cadquery-ocp"),
    ],
)
async def test_convert_to_glb_step_every_format_branch(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
    fixture_name: str,
    blob_format: BlobFormat,
    blob_kind: BlobKind,
    expected_tool: str,
) -> None:
    content = getattr(corpus, fixture_name).read_bytes()
    settings = get_settings()

    blob_hash, outcome = await _run_convert_to_glb(
        db_session,
        backend,
        seed_file,
        content,
        rel_path=f"part.{blob_format.value}",
        blob_format=blob_format,
        blob_kind=blob_kind,
    )

    assert outcome == "done"
    with sync_session() as session:
        deriv = session.execute(
            select(Derivative).where(
                Derivative.blob_hash == blob_hash, Derivative.kind == DerivativeKind.GLB
            )
        ).scalar_one()
    assert deriv.status == DerivativeStatus.OK
    assert deriv.tool == expected_tool

    from app.services import derivatives as derivatives_service

    glb_path = derivatives_service.derivative_path(settings, blob_hash, DerivativeKind.GLB)
    assert Path(deriv.local_path) == glb_path
    assert glb_path.read_bytes()[:4] == _GLB_MAGIC
    mesh = _round_trip(glb_path)
    assert len(mesh.faces) == 12
    assert convert.to_z_up(mesh).extents == pytest.approx(EXPECTED_EXTENTS_MM, abs=1e-3)


async def test_convert_to_glb_step_multi_object_no_unit_3mf_succeeds(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
) -> None:
    """Live-bug regression: a unit-less, two-object Production-Extension
    3MF (``corpus.box_3mf_multi_object_no_unit`` -- see its docstring/
    ``meshload``'s for the trimesh multi-geometry-Scene-flatten metadata-loss
    bug this reproduces) must still produce an ``ok`` ``glb`` derivative with
    real geometry, not crash ``mesh.convert_units`` with "No units and not
    allowed to guess!".
    """
    content = corpus_module.box_3mf_multi_object_no_unit()
    settings = get_settings()

    blob_hash, outcome = await _run_convert_to_glb(
        db_session,
        backend,
        seed_file,
        content,
        rel_path="part_multi_no_unit.3mf",
        blob_format=BlobFormat.THREEMF,
        blob_kind=BlobKind.MESH,
    )

    assert outcome == "done"
    with sync_session() as session:
        deriv = session.execute(
            select(Derivative).where(
                Derivative.blob_hash == blob_hash, Derivative.kind == DerivativeKind.GLB
            )
        ).scalar_one()
    assert deriv.status == DerivativeStatus.OK
    assert deriv.tool == "trimesh"

    from app.services import derivatives as derivatives_service

    glb_path = derivatives_service.derivative_path(settings, blob_hash, DerivativeKind.GLB)
    assert glb_path.read_bytes()[:4] == _GLB_MAGIC
    mesh = _round_trip(glb_path)
    assert len(mesh.faces) == 24
    assert convert.to_z_up(mesh).extents == pytest.approx(EXPECTED_EXTENTS_MM, abs=1e-3)


async def test_convert_to_glb_step_skips_when_derivative_already_ok(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    content = corpus.box_stl.read_bytes()
    model, revision = await _seed_model_and_revision(db_session)
    file = await seed_file(
        model, revision, "part.stl", content, blob_format=BlobFormat.STL, blob_kind=BlobKind.MESH
    )

    with sync_session() as session:
        deriv = pipeline.derivatives.upsert_derivative(session, file.blob_hash, DerivativeKind.GLB)
        pipeline.derivatives.mark_derivative(
            session, deriv, status=DerivativeStatus.OK, tool="trimesh", local_path="/x/y.glb"
        )

    with sync_session() as session:
        blob = session.get(Blob, file.blob_hash)
        outcome = pipeline._convert_to_glb_step(session, get_settings(), backend, blob)

    assert outcome == "skipped"
    with sync_session() as session:
        deriv = session.execute(
            select(Derivative).where(
                Derivative.blob_hash == file.blob_hash, Derivative.kind == DerivativeKind.GLB
            )
        ).scalar_one()
    # Unchanged -- a skip must not redo any of the step's work.
    assert deriv.local_path == "/x/y.glb"


async def test_convert_to_glb_truncated_stl_fails_derivative_and_job(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
) -> None:
    content = b"not-actually-an-stl-file"
    model, revision = await _seed_model_and_revision(db_session)
    file = await seed_file(
        model, revision, "part.stl", content, blob_format=BlobFormat.STL, blob_kind=BlobKind.MESH
    )

    job = Job(type="convert_to_glb", subject_type="file", subject_id=file.id, state="queued")
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with pytest.raises(ValueError, match="no geometry found"):
        pipeline.convert_to_glb(str(job.id), file.blob_hash)

    await db_session.refresh(job)
    assert job.state == "failed"
    assert "no geometry found" in job.error

    with sync_session() as session:
        deriv = session.execute(
            select(Derivative).where(
                Derivative.blob_hash == file.blob_hash, Derivative.kind == DerivativeKind.GLB
            )
        ).scalar_one()
    assert deriv.status == DerivativeStatus.FAILED
    assert deriv.error is not None
    assert "no geometry found" in deriv.error
