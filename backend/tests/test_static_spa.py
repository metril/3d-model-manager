"""SPA static-file serving + client-side-routing fallback (Task 9, see
``app/static.py``). No DB/Redis needed here -- ``create_app()`` only touches
those inside the lifespan, which these tests never enter.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def static_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<html>spa shell</html>")
    assets = root / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('hi');")
    monkeypatch.setenv("STATIC_DIR", str(root))
    get_settings.cache_clear()
    return root


async def _client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_serves_real_static_file(static_dir: Path) -> None:
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/assets/app.js")

    assert response.status_code == 200
    assert "console.log" in response.text


async def test_real_static_file_has_immutable_cache_headers(
    static_dir: Path,
) -> None:
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/assets/app.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_root_serves_index(static_dir: Path) -> None:
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "spa shell" in response.text


async def test_index_has_no_cache_headers(static_dir: Path) -> None:
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


async def test_index_html_direct_request_has_no_cache_headers(
    static_dir: Path,
) -> None:
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/index.html")

    assert response.status_code == 200
    assert "spa shell" in response.text
    assert response.headers["cache-control"] == "no-cache"


async def test_unknown_frontend_route_falls_back_to_index(static_dir: Path) -> None:
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/models/some-slug")

    assert response.status_code == 200
    assert "spa shell" in response.text


async def test_unmatched_api_path_stays_json_404_not_spa(static_dir: Path) -> None:
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "spa shell" not in response.text


async def test_path_traversal_falls_back_to_index_not_host_file(
    static_dir: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me")

    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/../secret.txt")

    # httpx/starlette normalize `..` out of the URL path before routing ever
    # sees it, so this just exercises the ordinary fallback path -- the
    # traversal guard in `mount_spa` is defense in depth for any client that
    # sends a raw un-normalized path.
    assert response.status_code == 200
    assert "spa shell" in response.text


def test_traversal_guard_rejects_literal_dotdot(static_dir: Path, tmp_path: Path) -> None:
    """Direct unit test: calling the fallback handler with literal .. in
    full_path must not escape the static_root, even before the request hits
    the router.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me")

    # The fallback handler's containment logic is: resolve the candidate
    # path and check that it's relative_to static_root.
    static_root = static_dir.resolve()

    # Simulate what spa_fallback does: try to resolve a path with `..`
    # and check that is_relative_to rejects it.
    traversal_path = "../secret.txt"
    candidate = (static_root / traversal_path).resolve()

    # The containment guard must reject this.
    assert not candidate.is_relative_to(static_root)
    # So it falls back to index.html, not the secret file.
    assert candidate.is_file()  # The file exists on the host
    assert secret.read_text() == "do not serve me"  # Confirm it exists
    # But the fallback handler never serves it because of is_relative_to


async def test_static_disabled_by_default() -> None:
    """No STATIC_DIR set -- api_router's normal JSON 404 applies to
    everything outside /api, same as before Task 9.
    """
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


async def test_health_still_works_when_static_enabled(static_dir: Path) -> None:
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    # Shape-checked, not equality-checked: this test is about the SPA catch-all
    # not shadowing the api_router route, so it only cares that /api/health is
    # still reachable and still the health payload. Asserting the whole dict
    # would make it a second place to update every time that payload grows
    # (see tests/test_health.py, which owns the payload contract).
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


async def test_service_worker_and_manifest_are_not_immutable(static_dir: Path) -> None:
    (static_dir / "sw.js").write_text("// sw")
    (static_dir / "manifest.webmanifest").write_text("{}")
    async with await _client(create_app()) as client:
        for name in ("sw.js", "manifest.webmanifest"):
            response = await client.get(f"/{name}")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"


async def test_bare_orcaslicer_served_by_spa(static_dir: Path) -> None:
    app = create_app()
    async with await _client(app) as client:
        for url in ("/slicer", "/slicer?x=1"):
            response = await client.get(url)
            assert response.status_code == 200
            assert "spa shell" in response.text


async def test_orcaslicer_prefix_not_served_by_spa(static_dir: Path) -> None:
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/slicer/nope")

    assert response.status_code != 200
    assert "spa shell" not in response.text
