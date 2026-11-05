"""``render_thumb`` pipeline step tests (Task 6; SPEC pipeline rows 5-6;
RESEARCH §4): ``app.pipeline.render.render_glb_png`` as a pure function
against real f3d (no mocking the tool under test -- Global Constraints "Real
infra in tests"), the engine-creation guard that keeps a host with no usable
GL backend from core-dumping the worker, then the registered step itself for
both branches: mesh/cad blobs render their already-converted ``glb``
derivative, png/jpg blobs go straight to ``thumbs.make_thumbs_from_image`` on
the original bytes.

Render assertions follow the brief's exact recipe: real PNG magic bytes, the
requested dimensions, and non-uniform pixel content
(``len(set(img.tobytes())) > 1``) to prove the render isn't just a blank
frame.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import f3d
import pytest
import trimesh
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Blob, Derivative, Model, Revision
from app.models.enums import BlobFormat, BlobKind, DerivativeKind, DerivativeStatus
from app.pipeline import render
from app.services import derivatives
from app.storage.local import LocalStorageBackend
from app.tasks import pipeline
from app.tasks.base import sync_session
from tests.corpus import CorpusPaths

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _distinct_byte_values(png_path: Path) -> int:
    with Image.open(png_path) as image:
        return len(set(image.tobytes()))


# ---------------------------------------------------------------------------
# app.pipeline.render.render_glb_png: pure function, real f3d.
# ---------------------------------------------------------------------------


def test_render_glb_png_produces_real_nonuniform_png_at_requested_size(tmp_path: Path) -> None:
    box = trimesh.creation.box(extents=(20.0, 10.0, 5.0))
    glb_path = tmp_path / "box.glb"
    box.export(glb_path)
    out_png = tmp_path / "out.png"

    render.render_glb_png(glb_path, out_png, size=512)

    assert out_png.read_bytes()[:8] == _PNG_MAGIC
    with Image.open(out_png) as image:
        assert image.size == (512, 512)
    assert _distinct_byte_values(out_png) > 1


def test_render_glb_png_engine_is_reused_across_calls(tmp_path: Path) -> None:
    """Two renders in a row must both succeed -- proving the cached engine
    (``scene.clear()`` between renders) tolerates reuse rather than only
    working once.
    """
    box = trimesh.creation.box(extents=(20.0, 10.0, 5.0))
    glb_path = tmp_path / "box.glb"
    box.export(glb_path)

    out_1 = tmp_path / "out1.png"
    out_2 = tmp_path / "out2.png"
    render.render_glb_png(glb_path, out_1, size=256)
    render.render_glb_png(glb_path, out_2, size=256)

    for out in (out_1, out_2):
        assert out.read_bytes()[:8] == _PNG_MAGIC
        with Image.open(out) as image:
            assert image.size == (256, 256)


# ---------------------------------------------------------------------------
# `render._engine` backend selection. `create_osmesa()` raises when OSMesa is
# missing, but `create(True)` SIGSEGVs when EGL is unusable -- so the EGL
# branch is rehearsed in a child process first. These tests drive the child's
# outcome by faking `subprocess.run`'s result rather than hand-writing the
# diagnostic, so `_egl_probe_failure`'s own returncode/signal formatting is
# under test too.
# ---------------------------------------------------------------------------


@pytest.fixture
def _fresh_engine_cache():
    """``_engine`` is ``lru_cache``d, so a test that exercises its
    construction branches has to drop the process-wide engine on the way in
    AND on the way out -- otherwise it either never runs its branch (an
    earlier render test already cached a real engine) or leaves a stub behind
    for every test after it.
    """
    render._engine.cache_clear()
    yield
    render._engine.cache_clear()


class _StubEngine:
    """Stand-in for ``f3d.Engine``: ``_engine`` only touches ``.options``."""

    def __init__(self) -> None:
        self.options: dict[str, object] = {}


def _no_osmesa(*args, **kwargs):
    """What real f3d does on a host without the unversioned libOSMesa.so."""
    raise RuntimeError("Cannot find OSMesa library")


@pytest.mark.usefixtures("_fresh_engine_cache")
def test_engine_prefers_osmesa_and_never_spawns_the_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supported (container) path must cost exactly nothing: OSMesa is
    tried in-process because it fails safely, and the subprocess probe exists
    only to guard the branch after it.
    """
    stub = _StubEngine()

    def _probe_boom():
        raise AssertionError("the EGL probe must not spawn when OSMesa works")

    monkeypatch.setattr(f3d.Engine, "create_osmesa", lambda: stub)
    monkeypatch.setattr(render, "_egl_probe_failure", _probe_boom)

    engine = render._engine()

    assert engine is stub
    assert stub.options == render._RENDER_OPTIONS


@pytest.mark.usefixtures("_fresh_engine_cache")
def test_engine_falls_back_to_egl_when_the_probe_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dev-host path: no OSMesa, but the child process came back clean,
    so the EGL branch is the one taken.

    `create(True)` is stubbed rather than genuinely called. What's under test
    is control flow -- probe first, EGL only after it survives -- and the real
    call is precisely what SIGSEGVs on a host with no swrast driver, so
    invoking it here would hand this test a core dump as its failure mode, on
    the exact code path it exists to stop core-dumping. It does survive on a
    dev box and on CI, but only because libgl1-mesa-dri is installed there for
    OSMesa's sake -- far too incidental to hang a test's safety on. Real EGL
    creation is covered out-of-process, and crash-proof, by the probe test
    below.
    """
    calls: list[object] = []
    stub = _StubEngine()

    def _probe_ok():
        calls.append("probe")
        return None

    def _create(offscreen):
        calls.append(("create", offscreen))
        return stub

    monkeypatch.setattr(f3d.Engine, "create_osmesa", _no_osmesa)
    monkeypatch.setattr(render, "_egl_probe_failure", _probe_ok)
    monkeypatch.setattr(f3d.Engine, "create", _create)

    engine = render._engine()

    # Order is the safety property: the probe has to run BEFORE the call it
    # guards, not merely at some point during construction.
    assert calls == ["probe", ("create", True)]
    assert engine is stub
    assert stub.options == render._RENDER_OPTIONS


@pytest.mark.usefixtures("_fresh_engine_cache")
def test_engine_raises_actionable_error_when_the_probe_segfaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the CI core dump: with neither backend available
    `create(True)` dies on SIGSEGV instead of raising, which took a whole
    pytest process down with exit 139 mid-suite. The child absorbs that (a
    signal death surfaces as a negative returncode, not an exception), and
    `create(True)` must never be reached in THIS process -- the whole point
    is that the operator gets a message rather than a core file.
    """
    runs: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        runs.append(cmd)
        return subprocess.CompletedProcess(cmd, -11, stdout="", stderr="libEGL warning: DRI2\n")

    def _create_boom(*args, **kwargs):
        raise AssertionError("create(True) must not run once the probe reported a crash")

    monkeypatch.setattr(f3d.Engine, "create_osmesa", _no_osmesa)
    monkeypatch.setattr(f3d.Engine, "create", _create_boom)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        render._engine()

    message = str(excinfo.value)
    assert "libosmesa6" in message
    assert "libOSMesa.so" in message
    assert "docker/Dockerfile" in message
    # The returncode/signal and the child's stderr are the only diagnostics
    # an operator gets -- nothing was raised in-process to inspect.
    assert "SIGSEGV" in message
    assert "-11" in message
    assert "libEGL warning: DRI2" in message
    # The original OSMesa failure stays chained, so both halves of "no usable
    # backend" are visible in one traceback.
    assert "Cannot find OSMesa library" in str(excinfo.value.__cause__)
    # A real child process, not an in-process call -- the only arrangement
    # that can survive the crash being guarded against.
    assert runs == [[sys.executable, "-c", render._EGL_PROBE_SCRIPT]]


@pytest.mark.usefixtures("_fresh_engine_cache")
def test_a_real_child_segfault_becomes_a_catchable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guarantee end to end, with the mechanism itself unmocked: a child
    that genuinely dies on SIGSEGV comes back as an exception this process can
    catch, and this process is still alive to catch it.

    The sibling test above fakes `subprocess.run`'s result, which pins the
    message formatting but would pass just as happily if the isolation were
    bogus -- if the probe script were exec'd in-process, say. Only a real
    signal death proves the isolation is real. `ctypes.string_at(0)` is an
    instant, reliable SIGSEGV that needs no f3d import, so this costs
    milliseconds; the genuine EGL crash it stands in for can't be summoned on
    demand, since it depends on the host's driver stack and does NOT reproduce
    inside a plain container (f3d's wheel bundles its own Mesa).

    A regression here doesn't show up as a red test: the whole suite dies with
    exit 139, which is precisely the symptom this guard exists to prevent.
    """
    monkeypatch.setattr(f3d.Engine, "create_osmesa", _no_osmesa)
    monkeypatch.setattr(render, "_EGL_PROBE_SCRIPT", "import ctypes; ctypes.string_at(0)")

    with pytest.raises(RuntimeError) as excinfo:
        render._engine()

    assert "SIGSEGV" in str(excinfo.value)
    assert "libosmesa6" in str(excinfo.value)


@pytest.mark.usefixtures("_fresh_engine_cache")
def test_engine_raises_actionable_error_when_the_probe_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver init that wedges is as unusable as one that crashes, and
    worse for a worker: without the timeout the job would hang forever
    instead of failing. Same clean `RuntimeError` as the segfault branch.
    """

    def _fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(f3d.Engine, "create_osmesa", _no_osmesa)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        render._engine()

    message = str(excinfo.value)
    assert "timed out" in message
    assert "libosmesa6" in message
    assert "docker/Dockerfile" in message


def test_egl_probe_helper_really_spawns_a_child_and_never_raises() -> None:
    """The probe unmocked, on this host -- the only test that proves the
    child script itself is valid. A probe that fell over on its own code
    (typo, un-importable f3d) would report "EGL unusable" on every host,
    including the many where it is fine, turning a working dev box into a
    hard error. Dev boxes have Mesa EGL but no OSMesa, so this comes back
    clean; the shape assertion holds on a GL-less host too, where the
    contract that matters is "returns a diagnostic instead of taking this
    process down". Costs ~1s: one interpreter start, 32x32 of an empty scene.
    """
    result = render._egl_probe_failure()

    assert result is None or result.startswith("EGL probe"), result
    assert "SyntaxError" not in (result or "")
    assert "ModuleNotFoundError" not in (result or "")


# ---------------------------------------------------------------------------
# The registered `render_thumb` step, called directly (Task 3-5 convention).
# ---------------------------------------------------------------------------


async def _seed_model_and_revision(db_session: AsyncSession) -> tuple[Model, Revision]:
    model = Model(slug="widget", name="Widget")
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    db_session.add(revision)
    await db_session.flush()
    return model, revision


async def _seed_blob_with_ok_glb(
    db_session: AsyncSession,
    seed_file,
    corpus: CorpusPaths,
    *,
    rel_path: str = "part.stl",
) -> str:
    """Seed a file/blob and a real, ``ok`` ``glb`` derivative for it --
    mirrors ``test_pipeline_optimize.py``'s helper of the same shape.
    """
    settings = get_settings()
    content = corpus.box_stl.read_bytes()
    model, revision = await _seed_model_and_revision(db_session)
    file = await seed_file(
        model, revision, rel_path, content, blob_format=BlobFormat.STL, blob_kind=BlobKind.MESH
    )

    glb_path = derivatives.derivative_path(settings, file.blob_hash, DerivativeKind.GLB)
    glb_path.parent.mkdir(parents=True, exist_ok=True)
    box = trimesh.creation.box(extents=(20.0, 10.0, 5.0))
    glb_path.write_bytes(box.export(file_type="glb"))

    db_session.add(
        Derivative(
            blob_hash=file.blob_hash,
            kind=DerivativeKind.GLB,
            status=DerivativeStatus.OK,
            local_path=str(glb_path),
            tool="trimesh",
        )
    )
    await db_session.commit()
    return file.blob_hash


async def test_render_thumb_mesh_renders_and_publishes_both_thumbs(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    settings = get_settings()
    blob_hash = await _seed_blob_with_ok_glb(db_session, seed_file, corpus)

    with sync_session() as session:
        blob = session.get(Blob, blob_hash)
        outcome = pipeline._render_thumb_step(session, settings, backend, blob)

    assert outcome == "done"

    p1024 = derivatives.derivative_path(settings, blob_hash, DerivativeKind.THUMB_1024)
    p256 = derivatives.derivative_path(settings, blob_hash, DerivativeKind.THUMB_256)

    assert p1024.read_bytes()[:8] == _PNG_MAGIC
    assert p256.read_bytes()[:8] == _PNG_MAGIC
    with Image.open(p1024) as image:
        assert image.size == (1024, 1024)
    with Image.open(p256) as image:
        assert image.size == (256, 256)
    assert _distinct_byte_values(p1024) > 1

    with sync_session() as session:
        rows = {
            d.kind: d
            for d in session.query(Derivative).filter(Derivative.blob_hash == blob_hash).all()
            if d.kind in (DerivativeKind.THUMB_1024, DerivativeKind.THUMB_256)
        }
    assert rows[DerivativeKind.THUMB_1024].status == DerivativeStatus.OK
    assert rows[DerivativeKind.THUMB_1024].tool == "f3d"
    assert rows[DerivativeKind.THUMB_256].status == DerivativeStatus.OK
    assert rows[DerivativeKind.THUMB_256].tool == "f3d"


async def test_render_thumb_skips_when_both_thumbs_already_ok(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    blob_hash = await _seed_blob_with_ok_glb(db_session, seed_file, corpus)

    with sync_session() as session:
        blob = session.get(Blob, blob_hash)
        first = pipeline._render_thumb_step(session, settings, backend, blob)
    assert first == "done"

    def _boom(*args, **kwargs):
        raise AssertionError("render_glb_png must not be called again once thumbs are ok")

    monkeypatch.setattr(render, "render_glb_png", _boom)

    with sync_session() as session:
        blob = session.get(Blob, blob_hash)
        second = pipeline._render_thumb_step(session, settings, backend, blob)

    assert second == "skipped"


async def test_render_thumb_missing_glb_derivative_raises(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    settings = get_settings()
    content = corpus.box_stl.read_bytes()
    model, revision = await _seed_model_and_revision(db_session)
    file = await seed_file(
        model, revision, "part.stl", content, blob_format=BlobFormat.STL, blob_kind=BlobKind.MESH
    )

    with sync_session() as session:
        blob = session.get(Blob, file.blob_hash)
        with pytest.raises(RuntimeError, match="glb missing"):
            pipeline._render_thumb_step(session, settings, backend, blob)


async def test_render_thumb_render_failure_marks_both_derivatives_failed(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global Constraints "Failure semantics": a render failure must mark
    the derivative row(s) ``failed`` (with ``error``/``tool``) AND fail the
    job -- not just fail the job while leaving THUMB_1024/256 with no row at
    all, which is what a naive "only touch derivative rows on success" flow
    would do.
    """
    settings = get_settings()
    blob_hash = await _seed_blob_with_ok_glb(db_session, seed_file, corpus)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated f3d render failure")

    monkeypatch.setattr(render, "render_glb_png", _boom)

    with sync_session() as session:
        blob = session.get(Blob, blob_hash)
        with pytest.raises(RuntimeError, match="simulated f3d render failure"):
            pipeline._render_thumb_step(session, settings, backend, blob)

    with sync_session() as session:
        rows = {
            d.kind: d
            for d in session.query(Derivative).filter(Derivative.blob_hash == blob_hash).all()
            if d.kind in (DerivativeKind.THUMB_1024, DerivativeKind.THUMB_256)
        }
    assert rows[DerivativeKind.THUMB_1024].status == DerivativeStatus.FAILED
    assert rows[DerivativeKind.THUMB_1024].tool == "f3d"
    assert "simulated f3d render failure" in rows[DerivativeKind.THUMB_1024].error
    assert rows[DerivativeKind.THUMB_256].status == DerivativeStatus.FAILED
    assert rows[DerivativeKind.THUMB_256].tool == "f3d"


async def test_render_thumb_image_blob_uses_pillow(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    settings = get_settings()
    model, revision = await _seed_model_and_revision(db_session)
    content = corpus.red_png.read_bytes()
    file = await seed_file(
        model, revision, "cover.png", content, blob_format=BlobFormat.PNG, blob_kind=BlobKind.IMAGE
    )

    with sync_session() as session:
        blob = session.get(Blob, file.blob_hash)
        outcome = pipeline._render_thumb_step(session, settings, backend, blob)

    assert outcome == "done"

    p1024 = derivatives.derivative_path(settings, file.blob_hash, DerivativeKind.THUMB_1024)
    p256 = derivatives.derivative_path(settings, file.blob_hash, DerivativeKind.THUMB_256)
    assert p1024.read_bytes()[:8] == _PNG_MAGIC
    assert p256.read_bytes()[:8] == _PNG_MAGIC
    # `red.png` is 64x64 -- `make_thumbs_from_image` never upscales.
    with Image.open(p1024) as image:
        assert image.size == (64, 64)

    with sync_session() as session:
        deriv = (
            session.query(Derivative)
            .filter(
                Derivative.blob_hash == file.blob_hash, Derivative.kind == DerivativeKind.THUMB_1024
            )
            .one()
        )
    assert deriv.tool == "pillow"


async def test_render_thumb_webp_blob_uses_pillow(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file,
    corpus: CorpusPaths,
) -> None:
    """feat/import-fidelity T1: ``BlobFormat.WEBP`` must route through the
    same image branch as png/jpg (``_IMAGE_FORMATS``), not the mesh/glb
    branch -- a blob whose format is webp but got missed there would blow up
    on the ``glb missing`` ``RuntimeError`` instead of rendering a thumb.
    """
    settings = get_settings()
    model, revision = await _seed_model_and_revision(db_session)
    content = corpus.red_webp.read_bytes()
    file = await seed_file(
        model,
        revision,
        "cover.webp",
        content,
        blob_format=BlobFormat.WEBP,
        blob_kind=BlobKind.IMAGE,
    )

    with sync_session() as session:
        blob = session.get(Blob, file.blob_hash)
        outcome = pipeline._render_thumb_step(session, settings, backend, blob)

    assert outcome == "done"

    p1024 = derivatives.derivative_path(settings, file.blob_hash, DerivativeKind.THUMB_1024)
    p256 = derivatives.derivative_path(settings, file.blob_hash, DerivativeKind.THUMB_256)
    # `_publish_thumb` always re-encodes as PNG regardless of the source
    # format -- both derivatives are PNGs on disk even though the source
    # blob was webp.
    assert p1024.read_bytes()[:8] == _PNG_MAGIC
    assert p256.read_bytes()[:8] == _PNG_MAGIC
    with Image.open(p1024) as image:
        assert image.size == (64, 64)

    with sync_session() as session:
        deriv = (
            session.query(Derivative)
            .filter(
                Derivative.blob_hash == file.blob_hash, Derivative.kind == DerivativeKind.THUMB_1024
            )
            .one()
        )
    assert deriv.tool == "pillow"


# ---------------------------------------------------------------------------
# End-to-end: the registered step actually runs through the full pipeline
# now that Task 6 fills it in (mesh chains no longer stop at optimize_glb).
# ---------------------------------------------------------------------------


async def test_stl_upload_pipeline_runs_through_render_thumb(
    authenticated_client,
    corpus: CorpusPaths,
) -> None:
    response = await authenticated_client.post(
        "/api/models", json={"name": "Render Pipeline Target"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    revision_id = body["current_revision"]["id"]

    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": body["id"], "revision_id": revision_id, "rel_path": "part.stl"},
        content=corpus.box_stl.read_bytes(),
    )
    assert upload.status_code == 201, upload.text

    jobs_resp = await authenticated_client.get("/api/jobs")
    jobs_list = jobs_resp.json()
    for step in ("extract_metadata", "convert_to_glb", "optimize_glb", "render_thumb"):
        step_job = next(j for j in jobs_list if j["type"] == step)
        assert step_job["state"] == "done", (step, step_job)


@pytest.mark.usefixtures("_fresh_engine_cache")
def test_engine_reports_cache_error_not_missing_gl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _cache_denied(*args, **kwargs):
        raise RuntimeError(
            "Could not use cache: filesystem error: cannot create directories: "
            "Permission denied [/home/tdmm/.cache/f3d]"
        )

    def _probe_boom():
        raise AssertionError("cache errors must not reach the EGL probe")

    monkeypatch.setattr(f3d.Engine, "create_osmesa", _cache_denied)
    monkeypatch.setattr(render, "_egl_probe_failure", _probe_boom)

    with pytest.raises(RuntimeError) as excinfo:
        render._engine()

    assert "cache directory" in str(excinfo.value)
    assert "no usable headless GL backend" not in str(excinfo.value)
