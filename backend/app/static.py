"""SPA static-file serving with client-side-routing fallback (Task 9:
Docker/compose integration).

Enabled only when ``STATIC_DIR`` is set (default ``None``, i.e.
disabled for local development, where the Vite dev server serves the
frontend and proxies ``/api`` to this backend instead -- see README
"Development"). When enabled, ``app.main.create_app`` calls ``mount_spa``,
which adds a single catch-all ``GET`` route, registered AFTER
``api_router`` so it never shadows an actual API route:

- ``GET /api/...`` for anything ``api_router`` didn't already match falls
  through to FastAPI's normal JSON 404 -- this route explicitly declines
  any path starting with ``api/`` rather than serving the SPA shell for it.
- A path that resolves to a real file under ``static_dir`` is served as-is
  (JS/CSS/images/etc, and the SPA's own ``index.html`` if requested by
  name or as ``/``).
- Anything else falls back to ``index.html`` so the client-side router
  (TanStack Router) gets a chance to render the matching page -- e.g.
  ``/models/some-slug`` has no file on disk but is a valid frontend route.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse

_API_PREFIX = "api"


_NO_CACHE_FILES = {"sw.js", "manifest.webmanifest"}


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Register the SPA catch-all route against ``app``, serving files (and
    the SPA fallback) from ``static_dir``. Caller is responsible for only
    calling this when ``static_dir/index.html`` actually exists.
    """
    static_root = static_dir.resolve()
    index_file = static_root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path == _API_PREFIX or full_path.startswith(f"{_API_PREFIX}/"):
            # Never serve the SPA shell for an unmatched /api/* path -- it
            # keeps 404ing as normal JSON, via FastAPI's default handler.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

        candidate = (static_root / full_path).resolve()
        # `is_relative_to` rejects path-traversal attempts (e.g.
        # `../../etc/passwd`) before ever touching the filesystem outside
        # static_root.
        if candidate.is_relative_to(static_root) and candidate.is_file():
            # index.html is the mutable entry point; always send no-cache so
            # the browser checks for updates.
            # Same for the service worker + manifest: unhashed, and a stale
            # sw.js would pin users to an old app shell.
            if candidate == index_file or full_path in _NO_CACHE_FILES:
                return FileResponse(
                    candidate,
                    headers={"Cache-Control": "no-cache"},
                )
            # Other static files (JS/CSS/images) have content-hashed filenames
            # emitted by Vite, so they are immutable and safe to cache forever.
            return FileResponse(
                candidate,
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
        # Fallback to index.html for client-side routing; must not be cached
        # so the browser always gets fresh HTML to check for updates.
        return FileResponse(
            index_file,
            headers={"Cache-Control": "no-cache"},
        )
