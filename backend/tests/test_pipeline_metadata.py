"""``extract_metadata`` pipeline step tests (Task 3; SPEC pipeline row 1;
RESEARCH §1/§3): ``app.pipeline.meshload``/``app.pipeline.slicedmeta`` as
pure functions against the procedural corpus, then the registered pipeline
step itself (called directly, like M1's ingest tests) for every format
branch -- including the lib3mf Production-Extension fallback, the
skip-if-already-extracted idempotency, the CAD-from-GLB branch and its
"pipeline order broken" failure mode -- plus one API-driven eager upload
proving the end-to-end wiring and the dedup skip on a second identical
upload.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import trimesh
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Blob, BlobMeta, Derivative, Job, Model, Revision
from app.models.enums import BlobFormat, BlobKind, DerivativeKind, DerivativeStatus
from app.pipeline import meshload, slicedmeta
from app.storage.local import LocalStorageBackend
from app.tasks import pipeline
from app.tasks.base import sync_session
from app.tasks.pipeline import UnsupportedBlobError
from tests import corpus
from tests.corpus import CorpusPaths

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")

EXPECTED_EXTENTS_MM = (20.0, 10.0, 5.0)
EXPECTED_VOLUME_CM3 = 1.0
EXPECTED_AREA_CM2 = 7.0


# ---------------------------------------------------------------------------
# app.pipeline.meshload: pure functions against the corpus.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name, fmt",
    [
        ("box_stl", BlobFormat.STL),
        ("box_obj", BlobFormat.OBJ),
        ("box_3mf_generic", BlobFormat.THREEMF),
    ],
)
def test_load_mesh_native_formats_use_trimesh(
    corpus: CorpusPaths, fixture_name: str, fmt: BlobFormat
) -> None:
    path = getattr(corpus, fixture_name)

    loaded = meshload.load_mesh(path, fmt)

    assert loaded.tool == "trimesh"
    assert len(loaded.mesh.faces) == 12
    assert loaded.mesh.extents == pytest.approx(EXPECTED_EXTENTS_MM)
    assert loaded.mesh.is_watertight
    assert loaded.mesh.volume == pytest.approx(1000.0)
    assert loaded.mesh.area == pytest.approx(700.0)


def test_load_mesh_3mf_meter_unit_normalizes_to_mm(corpus: CorpusPaths) -> None:
    """``box_3mf_meter`` describes the same 20x10x5 mm box in metres
    (``unit="meter"``, coordinates /1000) -- trimesh parses the ``<model
    unit>`` attribute but never applies it, so before the U1 fix this comes
    back as a ~1000x-too-small ``(0.02, 0.01, 0.005)`` mesh.
    """
    loaded = meshload.load_mesh(corpus.box_3mf_meter, BlobFormat.THREEMF)

    assert loaded.tool == "trimesh"
    assert loaded.mesh.extents == pytest.approx(EXPECTED_EXTENTS_MM, abs=1e-3)


def test_load_mesh_3mf_multi_object_no_unit_defaults_to_mm(tmp_path: Path) -> None:
    """``box_3mf_multi_object_no_unit``: a two-object Production-Extension
    3MF (each part wrapped in a local ``<components>``/``<component
    p:path=...>``, which -- unlike ``<build><item p:path=...>`` -- trimesh's
    reader DOES follow, so this loads via the trimesh branch, not lib3mf)
    whose root ``<model>`` has NO ``unit`` attribute at all.

    Live-bug regression: trimesh's OWN 3MF loader already tags every
    geometry with the spec-default ``"millimeters"`` units at load time
    (``trimesh.exchange.threemf.load_3MF``), but flattening a
    multi-geometry ``Scene`` to one mesh silently drops ALL per-geometry
    ``metadata`` -- including that units tag -- via a trimesh bug in
    ``Scene.to_geometry()``/``trimesh.util.concatenate`` (verified directly:
    a plain 2-box ``Scene`` loses ``mesh.units`` the same way). Before the
    fix this made ``mesh.convert_units("millimeters", guess=False)`` raise
    "No units and not allowed to guess!" for a file that was never actually
    unit-ambiguous.
    """
    path = tmp_path / "box_multi_no_unit.3mf"
    path.write_bytes(corpus.box_3mf_multi_object_no_unit())

    loaded = meshload.load_mesh(path, BlobFormat.THREEMF)

    assert loaded.tool == "trimesh"
    assert len(loaded.mesh.faces) == 24
    assert loaded.mesh.extents == pytest.approx(EXPECTED_EXTENTS_MM, abs=1e-3)


def test_load_mesh_bambu_3mf_falls_back_to_lib3mf(corpus: CorpusPaths) -> None:
    """The Production-Extension fixture defeats trimesh's build-item
    resolution -- proving the lib3mf fallback actually fired, not just that
    it exists in the code.
    """
    loaded = meshload.load_mesh(corpus.box_3mf_bambu, BlobFormat.THREEMF)

    assert loaded.tool == "lib3mf"
    assert len(loaded.mesh.faces) == 12
    assert loaded.mesh.extents == pytest.approx(EXPECTED_EXTENTS_MM)
    assert loaded.mesh.is_watertight


def test_load_3mf_lib3mf_direct(corpus: CorpusPaths) -> None:
    mesh = meshload.load_3mf_lib3mf(corpus.box_3mf_bambu)

    assert len(mesh.faces) == 12
    assert mesh.extents == pytest.approx(EXPECTED_EXTENTS_MM)


def test_load_3mf_lib3mf_meter_unit_normalizes_to_mm(tmp_path: Path) -> None:
    """Same U1 regression as ``test_load_mesh_3mf_meter_unit_normalizes_to_mm``,
    but driving ``load_3mf_lib3mf`` directly via a Production-Extension
    meter-unit 3MF (``box_3mf_bambu_meter``: same layout as ``box_3mf_bambu``,
    with ``unit="meter"`` and coordinates /1000), proving the lib3mf branch's
    own unit-factor table -- not just the trimesh branch -- normalizes to mm.
    """
    path = tmp_path / "box_bambu_meter.3mf"
    path.write_bytes(corpus.box_3mf_bambu_meter())

    mesh = meshload.load_3mf_lib3mf(path)

    assert len(mesh.faces) == 12
    assert mesh.extents == pytest.approx(EXPECTED_EXTENTS_MM, abs=1e-3)


def test_load_mesh_stl_raises_clearly_on_unparseable_content(tmp_path: Path) -> None:
    """Garbage bytes named ``.stl`` don't make trimesh raise -- it silently
    comes back with an empty ``Scene`` (0 geometries), same shape as the
    Bambu-3mf-defeats-trimesh case above. Unlike 3mf there's no fallback
    tool for stl/obj, so this must surface as a clear, explicit failure
    (a real upload's ``extract_metadata`` job failing with a legible error)
    rather than crashing downstream on ``mesh.extents`` being ``None`` for
    an empty mesh.
    """
    garbage = tmp_path / "blob.stl"
    garbage.write_bytes(b"not-actually-an-stl-file")

    with pytest.raises(ValueError, match="no geometry found"):
        meshload.load_mesh(garbage, BlobFormat.STL)


# ---------------------------------------------------------------------------
# app.pipeline.slicedmeta: pure functions against the corpus.
# ---------------------------------------------------------------------------


def test_parse_gcode_3mf_extracts_bambu_ground_truth(corpus: CorpusPaths) -> None:
    sliced = slicedmeta.parse_gcode_3mf(corpus.sliced_gcode_3mf)

    assert sliced.print_time_s == 5400
    assert sliced.filament_g == pytest.approx(20.0)
    assert sliced.filament_m == pytest.approx(4.82 + 2.41)
    assert sliced.plate_count == 2
    assert sliced.filament_types == ["PLA", "PETG"]
    assert sliced.nozzle == pytest.approx(0.4)
    assert sliced.layer_height == pytest.approx(0.2)
    assert sliced.printer_model == "Bambu Lab A1 mini"

    assert sliced.plates == [
        {
            "index": 1,
            "name": None,
            "prediction_s": 3600,
            "weight_g": pytest.approx(12.5),
            "gcode_file": "Metadata/plate_1.gcode",
            "thumbnail_file": "Metadata/plate_1.png",
            "filaments": [
                {
                    "type": "PLA",
                    "color": "#FF0000",
                    "used_m": pytest.approx(4.82),
                    "used_g": pytest.approx(12.5),
                }
            ],
        },
        {
            "index": 2,
            "name": None,
            "prediction_s": 1800,
            "weight_g": pytest.approx(7.5),
            "gcode_file": "Metadata/plate_2.gcode",
            "thumbnail_file": "Metadata/plate_2.png",
            "filaments": [
                {
                    "type": "PETG",
                    "color": "#0000FF",
                    "used_m": pytest.approx(2.41),
                    "used_g": pytest.approx(7.5),
                }
            ],
        },
    ]


def test_parse_gcode_3mf_missing_plate_index_falls_back_to_document_position(
    corpus: CorpusPaths,
) -> None:
    """Important #1 regression (whole-branch review): a plate whose
    ``slice_info.config`` entry has no ``index`` metadata at all (a slightly
    different slicer version, per the module's own tolerant-of-absence
    design) must still come back with a usable ``int`` index -- falling back
    to its 1-based position in document order -- rather than ``None``, which
    would 500 the model/revision detail endpoint one layer up
    (``PlateOut.index`` is a non-optional ``int``; see
    ``test_pipeline_metadata_endpoint.py``-style coverage below for the
    end-to-end proof).
    """
    sliced = slicedmeta.parse_gcode_3mf(corpus.sliced_gcode_3mf_missing_index)

    assert sliced.plate_count == 2
    assert [p["index"] for p in sliced.plates] == [1, 2]
    # The index-less plate's other fields still parse normally, and its
    # gcode/thumbnail files still resolve via `model_settings.config`'s
    # `plater_id`-keyed mapping -- confirming the positional fallback (2)
    # happens to line up with this fixture's `plater_id`, exactly as a real
    # Bambu Studio export (plates emitted in plater order) would.
    assert sliced.plates[1]["prediction_s"] == 1800
    assert sliced.plates[1]["weight_g"] == pytest.approx(7.5)
    assert sliced.plates[1]["gcode_file"] == "Metadata/plate_2.gcode"
    assert sliced.plates[1]["thumbnail_file"] == "Metadata/plate_2.png"


def test_parse_gcode_3mf_tolerant_of_missing_sources(tmp_path: Path) -> None:
    """None of the three source files existing must not raise -- an export
    from an unrecognized tool just yields an all-empty ``SlicedMeta``.
    """
    import zipfile

    empty_zip = tmp_path / "empty.gcode.3mf"
    with zipfile.ZipFile(empty_zip, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")

    sliced = slicedmeta.parse_gcode_3mf(empty_zip)

    assert sliced == slicedmeta.SlicedMeta(
        print_time_s=None,
        filament_g=None,
        filament_m=None,
        filament_types=[],
        layer_height=None,
        nozzle=None,
        printer_model=None,
        plate_count=0,
        plates=[],
    )


def test_parse_gcode_header_extracts_bambu_ground_truth(corpus: CorpusPaths) -> None:
    header = slicedmeta.parse_gcode_header(corpus.bambu_gcode)

    assert header.print_time_s == 3690
    assert header.filament_g == pytest.approx(12.5)
    assert header.filament_m == pytest.approx(4.8205)
    assert header.layer_count == 175
    assert header.max_z_mm == pytest.approx(35.0)
    assert header.raw["total estimated time"] == "1h 1m 30s"


def test_parse_gcode_header_tolerant_of_missing_header_block(tmp_path: Path) -> None:
    plain_gcode = tmp_path / "plain.gcode"
    plain_gcode.write_bytes(b"; just a comment, no header block\nG28\nG1 X10\n")

    header = slicedmeta.parse_gcode_header(plain_gcode)

    assert header == slicedmeta.GcodeMeta(
        print_time_s=None,
        filament_g=None,
        filament_m=None,
        layer_count=None,
        max_z_mm=None,
        raw={},
    )


# ---------------------------------------------------------------------------
# The registered pipeline step, called directly (M1 ingest-test convention).
# ---------------------------------------------------------------------------


async def _seed_model_and_revision(db_session: AsyncSession) -> tuple[Model, Revision]:
    model = Model(slug="widget", name="Widget")
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    db_session.add(revision)
    await db_session.flush()
    return model, revision


async def _run_extract_metadata(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    content: bytes,
    *,
    rel_path: str,
    blob_format: BlobFormat,
    blob_kind: BlobKind = BlobKind.MESH,
) -> tuple[str, pipeline.StepOutcome]:
    model, revision = await _seed_model_and_revision(db_session)
    file = await seed_file(
        model, revision, rel_path, content, blob_format=blob_format, blob_kind=blob_kind
    )

    with sync_session() as session:
        blob = session.get(Blob, file.blob_hash)
        outcome = pipeline._extract_metadata_step(session, get_settings(), backend, blob)

    return file.blob_hash, outcome


@pytest.mark.parametrize(
    "fixture_name, blob_format",
    [
        ("box_stl", BlobFormat.STL),
        ("box_obj", BlobFormat.OBJ),
        ("box_3mf_generic", BlobFormat.THREEMF),
    ],
)
async def test_extract_metadata_native_mesh_formats(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
    fixture_name: str,
    blob_format: BlobFormat,
) -> None:
    content = getattr(corpus, fixture_name).read_bytes()

    blob_hash, outcome = await _run_extract_metadata(
        db_session,
        backend,
        seed_file,
        content,
        rel_path=f"part.{blob_format.value}",
        blob_format=blob_format,
    )

    assert outcome == "done"
    with sync_session() as session:
        meta = session.get(BlobMeta, blob_hash)
    assert meta.triangle_count == 12
    assert meta.dims_mm == pytest.approx(list(EXPECTED_EXTENTS_MM))
    assert meta.volume_cm3 == pytest.approx(EXPECTED_VOLUME_CM3)
    assert meta.surface_area_cm2 == pytest.approx(EXPECTED_AREA_CM2)
    assert meta.is_watertight is True
    assert meta.raw == {"tool": "trimesh"}


async def test_extract_metadata_3mf_meter_unit_yields_correct_mm_metadata(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    """U1 regression through the full pipeline step: a meter-unit 3MF must
    yield correct mm-scale ``dims_mm``/``volume_cm3``/``surface_area_cm2``,
    not the ~1000x-too-small values the unfixed loader used to produce.
    """
    content = corpus.box_3mf_meter.read_bytes()

    blob_hash, outcome = await _run_extract_metadata(
        db_session,
        backend,
        seed_file,
        content,
        rel_path="part_meter.3mf",
        blob_format=BlobFormat.THREEMF,
    )

    assert outcome == "done"
    with sync_session() as session:
        meta = session.get(BlobMeta, blob_hash)
    assert meta.triangle_count == 12
    assert meta.dims_mm == pytest.approx(list(EXPECTED_EXTENTS_MM))
    assert meta.volume_cm3 == pytest.approx(EXPECTED_VOLUME_CM3)
    assert meta.surface_area_cm2 == pytest.approx(EXPECTED_AREA_CM2)
    assert meta.is_watertight is True
    assert meta.raw == {"tool": "trimesh"}


async def test_extract_metadata_3mf_multi_object_no_unit_succeeds(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
) -> None:
    """Live-bug regression through the full pipeline step (not just the
    pure loader above): a unit-less, two-object 3MF must extract metadata
    successfully instead of ``extract_metadata`` (the FIRST step in
    ``PIPELINE_STEPS`` for every mesh format, ahead of ``convert_to_glb``)
    dying on the exact ``ValueError`` the live crash reported.
    """
    content = corpus.box_3mf_multi_object_no_unit()

    blob_hash, outcome = await _run_extract_metadata(
        db_session,
        backend,
        seed_file,
        content,
        rel_path="part_multi_no_unit.3mf",
        blob_format=BlobFormat.THREEMF,
    )

    assert outcome == "done"
    with sync_session() as session:
        meta = session.get(BlobMeta, blob_hash)
    assert meta.triangle_count == 24
    assert meta.dims_mm == pytest.approx(list(EXPECTED_EXTENTS_MM), abs=1e-3)
    assert meta.raw == {"tool": "trimesh"}


async def test_extract_metadata_bambu_3mf_records_lib3mf_tool(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    content = corpus.box_3mf_bambu.read_bytes()

    blob_hash, outcome = await _run_extract_metadata(
        db_session,
        backend,
        seed_file,
        content,
        rel_path="part.3mf",
        blob_format=BlobFormat.THREEMF,
    )

    assert outcome == "done"
    with sync_session() as session:
        meta = session.get(BlobMeta, blob_hash)
    assert meta.triangle_count == 12
    assert meta.dims_mm == pytest.approx(list(EXPECTED_EXTENTS_MM))
    assert meta.volume_cm3 == pytest.approx(EXPECTED_VOLUME_CM3)
    assert meta.surface_area_cm2 == pytest.approx(EXPECTED_AREA_CM2)
    assert meta.is_watertight is True
    assert meta.raw == {"tool": "lib3mf"}


async def test_extract_metadata_skips_when_row_already_exists(
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

    # Pre-seed a BlobMeta row with a sentinel value a real run would never
    # produce, so "still there unchanged" is a meaningful assertion.
    db_session.add(BlobMeta(blob_hash=file.blob_hash, triangle_count=999999))
    await db_session.commit()

    with sync_session() as session:
        blob = session.get(Blob, file.blob_hash)
        outcome = pipeline._extract_metadata_step(session, get_settings(), backend, blob)

    assert outcome == "skipped"
    with sync_session() as session:
        meta = session.get(BlobMeta, file.blob_hash)
    assert meta.triangle_count == 999999


async def test_extract_metadata_gcode_3mf(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    content = corpus.sliced_gcode_3mf.read_bytes()

    blob_hash, outcome = await _run_extract_metadata(
        db_session,
        backend,
        seed_file,
        content,
        rel_path="print.gcode.3mf",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )

    assert outcome == "done"
    with sync_session() as session:
        meta = session.get(BlobMeta, blob_hash)
    assert meta.print_time_s == 5400
    assert meta.filament_g == pytest.approx(20.0)
    assert meta.plate_count == 2
    assert meta.filament_types == ["PLA", "PETG"]
    assert meta.nozzle == pytest.approx(0.4)
    assert meta.layer_height == pytest.approx(0.2)
    assert meta.printer_model == "Bambu Lab A1 mini"
    assert meta.raw["tool"] == "zipfile"
    assert len(meta.raw["plates"]) == 2
    assert meta.raw["plates"][0]["gcode_file"] == "Metadata/plate_1.gcode"
    # R10-B: plate 1's embedded gcode (see `bambu_gcode`) enriches the zip's
    # own project_settings with layer_count/slicer -- head-only zip read.
    assert meta.layer_count == 175
    assert meta.slicer == "BambuStudio"


async def test_extract_metadata_gcode(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    content = corpus.bambu_gcode.read_bytes()

    blob_hash, outcome = await _run_extract_metadata(
        db_session,
        backend,
        seed_file,
        content,
        rel_path="plate_1.gcode",
        blob_format=BlobFormat.GCODE,
        blob_kind=BlobKind.GCODE,
    )

    assert outcome == "done"
    with sync_session() as session:
        meta = session.get(BlobMeta, blob_hash)
    assert meta.print_time_s == 3690
    assert meta.filament_g == pytest.approx(12.5)
    assert meta.filament_m == pytest.approx(4.8205)
    assert meta.raw["tool"] == "gcode-header"
    assert meta.raw["header"]["total layer number"] == "175"
    # R10-B: same bare-gcode bytes, now also merged through gcode_meta.
    assert meta.layer_count == 175
    assert meta.slicer == "BambuStudio"


async def test_extract_metadata_unsupported_format_raises(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
) -> None:
    """png/jpg/other never route ``extract_metadata`` to this branch via
    ``PIPELINE_STEPS`` -- this only proves the defensive branch itself is
    correct in isolation.
    """
    blob = Blob(hash="f" * 64, size=1, kind=BlobKind.IMAGE, format=BlobFormat.PNG)
    db_session.add(blob)
    await db_session.commit()

    with sync_session() as session:
        blob = session.get(Blob, "f" * 64)
        with pytest.raises(UnsupportedBlobError):
            pipeline._extract_metadata_step(session, get_settings(), backend, blob)


# -- Mesh-format parse failures: glb Derivative persistence -----------------
#
# extract_metadata runs BEFORE convert_to_glb for every mesh format
# (PIPELINE_STEPS), so a load_mesh failure here means convert_to_glb -- the
# step that actually owns the glb Derivative row -- never gets dispatched at
# all (run_step only enqueues the next step from its success path). Without
# `_fail_glb_derivative_from_metadata_step`, this used to leave the blob
# with a failed Job but NO glb Derivative row whatsoever (Global Constraints
# "Failure semantics" calls for a `failed` row, not an absent one) -- the
# live bug's "second symptom".


async def test_extract_metadata_mesh_load_failure_marks_glb_derivative_failed(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise ValueError("No units and not allowed to guess!")

    monkeypatch.setattr(meshload, "load_mesh", _raise)

    model, revision = await _seed_model_and_revision(db_session)
    file = await seed_file(
        model,
        revision,
        "part.3mf",
        b"irrelevant -- load_mesh is monkeypatched to always raise",
        blob_format=BlobFormat.THREEMF,
        blob_kind=BlobKind.MESH,
    )
    job = Job(type="extract_metadata", subject_type="file", subject_id=file.id, state="queued")
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with pytest.raises(ValueError, match="No units and not allowed to guess"):
        pipeline.extract_metadata(str(job.id), file.blob_hash)

    await db_session.refresh(job)
    assert job.state == "failed"
    assert "No units and not allowed to guess" in job.error

    with sync_session() as session:
        deriv = session.execute(
            select(Derivative).where(
                Derivative.blob_hash == file.blob_hash, Derivative.kind == DerivativeKind.GLB
            )
        ).scalar_one()
        assert deriv.status == DerivativeStatus.FAILED
        assert deriv.error is not None
        assert "No units and not allowed to guess" in deriv.error

        # convert_to_glb never even got the chance to run -- extract_metadata
        # failing means run_step never dispatches the next step.
        convert_jobs = list(
            session.execute(
                select(Job).where(Job.type == "convert_to_glb", Job.subject_id == file.id)
            ).scalars()
        )
    assert convert_jobs == []


# -- CAD branch: reads the already-converted GLB derivative -----------------


async def _seed_cad_blob(db_session: AsyncSession, blob_format: BlobFormat) -> str:
    blob_hash = "c" * 64
    db_session.add(Blob(hash=blob_hash, size=1, kind=BlobKind.CAD, format=blob_format))
    await db_session.commit()
    return blob_hash


async def test_extract_metadata_cad_branch_reads_glb_derivative(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    data_dir: Path,
) -> None:
    from app.services import derivatives as derivatives_service

    blob_hash = await _seed_cad_blob(db_session, BlobFormat.STEP)
    settings = get_settings()
    glb_path = derivatives_service.derivative_path(settings, blob_hash, DerivativeKind.GLB)
    glb_path.parent.mkdir(parents=True, exist_ok=True)
    box = trimesh.creation.box(extents=(20.0, 10.0, 5.0))
    glb_path.write_bytes(box.export(file_type="glb"))

    db_session.add(
        Derivative(
            blob_hash=blob_hash,
            kind=DerivativeKind.GLB,
            status=DerivativeStatus.OK,
            local_path=str(glb_path),
            tool="cascadio",
        )
    )
    await db_session.commit()

    with sync_session() as session:
        blob = session.get(Blob, blob_hash)
        outcome = pipeline._extract_metadata_step(session, settings, backend, blob)

    assert outcome == "done"
    with sync_session() as session:
        meta = session.get(BlobMeta, blob_hash)
    assert meta.triangle_count == 12
    assert meta.dims_mm == pytest.approx(list(EXPECTED_EXTENTS_MM))
    assert meta.volume_cm3 == pytest.approx(EXPECTED_VOLUME_CM3)
    assert meta.surface_area_cm2 == pytest.approx(EXPECTED_AREA_CM2)
    assert meta.is_watertight is True
    assert meta.raw == {"tool": "glb-derived"}


@pytest.mark.parametrize("blob_format", [BlobFormat.STEP, BlobFormat.IGES])
async def test_extract_metadata_cad_branch_missing_glb_raises_order_broken(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    blob_format: BlobFormat,
) -> None:
    blob_hash = await _seed_cad_blob(db_session, blob_format)

    with (
        sync_session() as session,
        pytest.raises(RuntimeError, match="glb derivative not ready; pipeline order broken"),
    ):
        blob = session.get(Blob, blob_hash)
        pipeline._extract_metadata_step(session, get_settings(), backend, blob)


async def test_extract_metadata_cad_branch_missing_glb_job_ends_failed(
    db_session: AsyncSession,
) -> None:
    """Same as above, but through the registered task (``run_step``'s own
    job bookkeeping) rather than the bare ``StepFn`` -- the observable
    outcome the brief actually cares about is the ``jobs`` row itself ending
    up ``failed`` with the order-broken message, not just the raw exception.
    """
    blob_hash = await _seed_cad_blob(db_session, BlobFormat.STEP)
    job = Job(type="extract_metadata", subject_type="file", subject_id=1, state="queued")
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with pytest.raises(RuntimeError, match="glb derivative not ready; pipeline order broken"):
        pipeline.extract_metadata(str(job.id), blob_hash)

    await db_session.refresh(job)
    assert job.state == "failed"
    assert "glb derivative not ready; pipeline order broken" in job.error


# ---------------------------------------------------------------------------
# End-to-end: PUT /api/uploads (eager Celery) -> blob_meta row; a second
# upload of identical bytes dedups at the blob level and skips re-extraction.
# ---------------------------------------------------------------------------


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def test_upload_stl_creates_blob_meta_row_second_identical_upload_skips(
    authenticated_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    corpus: CorpusPaths,
) -> None:
    content = corpus.box_stl.read_bytes()
    call_count = {"n": 0}
    real_load_mesh = meshload.load_mesh

    def _counting_load_mesh(path, fmt):
        call_count["n"] += 1
        return real_load_mesh(path, fmt)

    # Both `extract_metadata` and (Task 5) `convert_to_glb` call through this
    # same `app.pipeline.meshload` module object, so patching it here counts
    # calls from either step -- a real STL upload's first pass through the
    # full chain calls it exactly twice (once per step), not once.
    monkeypatch.setattr(pipeline.meshload, "load_mesh", _counting_load_mesh)

    created = await _create_model(authenticated_client, "Metadata Upload Target")
    revision_id = created["current_revision"]["id"]

    first = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "part.stl"},
        content=content,
    )
    assert first.status_code == 201, first.text
    blob_hash = first.json()["blob_hash"]

    with sync_session() as session:
        meta = session.get(BlobMeta, blob_hash)
    assert meta is not None
    assert meta.triangle_count == 12
    assert call_count["n"] == 2

    # A second upload of byte-identical content dedups at the blob level
    # (new File row, same blob_hash) -- its own extract_metadata AND
    # convert_to_glb runs must both skip rather than re-parsing/re-converting.
    second = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "part2.stl"},
        content=content,
    )
    assert second.status_code == 201, second.text
    assert second.json()["blob_hash"] == blob_hash

    with sync_session() as session:
        meta_count = session.scalar(
            select(func.count()).select_from(BlobMeta).where(BlobMeta.blob_hash == blob_hash)
        )
    assert meta_count == 1
    assert call_count["n"] == 2


async def test_upload_gcode_3mf_missing_plate_index_detail_endpoint_200s(
    authenticated_client: httpx.AsyncClient,
    corpus: CorpusPaths,
) -> None:
    """Important #1 regression, end-to-end: before the fix, the index-less
    plate's ``BlobMeta.raw["plates"]`` entry carried ``{"index": None, ...}``,
    which ``PlateOut.from_raw`` (a non-optional ``index: int``) turned into a
    Pydantic ``ValidationError`` -- a 500 on every subsequent
    ``GET /api/revisions/{id}`` for this file, permanently (the bad row is
    already in the DB; ``extract_metadata`` skips on re-run). With the fix,
    metadata extraction succeeds, the detail endpoint 200s, and both plates
    render with their (now always-int) positional indices.
    """
    content = corpus.sliced_gcode_3mf_missing_index.read_bytes()
    created = await _create_model(authenticated_client, "Sliced Missing-Index Target")
    revision_id = created["current_revision"]["id"]

    upload = await authenticated_client.put(
        "/api/uploads",
        params={
            "model_id": created["id"],
            "revision_id": revision_id,
            "rel_path": "print.gcode.3mf",
        },
        content=content,
    )
    assert upload.status_code == 201, upload.text

    detail = await authenticated_client.get(f"/api/revisions/{revision_id}")
    assert detail.status_code == 200, detail.text

    file_out = next(f for f in detail.json()["files"] if f["rel_path"] == "print.gcode.3mf")
    assert file_out["meta"] is not None
    plates = file_out["meta"]["plates"]
    assert [p["index"] for p in plates] == [1, 2]
