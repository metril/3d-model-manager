"""Route-level auth sweep (SPEC requirement 1: "every /api route except
/api/auth/login and /api/health requires a valid session cookie").

``app/api/__init__.py`` enforces this with a single router-level
``require_session`` dependency on ``protected_router`` rather than a
per-endpoint check, specifically so no one has to remember to wire auth into
every new endpoint by hand. This test is the other half of that bargain: it
enumerates every operation from the app's own OpenAPI schema (rather than a
route list maintained by hand here, which would drift) and asserts each one
401s with no session cookie -- so a future route that lands outside
``protected_router`` by mistake fails a test immediately instead of shipping
as a silent auth hole.

``/ext/*`` (M10 Workstream A) is the one family of routes NOT gated by
``require_session`` -- it carries its own router-level ``require_api_token``
dependency instead (see ``app.api.ext``). A bare request with no session
cookie AND no bearer token still 401s there, for a different reason, so this
sweep still catches an ``/ext`` route that accidentally loses its auth
dependency; the bearer-specific contract (missing/malformed/revoked token,
and the reverse scope-isolation direction) is covered in
``test_ext_api.py``.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.main import create_app

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")

# The SPEC's two explicit carve-outs (see app/api/__init__.py) -- everything
# else must be behind `require_session`.
PUBLIC_ROUTES = {
    ("get", "/api/health"),
    ("post", "/api/auth/login"),
    # OIDC login flow: pre-session by definition (browser redirects to/from the IdP).
    ("get", "/api/auth/oidc/providers"),
    ("get", "/api/auth/oidc/{slug}/start"),
    ("get", "/api/auth/oidc/{slug}/callback"),
}

# Dummy values for every path parameter used anywhere in the API, so a
# syntactically concrete URL can be built for each route. The values
# themselves don't matter -- these requests are never expected to reach a
# handler, only to prove the router-level auth dependency fires first.
_PATH_PARAM_VALUES = {
    "slug": "placeholder-slug",
    "model_id": "1",
    "user_id": "1",
    "provider_id": "1",
    "group_id": "1",
    "revision_id": "1",
    "revision_a_id": "1",
    "revision_b_id": "2",
    "file_id": "1",
    "archive_id": "1",
    "filename": "placeholder.stl",
    "note_id": "1",
    "name": "placeholder-tag",
    "tag_id": "1",
    "job_id": str(uuid.uuid4()),
    "blob_hash": "a" * 64,
    "index": "1",
    "id": "1",
    "printer_id": "1",
    "import_id": "1",
    "backend_id": "1",
    # M8 H: followed collections / review queue / remote list browsing.
    "site": "thingiverse",
    "list_id": "1",
    "collection_id": "1",
    "pending_id": "1",
    # M10 Workstream A: browser-extension API-token management.
    "token_id": "1",
    # Branch 4 Task 1: print queue entries.
    "entry_id": "1",
    # Branch 5 Task 1: per-model print history.
    "print_id": "1",
    # R13b: categories.
    "category_id": "1",
    # R13c: materials.
    "material_id": "1",
    # P5 spool tray plan: spools CRUD + AMS slot assignment.
    "spool_id": "1",
    "unit": "0",
    "slot": "0",
    # P8 Task 7: Home Assistant entity links.
    "link_id": "1",
    # P6: slicer profiles + print recipes.
    "profile_id": "1",
    "recipe_id": "1",
    # P7 review/billing plan: finance cost centers.
    "cost_center_id": "1",
}


def _concrete_path(template: str) -> str:
    path = template
    for name, value in _PATH_PARAM_VALUES.items():
        path = path.replace("{" + name + "}", value)
    assert "{" not in path, f"no dummy value registered for a path param in {template!r}"
    return path


def _all_routes() -> list[tuple[str, str]]:
    """``(method, path)`` for every operation in the app's OpenAPI schema.

    Reading the schema (a public, stable FastAPI API) rather than walking
    ``app.routes`` directly keeps this test decoupled from FastAPI's
    internal router representation.
    """
    schema = create_app().openapi()
    return [
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
    ]


_SWEPT_ROUTES = [(m, p) for m, p in _all_routes() if (m.lower(), p) not in PUBLIC_ROUTES]


@pytest.mark.parametrize("method,path", _SWEPT_ROUTES, ids=[f"{m} {p}" for m, p in _SWEPT_ROUTES])
async def test_route_requires_session_cookie(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    """Every swept route must 401 with no session cookie, BEFORE any 404
    (unknown id) or 422 (missing body/query params) its own handler might
    otherwise produce -- proving auth really is enforced ahead of each
    endpoint's own parameter resolution, not just for the routes that happen
    to have their own dedicated auth test.
    """
    url = _concrete_path(path)
    body = {} if method in {"POST", "PUT", "PATCH"} else None

    response = await client.request(method, url, json=body)

    assert response.status_code == 401, (
        f"{method} {url} returned {response.status_code}, expected 401 (auth must win over 404/422)"
    )


def test_sweep_is_not_accidentally_empty(client: httpx.AsyncClient) -> None:
    """Guards the parametrization itself: if ``_all_routes()`` ever came back
    empty (e.g. a refactor breaks ``create_app().openapi()``), the sweep
    above would silently collect zero test cases and "pass" without checking
    anything.
    """
    assert len(_SWEPT_ROUTES) >= 20
