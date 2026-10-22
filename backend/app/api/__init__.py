"""Aggregate API router mounted under ``/api`` by the app factory.

Split per the SPEC auth rule ("every /api route except /api/auth/login and
/api/health requires a valid session cookie"): ``health_router`` and
``auth.public_router`` stay outside any auth dependency, while
``protected_router`` carries a single router-level ``require_session``
dependency so everything included under it is auth-gated without
per-endpoint copy-paste. Later tasks add their routers to
``protected_router``, not to ``api_router`` directly.

``ext.router`` (M10 Workstream A: browser-extension endpoints) is the one
other exception -- it's mounted directly on ``api_router`` like
``health_router``/``auth.public_router`` above, but it is NOT unauthenticated:
it carries its own router-level ``require_api_token`` dependency (a separate,
narrowly-scoped bearer-token auth plane, deliberately kept independent of
``require_session`` -- see ``app.api.ext``'s module docstring).

``slicer.router`` (Round 8 Task 4: Bambu Studio post-processing intake) is
mounted the same way, for the same reason -- a Bambu Studio post-processing
script can't present the httponly session cookie either, so it authenticates
with the SAME bearer-token plane ``ext.router`` uses.

``files.public_router`` (R10-C: slicer deep links) is mounted the same way
too, but for the opposite reason: it exposes signed-token download auth as
an ALTERNATIVE to the session cookie on ``GET .../download``, so it can't
sit under ``protected_router``'s blanket ``require_session`` -- see
``app.api.files``'s module docstring. It stays a 3-line stub next to
``files.router`` (which keeps the session-gated delete + slicer-link
endpoints) rather than a whole separate module.
"""

from fastapi import APIRouter, Depends

from app.api import (
    auth,
    blobs,
    categories,
    collections,
    events,
    ext,
    features,
    files,
    imports,
    jobs,
    materials,
    models,
    notes,
    print_jobs,
    printers,
    prints,
    queue,
    reports,
    revisions,
    scan,
    settings,
    slicer,
    stats,
    storage_tree,
    tags,
    uploads,
)
from app.api.deps import require_session
from app.api.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth.public_router)
api_router.include_router(ext.router)
api_router.include_router(slicer.router)
api_router.include_router(files.public_router)

protected_router = APIRouter(dependencies=[Depends(require_session)])
protected_router.include_router(auth.protected_router)
protected_router.include_router(models.router)
protected_router.include_router(blobs.router)
protected_router.include_router(revisions.router)
protected_router.include_router(files.router)
protected_router.include_router(tags.router)
protected_router.include_router(notes.router)
protected_router.include_router(uploads.router)
protected_router.include_router(jobs.router)
protected_router.include_router(events.router)
protected_router.include_router(scan.router)
protected_router.include_router(settings.router)
protected_router.include_router(settings.api_tokens_router)
protected_router.include_router(features.router)
protected_router.include_router(printers.router)
protected_router.include_router(print_jobs.router)
protected_router.include_router(prints.router)
protected_router.include_router(imports.router)
protected_router.include_router(collections.router)
protected_router.include_router(queue.router)
protected_router.include_router(reports.router)
protected_router.include_router(stats.router)
protected_router.include_router(categories.router)
protected_router.include_router(materials.router)
protected_router.include_router(storage_tree.router)

api_router.include_router(protected_router)
