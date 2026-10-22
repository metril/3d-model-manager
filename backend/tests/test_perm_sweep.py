"""CI guard: every mutating route must be permission-gated.

Enumerates routes from the app, so a new mutating route without a guard
fails here unless it is explicitly allowlisted (with a reason).
"""

from __future__ import annotations

import pytest

from tests.test_auth_sweep import PUBLIC_ROUTES, _all_routes, _concrete_path

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")

# Mutating routes a viewer may legitimately call. P2-P8 extend this list.
VIEWER_ALLOWLIST: set[tuple[str, str]] = {
    # Self-service session/credential actions: every logged-in user.
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/password"),
    # Personal extension tokens: any user manages their own.
    ("POST", "/api/settings/api-tokens"),
    ("DELETE", "/api/settings/api-tokens/{token_id}"),
    # Mints a signed download link for a file the viewer can already read.
    ("POST", "/api/files/{file_id}/slicer-link"),
}

# Bearer-token planes carry their own auth, not the session permission model.
_TOKEN_PLANE = ("/api/ext/", "/api/slicer/")
_MUTATING = [
    (m, p)
    for m, p in _all_routes()
    if m in {"POST", "PUT", "PATCH", "DELETE"}
    and (m.lower(), p) not in PUBLIC_ROUTES
    and not p.startswith(_TOKEN_PLANE)
    and (m, p) not in VIEWER_ALLOWLIST
]


@pytest.mark.usefixtures("printer_enabled")
@pytest.mark.parametrize("method,path", _MUTATING, ids=[f"{m} {p}" for m, p in _MUTATING])
async def test_viewer_gets_403_on_mutating_route(viewer_client, method, path):
    r = await viewer_client.request(
        method, _concrete_path(path), json={} if method != "DELETE" else None
    )
    assert r.status_code == 403, (
        f"{method} {path} -> {r.status_code}; add require_perm or allowlist it"
    )


def test_mutating_sweep_not_empty():
    assert len(_MUTATING) >= 60


def test_allowlist_entries_exist():
    assert set(_all_routes()) >= VIEWER_ALLOWLIST
