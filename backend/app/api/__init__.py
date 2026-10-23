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

``printers.camera_public_router`` (P4 Task 4: camera API) is the same
pattern as ``files.public_router`` -- its stream/snapshot routes accept a
session cookie OR a scoped camera token, so each does its own auth
(``app.api.printers._authorize_camera``) instead of sitting under
``protected_router``.
"""

from fastapi import APIRouter, Depends

from app.api import (
    admin,
    admin_oidc,
    archives,
    auth,
    blobs,
    categories,
    collections,
    events,
    ext,
    external_links,
    features,
    files,
    finance,
    home_assistant,
    imports,
    jobs,
    materials,
    models,
    notes,
    oidc,
    print_flow,
    print_jobs,
    printer_control,
    printers,
    prints,
    profiles,
    queue,
    reports,
    revisions,
    scan,
    sensors,
    settings,
    slicer,
    spoolman,
    spools,
    stats,
    storage_tree,
    tags,
    uploads,
    virtual_printers,
)
from app.api.deps import require_session
from app.api.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth.public_router)
api_router.include_router(oidc.public_router)
api_router.include_router(ext.router)
api_router.include_router(slicer.router)
api_router.include_router(files.public_router)
api_router.include_router(printers.camera_public_router)

protected_router = APIRouter(dependencies=[Depends(require_session)])
protected_router.include_router(auth.protected_router)
protected_router.include_router(admin.router)
protected_router.include_router(admin_oidc.router)
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
protected_router.include_router(home_assistant.router)
protected_router.include_router(sensors.router)
protected_router.include_router(spoolman.router)
protected_router.include_router(external_links.router)
protected_router.include_router(features.router)
# printer_control first: its literal /printers/developer-mode-warnings must
# match before printers.router's /printers/{printer_id}.
protected_router.include_router(printer_control.router)
protected_router.include_router(printers.router)
protected_router.include_router(print_flow.router)
protected_router.include_router(print_jobs.router)
protected_router.include_router(prints.router)
protected_router.include_router(archives.router)
protected_router.include_router(imports.router)
protected_router.include_router(collections.router)
protected_router.include_router(queue.router)
protected_router.include_router(finance.router)
protected_router.include_router(reports.router)
protected_router.include_router(stats.router)
protected_router.include_router(categories.router)
protected_router.include_router(materials.router)
protected_router.include_router(storage_tree.router)
protected_router.include_router(spools.router)
protected_router.include_router(profiles.router)
protected_router.include_router(profiles.slicer_status_router)
protected_router.include_router(virtual_printers.router)

api_router.include_router(protected_router)
