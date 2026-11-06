"""Per-plate assets for unsliced project 3MFs: convert step, backfill, API."""

from __future__ import annotations

import httpx
import pytest
import trimesh
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Blob, BlobMeta, File, Model, Revision
from app.models.enums import BlobFormat, BlobKind
from app.services import derivatives
from app.tasks import pipeline
from app.tasks.base import sync_session
from tests.conftest import BACKEND_DIR
from tests.threemf_builder import make_coloured_project_3mf, make_project_3mf

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


@pytest.fixture(autouse=True)
def chained(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture backfill self-chaining instead of hitting the broker."""
    calls: list[dict] = []
    monkeypatch.setattr(
        pipeline.backfill_plates, "apply_async", lambda *a, **k: calls.append(k), raising=False
    )
    return calls


async def _seed(db: AsyncSession, seed_file, data: bytes, slug: str = "plates"):
    model = Model(slug=slug, name=slug)
    db.add(model)
    await db.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001")
    db.add(rev)
    await db.flush()
    model.current_revision_id = rev.id
    f = await seed_file(
        model, rev, "proj.3mf", data, blob_format=BlobFormat.THREEMF, blob_kind=BlobKind.MESH
    )
    db.add(BlobMeta(blob_hash=f.blob_hash))
    await db.commit()
    return model, f


def _convert(backend, blob_hash: str):
    with sync_session() as session:
        blob = session.get(Blob, blob_hash)
        return pipeline._convert_to_glb_step(session, get_settings(), backend, blob)


async def test_convert_writes_plate_glbs_thumbs_and_meta(
    db_session: AsyncSession, backend, seed_file
) -> None:
    _, f = await _seed(db_session, seed_file, make_project_3mf(3, names=["A", "B", "C"]))
    assert _convert(backend, f.blob_hash) == "done"
    s = get_settings()
    meta = await db_session.get(BlobMeta, f.blob_hash, populate_existing=True)
    assert meta.plate_count == 3
    assert [p["name"] for p in meta.plates_json] == ["A", "B", "C"]
    assert all(
        p["has_glb"] and p["fits"] is True and p["object_count"] == 1 for p in meta.plates_json
    )
    assert meta.plates_json[0]["bbox"] == [20.0, 20.0, 20.0]
    assert all(p["bed"] == [256.0, 256.0] or len(p["bed"]) == 2 for p in meta.plates_json)
    assert meta.plates_json[0]["origin"] == [0.0, 0.0]
    assert meta.plates_json[1]["origin"][0] > meta.plates_json[1]["bed"][0]  # next column, gapped
    for i in (1, 2, 3):
        glb = derivatives.plate_glb_path(s, f.blob_hash, i)
        mesh = trimesh.load(glb, force="mesh")
        assert len(mesh.faces) == 12 and mesh.bounds[1][:2].max() <= 180
        assert mesh.bounds[0][1] == pytest.approx(0.0, abs=1e-3)  # Y-up: sits on y=0
        assert derivatives.plate_thumb_path(s, f.blob_hash, i).read_bytes().startswith(b"\x89PNG")


async def test_single_plate_gets_one_entry_plates_json(
    db_session: AsyncSession, backend, seed_file
) -> None:
    _, f = await _seed(db_session, seed_file, make_project_3mf(1), "single")
    _convert(backend, f.blob_hash)
    meta = await db_session.get(BlobMeta, f.blob_hash, populate_existing=True)
    assert len(meta.plates_json) == 1
    assert meta.plates_json[0]["has_glb"] is False and meta.plates_json[0]["filaments"] == []
    assert meta.plates_version == pipeline.PLATES_VERSION
    assert not derivatives.plate_glb_path(get_settings(), f.blob_hash, 1).exists()


async def test_cap_skips_glbs_above_16_plates(db_session: AsyncSession, backend, seed_file) -> None:
    _, f = await _seed(db_session, seed_file, make_project_3mf(17, bed=100.0), "many")
    _convert(backend, f.blob_hash)
    meta = await db_session.get(BlobMeta, f.blob_hash, populate_existing=True)
    assert len(meta.plates_json) == 17 and not any(p["has_glb"] for p in meta.plates_json)
    assert derivatives.plate_thumb_path(get_settings(), f.blob_hash, 17).exists()


async def test_backfill_analyses_existing_3mf(db_session: AsyncSession, backend, seed_file) -> None:
    _, f = await _seed(db_session, seed_file, make_project_3mf(3), "backfill")
    assert pipeline.backfill_plates() == 1
    meta = await db_session.get(BlobMeta, f.blob_hash, populate_existing=True)
    assert len(meta.plates_json) == 3
    assert pipeline.backfill_plates() == 0


async def test_backfill_refreshes_plates_missing_bed(
    db_session: AsyncSession, backend, seed_file
) -> None:
    _, f = await _seed(db_session, seed_file, make_project_3mf(3), "legacybed")
    assert pipeline.backfill_plates() == 1
    meta = await db_session.get(BlobMeta, f.blob_hash, populate_existing=True)
    meta.plates_json = [
        {k: v for k, v in p.items() if k not in ("bed", "origin")} for p in meta.plates_json
    ]
    await db_session.commit()
    assert pipeline.backfill_plates() == 1
    meta = await db_session.get(BlobMeta, f.blob_hash, populate_existing=True)
    assert all(p["bed"] and p["origin"] for p in meta.plates_json)
    assert pipeline.backfill_plates() == 0


async def test_plate_endpoints_and_file_plates(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, backend, seed_file
) -> None:
    model, f = await _seed(db_session, seed_file, make_project_3mf(3, names=["A", "B", "C"]), "api")
    _convert(backend, f.blob_hash)
    h = f.blob_hash
    r = await authenticated_client.get(f"/api/blobs/{h}/plates/2/glb")
    assert r.status_code == 200 and r.content[:4] == b"glTF"
    assert r.headers["content-type"] == "model/gltf-binary"
    r = await authenticated_client.get(f"/api/blobs/{h}/plates/2/thumb")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    assert (await authenticated_client.get(f"/api/blobs/{h}/plates/9/glb")).status_code == 404
    assert (await authenticated_client.get(f"/api/blobs/{h}/plates/9/thumb")).status_code == 404
    assert (
        await authenticated_client.get(f"/api/blobs/{'0' * 64}/plates/1/glb")
    ).status_code == 404
    assert (await authenticated_client.get(f"/api/blobs/{h}/plates/0/glb")).status_code == 422

    r = await authenticated_client.get(f"/api/models/{model.slug}")
    assert r.status_code == 200, r.text
    files = [x for rev in [r.json()] for x in (rev.get("current_revision") or {}).get("files", [])]
    plates = files[0]["meta"]["plates"]
    assert [(p["index"], p["name"], p["has_glb"], p["thumbnail_available"]) for p in plates] == [
        (1, "A", True, True),
        (2, "B", True, True),
        (3, "C", True, True),
    ]
    assert plates[1]["bed"] and plates[1]["origin"][0] > 0


def test_alembic_single_head() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1


async def test_corrupt_3mf_is_marked_and_backfill_advances(
    db_session: AsyncSession, backend, seed_file
) -> None:
    from sqlalchemy import select as sa_select

    for i in range(pipeline.BACKFILL_BATCH + 2):
        await _seed(db_session, seed_file, b"not a zip %d" % i, f"bad{i}")
    assert pipeline.backfill_plates() == 0
    assert pipeline.backfill_plates() == 0
    pending = (
        (await db_session.execute(sa_select(BlobMeta).where(BlobMeta.plates_json.is_(None))))
        .scalars()
        .all()
    )
    assert pending == []


async def test_stored_fits_uses_no_rotation(db_session: AsyncSession, backend, seed_file) -> None:
    # 20mm cube on a 20mm bed: no margin -> not fit; rotation irrelevant, but the
    # flag must be False-compatible (status derives from allow_rotate=False).
    _, f = await _seed(db_session, seed_file, make_project_3mf(2, bed=20.0), "fitsrot")
    _convert(backend, f.blob_hash)
    meta = await db_session.get(BlobMeta, f.blob_hash, populate_existing=True)
    assert [p["fits"] for p in meta.plates_json] == [False, False]


async def test_backfill_chains_only_on_full_batch(
    db_session: AsyncSession, backend, seed_file, chained: list[dict]
) -> None:
    for i in range(pipeline.BACKFILL_BATCH + 1):
        await _seed(db_session, seed_file, b"not a zip %d" % i, f"chain{i}")
    pipeline.backfill_plates()
    assert chained == [{"countdown": 5}]
    pipeline.backfill_plates()  # one left: partial batch, no re-enqueue
    assert chained == [{"countdown": 5}]


def test_plate_glb_is_y_up(tmp_path) -> None:
    flat = trimesh.creation.box(extents=(70, 40, 3.5))  # print space, Z-up
    pipeline._publish_plate_glb(get_settings(), "f" * 64, 1, flat)
    out = trimesh.load(derivatives.plate_glb_path(get_settings(), "f" * 64, 1), force="mesh")
    assert out.extents == pytest.approx((70, 3.5, 40), abs=1e-3)
    assert flat.extents == pytest.approx((70, 40, 3.5))  # source mesh untouched


async def test_glb_orientation_backfill(
    db_session: AsyncSession, backend, seed_file, chained, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import select as sa_select

    from app.models import Derivative
    from app.models.enums import DerivativeKind
    from app.pipeline import convert

    monkeypatch.setattr(
        pipeline.backfill_glb_orientation,
        "apply_async",
        lambda *a, **k: chained.append(k),
        raising=False,
    )
    _, f = await _seed(db_session, seed_file, make_project_3mf(3), "orient")
    _convert(backend, f.blob_hash)

    async def version() -> int:
        d = (
            await db_session.execute(
                sa_select(Derivative).where(
                    Derivative.blob_hash == f.blob_hash, Derivative.kind == DerivativeKind.GLB
                )
            )
        ).scalar_one()
        await db_session.refresh(d)
        return d.version

    assert await version() == convert.GLB_VERSION  # fresh conversions are current
    assert pipeline.backfill_glb_orientation() == 0  # nothing stale

    with sync_session() as s:
        d = pipeline._glb_derivative(s, f.blob_hash)
        d.version = 0
        s.commit()
    assert pipeline.backfill_glb_orientation() == 1
    assert await version() == convert.GLB_VERSION
    assert chained == []  # partial batch: no chaining
    assert pipeline.backfill_glb_orientation() == 0

    # Failures are stamped -1 and never retried; a full batch chains.
    with sync_session() as s:
        d = pipeline._glb_derivative(s, f.blob_hash)
        d.version = 0
        s.commit()
    monkeypatch.setattr(pipeline, "BACKFILL_BATCH", 1)
    monkeypatch.setattr(
        pipeline, "_regenerate_glb", lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert pipeline.backfill_glb_orientation() == 0
    assert await version() == -1
    assert len(chained) == 1 and chained[0]["countdown"] == 5
    assert pipeline.backfill_glb_orientation() == 0


async def test_filament_backfill_keeps_glbs_and_fills_source_meta(
    db_session: AsyncSession, backend, seed_file
) -> None:
    _, f = await _seed(db_session, seed_file, make_coloured_project_3mf(profile_meta=True), "fil")
    assert pipeline.backfill_plates() == 1
    meta = await db_session.get(BlobMeta, f.blob_hash, populate_existing=True)
    assert [p["filaments"] for p in meta.plates_json] == [
        [{"slot": 1, "colour": "#FF0000"}, {"slot": 3, "colour": "#0000FF"}],
        [{"slot": 2, "colour": "#00FF00"}],
    ]
    assert meta.plates_json[0]["colour_changes"] == []
    assert meta.plates_json[1]["colour_changes"] == [{"z": 10.0, "slot": 3, "colour": "#0000FF"}]
    sm = (await db_session.get(File, f.id, populate_existing=True)).source_meta
    assert sm["profile_id"] == 777 and sm["title"] == "Fast profile" and sm["author"] == "alice"
    # simulate a pre-feature row: no filament keys, stale version, NULL source_meta
    glb = derivatives.plate_glb_path(get_settings(), f.blob_hash, 1)
    mtime = glb.stat().st_mtime_ns
    meta.plates_json = [
        {k: v for k, v in p.items() if k not in ("filaments", "colour_changes")}
        for p in meta.plates_json
    ]
    meta.plates_version = None
    (await db_session.get(File, f.id)).source_meta = {"profile_id": 1}  # non-NULL: untouched
    await db_session.commit()
    assert pipeline.backfill_plates() == 1
    meta = await db_session.get(BlobMeta, f.blob_hash, populate_existing=True)
    assert meta.plates_json[1]["filaments"] == [{"slot": 2, "colour": "#00FF00"}]
    assert glb.stat().st_mtime_ns == mtime  # GLB not regenerated
    assert (await db_session.get(File, f.id, populate_existing=True)).source_meta == {
        "profile_id": 1
    }
    assert pipeline.backfill_plates() == 0


async def test_start_pipeline_fills_source_meta_for_deduped_file(
    db_session: AsyncSession, backend, seed_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, f = await _seed(
        db_session, seed_file, make_coloured_project_3mf(profile_meta=True), "dedup"
    )
    monkeypatch.setattr(pipeline, "enqueue_step_sync", lambda *a, **k: None)
    with sync_session() as session:
        pipeline.start_pipeline_sync(session, blob_hash=f.blob_hash, file_id=f.id)
    sm = (await db_session.get(File, f.id, populate_existing=True)).source_meta
    assert sm["profile_id"] == 777
    # non-NULL is never overwritten
    (await db_session.get(File, f.id)).source_meta = {"profile_id": 1}
    await db_session.commit()
    with sync_session() as session:
        pipeline.start_pipeline_sync(session, blob_hash=f.blob_hash, file_id=f.id)
    assert (await db_session.get(File, f.id, populate_existing=True)).source_meta == {
        "profile_id": 1
    }
