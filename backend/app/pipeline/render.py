"""f3d-based headless mesh thumbnail rendering (SPEC pipeline rows 5-6;
RESEARCH §4's verified engine/options/render_to_image snippet).

Renders the raw, uncompressed ``glb`` DERIVATIVE ROW only -- never
``glb_web``/``glb_preview`` (Global Constraints "Two GLB artifacts per
blob": those are meshopt-compressed via gltfpack ``-cc``, and f3d -- like
trimesh/OCCT -- cannot read `EXT_meshopt_compression`/`KHR_mesh_quantization`
back). ``app.tasks.pipeline``'s ``render_thumb`` step and
``render_assembly_thumb`` task are the two callers.

Engine creation (Global Constraints "Local prerequisites"): ``create_osmesa()``
is the container path (Task 10's Docker image installs ``libosmesa6`` plus the
unversioned ``libOSMesa.so`` symlink f3d actually dlopens); ``create(True)``
(EGL offscreen) is this project's verified dev-host fallback -- neither a
GPU/X server nor libosmesa6 is available locally, but Mesa's EGL offscreen
platform renders fine. Mesa prints ``libEGL warning:`` lines to stderr while
probing render nodes it can't open -- expected noise, not a bug (pytest
captures it per-test).

That fallback is *guarded*, because on a host with NEITHER backend the two
calls fail very differently: ``create_osmesa()`` raises cleanly ("Cannot find
OSMesa library"), but ``create(True)`` SIGSEGVs inside Mesa when there is no
GPU render node and no swrast driver -- verified on a bare ubuntu:24.04
container and on a GitHub Actions runner, where it took down a whole pytest
process (exit 139) mid-suite. A segfault can't be caught in-process, so the
EGL call is rehearsed in a throwaway child process (``_egl_probe_failure``)
and only repeated here if that child survived; otherwise this raises a
``RuntimeError`` naming the OSMesa fix. Cost on the supported path is exactly
zero -- the container has OSMesa, ``create_osmesa()`` succeeds, and the probe
never spawns.

The engine is built once and cached per worker process: creating a fresh one
per render would be wasteful, and the worker's memory-recycled ``cpu`` pool
(SPEC "Architecture") bounds whatever state accumulates in one across many
renders.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import f3d

# `app.pipeline`'s other modules are pure file-in/file-out and deliberately
# leave logging to `app.tasks.pipeline`, which owns the job/derivative
# bookkeeping around them. Engine creation is the exception: it's a
# once-per-process environment fact the caller can't see (it happens inside
# the `lru_cache` below, not on any call it makes), so "you are not on the
# supported rendering path" has to be said here or nowhere. Same
# `logging.getLogger(__name__)` convention as app/tasks/*, app/services/*.
logger = logging.getLogger(__name__)

_RENDER_OPTIONS: dict[str, object] = {
    "ui.axis": False,
    "render.grid.enable": False,
    "render.effect.antialiasing.mode": "ssaa",
    "render.effect.ambient_occlusion": True,
}

# Exactly what `_engine()` is about to do on the EGL branch, plus a tiny
# render -- engine creation alone doesn't touch the driver hard enough to
# trip the crash, and a 32x32 render of the empty scene does (and is
# otherwise free: no file is loaded, the image is discarded).
_EGL_PROBE_SCRIPT = """\
import f3d

f3d.Engine.autoload_plugins()
engine = f3d.Engine.create(True)
engine.window.size = 32, 32
engine.window.render_to_image(False)
"""

# Generous relative to a healthy probe (~1-2s) and to `_run_gltfpack`'s 300s,
# because this bounds a HANG, not work: a broken EGL stack that blocks in
# driver init would otherwise wedge the worker forever, which is no better
# than the crash this guards against.
_EGL_PROBE_TIMEOUT = 60


def _egl_probe_failure() -> str | None:
    """Rehearse EGL engine creation in a child process, returning ``None`` if
    it survived or a one-line diagnostic if it didn't.

    Both non-zero exits and timeouts count as failure: a segfault surfaces as
    a negative returncode (``-signal.SIGSEGV``) rather than an exception, and
    a wedged driver init is as unusable as a crashed one. The child's stderr
    is carried back because it (plus the signal number) is the ONLY diagnostic
    an operator gets -- nothing is raised in this process to inspect.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _EGL_PROBE_SCRIPT],
            capture_output=True,
            timeout=_EGL_PROBE_TIMEOUT,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return f"EGL probe timed out after {_EGL_PROBE_TIMEOUT}s (driver init hung)"
    if proc.returncode == 0:
        return None
    if proc.returncode < 0:
        try:
            name = signal.Signals(-proc.returncode).name
        except ValueError:  # pragma: no cover -- unknown/nonstandard signal
            name = "unknown signal"
        status = f"killed by {name} (returncode {proc.returncode})"
    else:
        status = f"exited {proc.returncode}"
    return f"EGL probe {status}; stderr: {(proc.stderr or '').strip()[-2000:]}"


def _is_cache_error(exc: BaseException) -> bool:
    """True when f3d failed on its cache dir (permissions/filesystem), not on GL."""
    msg = str(exc).lower()
    return "could not use cache" in msg or "cannot create directories" in msg


@lru_cache
def _engine() -> f3d.Engine:
    """The process-wide cached f3d engine: OSMesa if available, else EGL
    offscreen (subprocess-probed first -- see the module docstring).
    ``lru_cache`` (rather than a bare module global) gives this a single
    well-defined construction point that's also trivial to reset in tests
    (``_engine.cache_clear()``) if a test ever needs a fresh engine, and it's
    what bounds the probe to at most one spawn per process -- no second cache
    layer needed.
    """
    f3d.Engine.autoload_plugins()
    try:
        engine = f3d.Engine.create_osmesa()
    except Exception as osmesa_exc:
        if _is_cache_error(osmesa_exc):
            raise RuntimeError(
                "f3d could not use its cache directory (not a GL backend problem): "
                f"{osmesa_exc}. Make $XDG_CACHE_HOME (or ~/.cache) writable by the "
                "worker user -- docker/Dockerfile sets XDG_CACHE_HOME=/var/cache/tdmm."
            ) from osmesa_exc
        failure = _egl_probe_failure()
        if failure is not None:
            raise RuntimeError(
                "f3d found no usable headless GL backend, so thumbnails cannot be "
                "rendered on this host. OSMesa is the supported path: install the "
                "`libosmesa6` package AND create the unversioned `libOSMesa.so` "
                "symlink f3d dlopens (only `libosmesa6-dev` ships that name, e.g. "
                "`ln -s libOSMesa.so.8 libOSMesa.so` in the multiarch lib dir) -- "
                "docker/Dockerfile is the reference setup. The EGL fallback was "
                "tried in a child process and did not survive, so it is not used "
                f"here (it would take this process down with it). {failure}"
            ) from osmesa_exc
        logger.warning(
            "f3d: OSMesa unavailable (%s), falling back to EGL offscreen -- this is "
            "the dev-host path, not the supported one (see docker/Dockerfile)",
            osmesa_exc,
        )
        engine = f3d.Engine.create(True)
    engine.options.update(_RENDER_OPTIONS)
    return engine


def render_glb_png(glb: Path, out_png: Path, size: int = 1024) -> None:
    """Render ``glb`` (a raw, uncompressed ``glb`` derivative or a merged
    assembly GLB) to a ``size`` x ``size`` PNG at ``out_png``, reusing the
    cached per-process engine.

    ``out_png``'s parent directory must already exist -- this is pure
    rendering, not derivative-store plumbing (callers stage/publish through
    ``app.services.derivatives`` themselves, exactly like every other step's
    tool invocation in this pipeline). The scene is cleared after every
    render (success or failure) so the next caller -- another blob's
    ``render_thumb`` step, or the assembly task's merged-scene render --
    starts from an empty scene rather than accumulating geometry.
    """
    engine = _engine()
    engine.scene.add(str(glb))
    try:
        engine.window.size = size, size
        engine.window.render_to_image(False).save(str(out_png))
    finally:
        engine.scene.clear()
