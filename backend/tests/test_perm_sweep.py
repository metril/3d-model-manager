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
    # T6: self-service two-factor enrolment (acts only on the caller's own account).
    ("POST", "/api/auth/2fa/totp/setup"),
    ("POST", "/api/auth/2fa/totp/enable"),
    ("POST", "/api/auth/2fa/totp/disable"),
    ("POST", "/api/auth/2fa/backup/regenerate"),
    ("POST", "/api/auth/2fa/email/request"),
    ("POST", "/api/auth/2fa/email/enable"),
    ("POST", "/api/auth/2fa/email/confirm"),
    ("POST", "/api/auth/2fa/email/disable"),
    # T8a: each user manages their own emailed-print-event preferences.
    ("PUT", "/api/user-notifications/preferences"),
    # Personal extension tokens: any user manages their own.
    ("POST", "/api/settings/api-tokens"),
    ("DELETE", "/api/settings/api-tokens/{token_id}"),
    ("POST", "/api/settings/api-keys"),
    ("DELETE", "/api/settings/api-keys/{key_id}"),
    # Mints a signed download link for a file the viewer can already read.
    ("POST", "/api/files/{file_id}/slicer-link"),
    # P5 spool tray plan: a plan preview mutates nothing -- per-printer
    # access (when a printer_id is given) is checked inside the handler
    # itself, not via a global permission gate.
    ("POST", "/api/print/plan"),
    # T7: archive edits are gated in-handler (admin, job owner, or printers:print + ACL).
    ("PATCH", "/api/archives/{archive_id}"),
    ("POST", "/api/archives/{archive_id}/verdict"),
    ("POST", "/api/archives/{archive_id}/photos"),
    ("DELETE", "/api/archives/{archive_id}/photos/{filename}"),
    ("PUT", "/api/archives/{archive_id}/source"),
    ("DELETE", "/api/archives/{archive_id}/source"),
    ("PUT", "/api/archives/{archive_id}/f3d"),
    ("DELETE", "/api/archives/{archive_id}/f3d"),
    ("POST", "/api/archives/{archive_id}/sources/link-model"),
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


def test_q5_routes_are_gated():
    """Q5 mutating printer routes must not be viewer-allowlisted."""
    q5 = {
        ("POST", "/api/printers/discover"),
        ("POST", "/api/printers/diagnostic"),
        ("POST", "/api/printers/{printer_id}/hms/clear"),
        ("POST", "/api/printers/{printer_id}/hms/action"),
        ("POST", "/api/printers/{printer_id}/firmware/upload"),
    }
    assert q5 <= set(_all_routes())
    assert q5 <= set(_MUTATING)
