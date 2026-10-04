import httpx
import pytest

from app.config import get_settings
from app.importers import download


def _mock_client(body: bytes, *, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_stream_remote_to_spool_hashes_and_sizes(monkeypatch, data_dir):
    import blake3 as _b3

    body = b"solid cube\nendsolid cube\n"
    monkeypatch.setattr(download, "_download_client", lambda: _mock_client(body))
    get_settings.cache_clear()
    staged = download.stream_remote_to_spool(
        get_settings(), url="https://files.test/cube.stl", rel_path="cube.stl"
    )
    assert staged.size == len(body)
    assert staged.blob_hash == _b3.blake3(body).hexdigest()
    assert staged.rel_path == "cube.stl" and staged.spool_path.read_bytes() == body


def test_stream_remote_to_spool_http_error_leaves_no_spool(monkeypatch, data_dir):
    monkeypatch.setattr(download, "_download_client", lambda: _mock_client(b"nope", status=404))
    get_settings.cache_clear()
    with pytest.raises(httpx.HTTPStatusError):
        download.stream_remote_to_spool(
            get_settings(), url="https://files.test/missing.stl", rel_path="missing.stl"
        )
    # spool dir exists but holds no leftover file
    spooled = list((get_settings().data_dir / "spool").glob("*"))
    assert spooled == []


# ---------------------------------------------------------------------------
# feat/import-fidelity T2: Content-Type -> extension mapping + the
# `rel_path_from_response` override `app.tasks.importing`'s gallery-image
# download uses when a URL's own path suffix isn't a recognizable image
# extension.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type,expected",
    [
        ("image/png", "png"),
        ("image/jpeg", "jpg"),
        ("image/webp", "webp"),
        ("image/png; charset=binary", "png"),
        ("IMAGE/PNG", "png"),
        ("text/html", None),
        ("", None),
        (None, None),
    ],
)
def test_image_ext_from_content_type(content_type, expected):
    assert download.image_ext_from_content_type(content_type) == expected


def test_stream_remote_to_spool_rel_path_from_response_overrides_the_placeholder(
    monkeypatch, data_dir
):
    body = b"fake-webp-bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "image/webp"})

    monkeypatch.setattr(
        download,
        "_download_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
    )
    get_settings.cache_clear()
    staged = download.stream_remote_to_spool(
        get_settings(),
        url="https://files.test/cover",  # no recognizable suffix
        rel_path="images/01-cover.placeholder",
        rel_path_from_response=lambda resp: (
            f"images/01-cover.{download.image_ext_from_content_type(resp.headers.get('content-type'))}"
        ),
    )
    assert staged.rel_path == "images/01-cover.webp"
    assert staged.spool_path.read_bytes() == body


def test_stream_remote_to_spool_rel_path_from_response_raising_leaves_no_spool(
    monkeypatch, data_dir
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>not an image</html>", headers={"content-type": "text/html"}
        )

    monkeypatch.setattr(
        download,
        "_download_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
    )
    get_settings.cache_clear()

    def _boom(resp: httpx.Response) -> str:
        raise ValueError(f"unrecognized image content-type {resp.headers.get('content-type')!r}")

    with pytest.raises(ValueError, match="unrecognized image content-type"):
        download.stream_remote_to_spool(
            get_settings(),
            url="https://files.test/cover",
            rel_path="images/01-cover.placeholder",
            rel_path_from_response=_boom,
        )
    spooled = list((get_settings().data_dir / "spool").glob("*"))
    assert spooled == []


def test_stream_remote_to_spool_enforces_max_download_bytes(monkeypatch, data_dir):
    monkeypatch.setattr(download, "_download_client", lambda: _mock_client(b"x" * 100))
    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "max_download_bytes", 10)
    with pytest.raises(ValueError, match="exceeds"):
        download.stream_remote_to_spool(
            settings, url="https://files.test/big.stl", rel_path="big.stl"
        )
    spool_dir = data_dir / "spool"
    assert not spool_dir.exists() or list(spool_dir.iterdir()) == []
