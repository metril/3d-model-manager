"""Blob-derivative endpoints (Task 7 brief): immutable-cached thumbnails,
GLBs, per-plate PNGs, and the revision assembly thumbnail. Every scenario
here seeds ``Blob``/``Derivative``/``AssemblyThumb`` rows and derivative
files directly (Task 7's test-speed guidance) rather than running the real
pipeline -- the one real end-to-end enrichment run lives in
``test_uploads_api.py``.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AssemblyThumb, Blob, Derivative, Model, Revision
from app.models.enums import BlobFormat, BlobKind, DerivativeKind, DerivativeStatus
from app.services import derivatives

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")

_PNG_BYTES = b"\x89PNG\r\n\x1a\n-fake-thumb-bytes"
_UNKNOWN_HASH = "0" * 64


async def _seed_blob(
    db_session: AsyncSession,
    *,
    blob_hash: str,
    fmt: BlobFormat = BlobFormat.STL,
    kind: BlobKind = BlobKind.MESH,
) -> Blob:
    blob = Blob(hash=blob_hash, size=123, kind=kind, format=fmt)
    db_session.add(blob)
    await db_session.commit()
    return blob


async def _seed_derivative(
    db_session: AsyncSession,
    *,
    blob_hash: str,
    kind: DerivativeKind,
    status: DerivativeStatus,
    local_path: str | None = None,
) -> Derivative:
    deriv = Derivative(blob_hash=blob_hash, kind=kind, status=status, local_path=local_path)
    db_session.add(deriv)
    await db_session.commit()
    return deriv


async def _seed_model_and_revision(db_session: AsyncSession) -> tuple[Model, Revision]:
    model = Model(slug="widget", name="Widget")
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    db_session.add(revision)
    await db_session.flush()
    model.current_revision_id = revision.id
    await db_session.commit()
    return model, revision


# ---------------------------------------------------------------------------
# GET /api/blobs/{hash}/thumb
# ---------------------------------------------------------------------------


async def test_get_thumb_unknown_blob_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.get(f"/api/blobs/{_UNKNOWN_HASH}/thumb")

    assert response.status_code == 404


async def test_get_thumb_missing_row_is_404_pending(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    blob = await _seed_blob(db_session, blob_hash="a" * 64)

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/thumb")

    assert response.status_code == 404
    assert response.json()["detail"] == "pending"


@pytest.mark.parametrize("status_value", [DerivativeStatus.FAILED, DerivativeStatus.UNSUPPORTED])
async def test_get_thumb_not_ok_derivative_reports_its_status(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    status_value: DerivativeStatus,
) -> None:
    blob = await _seed_blob(db_session, blob_hash="b" * 64)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.THUMB_256, status=status_value
    )

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/thumb")

    assert response.status_code == 404
    assert response.json()["detail"] == status_value.value


async def test_get_thumb_ok_serves_bytes_with_immutable_headers(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    settings = get_settings()
    blob = await _seed_blob(db_session, blob_hash="c" * 64)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.THUMB_256, status=DerivativeStatus.OK
    )
    path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.THUMB_256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_BYTES)

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/thumb")

    assert response.status_code == 200
    assert response.content == _PNG_BYTES
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert response.headers["etag"] == f'"{blob.hash}:thumb_256"'


async def test_get_thumb_size_1024_uses_its_own_derivative(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    settings = get_settings()
    blob = await _seed_blob(db_session, blob_hash="d" * 64)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.THUMB_1024, status=DerivativeStatus.OK
    )
    path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.THUMB_1024)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_BYTES)

    ok = await authenticated_client.get(f"/api/blobs/{blob.hash}/thumb?size=1024")
    missing_256 = await authenticated_client.get(f"/api/blobs/{blob.hash}/thumb?size=256")

    assert ok.status_code == 200
    assert ok.headers["etag"] == f'"{blob.hash}:thumb_1024"'
    assert missing_256.status_code == 404


async def test_get_thumb_if_none_match_returns_304(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    settings = get_settings()
    blob = await _seed_blob(db_session, blob_hash="e" * 64)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.THUMB_256, status=DerivativeStatus.OK
    )
    path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.THUMB_256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_BYTES)

    first = await authenticated_client.get(f"/api/blobs/{blob.hash}/thumb")
    etag = first.headers["etag"]

    second = await authenticated_client.get(
        f"/api/blobs/{blob.hash}/thumb", headers={"If-None-Match": etag}
    )

    assert second.status_code == 304


async def test_get_thumb_if_none_match_multi_value_returns_304(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M2-Minor 4 fold: a client revalidating several cached representations
    sends its whole ``If-None-Match`` list on one request -- a strict
    single-value string compare would only match if the right entry happened
    to be first.
    """
    settings = get_settings()
    blob = await _seed_blob(db_session, blob_hash="1" * 64)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.THUMB_256, status=DerivativeStatus.OK
    )
    path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.THUMB_256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_BYTES)

    first = await authenticated_client.get(f"/api/blobs/{blob.hash}/thumb")
    etag = first.headers["etag"]

    second = await authenticated_client.get(
        f"/api/blobs/{blob.hash}/thumb",
        headers={"If-None-Match": f'"some-other-etag", {etag}'},
    )

    assert second.status_code == 304


async def test_get_thumb_if_none_match_weak_prefix_returns_304(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M2-Minor 4 fold: a weak validator (``W/"..."``, RFC 7232) must still
    match the strong ETag we compute -- the leading ``W/`` is stripped before
    comparing.
    """
    settings = get_settings()
    blob = await _seed_blob(db_session, blob_hash="2" * 64)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.THUMB_256, status=DerivativeStatus.OK
    )
    path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.THUMB_256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_BYTES)

    first = await authenticated_client.get(f"/api/blobs/{blob.hash}/thumb")
    etag = first.headers["etag"]

    second = await authenticated_client.get(
        f"/api/blobs/{blob.hash}/thumb", headers={"If-None-Match": f"W/{etag}"}
    )

    assert second.status_code == 304


async def test_get_thumb_ok_derivative_missing_file_on_disk_is_404(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M2-Minor 4 fold: the DB says `ok`, but the file was removed out of
    band -- this must be a clean 404, not a raw 500 from `FileResponse`
    discovering the file's gone at send time.
    """
    blob = await _seed_blob(db_session, blob_hash="3" * 64)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.THUMB_256, status=DerivativeStatus.OK
    )
    # Deliberately never writes the derivative file to disk.

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/thumb")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/blobs/{hash}/plates/{index}/thumb
# ---------------------------------------------------------------------------


async def test_get_plate_thumb_unknown_blob_is_404(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.get(f"/api/blobs/{_UNKNOWN_HASH}/plates/1/thumb")

    assert response.status_code == 404


async def test_get_plate_thumb_missing_file_is_404(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    blob = await _seed_blob(
        db_session, blob_hash="f" * 64, fmt=BlobFormat.GCODE_3MF, kind=BlobKind.SLICED
    )

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/plates/1/thumb")

    assert response.status_code == 404


async def test_get_plate_thumb_ok_serves_bytes_with_own_etag(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    settings = get_settings()
    blob = await _seed_blob(
        db_session, blob_hash="1" + "a" * 63, fmt=BlobFormat.GCODE_3MF, kind=BlobKind.SLICED
    )
    path = derivatives.plate_thumb_path(settings, blob.hash, 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_BYTES)

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/plates/2/thumb")

    assert response.status_code == 200
    assert response.content == _PNG_BYTES
    assert response.headers["etag"] == f'"{blob.hash}:plate2"'
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"


async def test_get_plate_thumb_negative_index_is_422(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    blob = await _seed_blob(
        db_session, blob_hash="2" + "a" * 63, fmt=BlobFormat.GCODE_3MF, kind=BlobKind.SLICED
    )

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/plates/-1/thumb")

    assert response.status_code == 422


async def test_get_plate_thumb_zero_index_is_422(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    blob = await _seed_blob(
        db_session, blob_hash="3" + "a" * 63, fmt=BlobFormat.GCODE_3MF, kind=BlobKind.SLICED
    )

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/plates/0/thumb")

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/blobs/{hash}/glb
# ---------------------------------------------------------------------------


async def test_get_glb_unknown_blob_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.get(f"/api/blobs/{_UNKNOWN_HASH}/glb")

    assert response.status_code == 404


async def test_get_glb_missing_row_is_404_pending(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    blob = await _seed_blob(db_session, blob_hash="4" + "a" * 63)

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/glb")

    assert response.status_code == 404
    assert response.json()["detail"] == "pending"


async def test_get_glb_failed_derivative_reports_failed(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    blob = await _seed_blob(db_session, blob_hash="5" + "a" * 63)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.GLB, status=DerivativeStatus.FAILED
    )

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/glb")

    assert response.status_code == 404
    assert response.json()["detail"] == "failed"


async def test_get_glb_serves_raw_derivative_when_web_missing(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    settings = get_settings()
    blob = await _seed_blob(db_session, blob_hash="6" + "a" * 63)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.GLB, status=DerivativeStatus.OK
    )
    raw_path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.GLB)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"raw-glb-bytes")

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/glb")

    assert response.status_code == 200
    assert response.content == b"raw-glb-bytes"
    assert response.headers["content-type"] == "model/gltf-binary"
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"


async def test_get_glb_ok_derivative_missing_file_on_disk_is_404(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M2-Minor 4 fold: same clean-404 fix as the thumb route, for the raw
    GLB derivative.
    """
    blob = await _seed_blob(db_session, blob_hash="d" + "b" * 63)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.GLB, status=DerivativeStatus.OK
    )
    # Deliberately never writes the raw GLB (or web GLB) file to disk.

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/glb")

    assert response.status_code == 404


async def test_get_glb_prefers_web_glb_over_raw_when_both_present(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    settings = get_settings()
    blob = await _seed_blob(db_session, blob_hash="7" + "a" * 63)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.GLB, status=DerivativeStatus.OK
    )
    raw_path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.GLB)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"raw-glb-bytes")
    web_path = derivatives.glb_web_path(settings, blob.hash)
    web_path.write_bytes(b"meshopt-web-bytes")

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/glb")

    assert response.status_code == 200
    assert response.content == b"meshopt-web-bytes"


async def test_get_glb_preview_true_serves_preview_when_ok(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    settings = get_settings()
    blob = await _seed_blob(db_session, blob_hash="8" + "a" * 63)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.GLB, status=DerivativeStatus.OK
    )
    raw_path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.GLB)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"raw-glb-bytes")
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.GLB_PREVIEW, status=DerivativeStatus.OK
    )
    preview_path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.GLB_PREVIEW)
    preview_path.write_bytes(b"preview-glb-bytes")

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/glb?preview=true")

    assert response.status_code == 200
    assert response.content == b"preview-glb-bytes"
    assert response.headers["etag"] == f'"{blob.hash}:glb_preview"'


async def test_get_glb_preview_true_falls_through_when_preview_not_ready(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    settings = get_settings()
    blob = await _seed_blob(db_session, blob_hash="9" + "a" * 63)
    await _seed_derivative(
        db_session, blob_hash=blob.hash, kind=DerivativeKind.GLB, status=DerivativeStatus.OK
    )
    raw_path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.GLB)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"raw-glb-bytes")
    # No GLB_PREVIEW row at all -- preview=true must fall through to the
    # normal (non-preview) chain rather than 404ing.

    response = await authenticated_client.get(f"/api/blobs/{blob.hash}/glb?preview=true")

    assert response.status_code == 200
    assert response.content == b"raw-glb-bytes"


# ---------------------------------------------------------------------------
# GET /api/revisions/{id}/assembly-thumb
# ---------------------------------------------------------------------------


async def test_get_assembly_thumb_unknown_revision_is_404(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.get("/api/revisions/999999/assembly-thumb")

    assert response.status_code == 404


async def test_get_assembly_thumb_missing_row_is_404_pending(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, revision = await _seed_model_and_revision(db_session)

    response = await authenticated_client.get(f"/api/revisions/{revision.id}/assembly-thumb")

    assert response.status_code == 404
    assert response.json()["detail"] == "pending"


async def test_get_assembly_thumb_failed_reports_failed(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, revision = await _seed_model_and_revision(db_session)
    db_session.add(AssemblyThumb(revision_id=revision.id, status=DerivativeStatus.FAILED))
    await db_session.commit()

    response = await authenticated_client.get(f"/api/revisions/{revision.id}/assembly-thumb")

    assert response.status_code == 404
    assert response.json()["detail"] == "failed"


async def test_get_assembly_thumb_ok_serves_bytes_with_no_cache_and_etag(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    settings = get_settings()
    _, revision = await _seed_model_and_revision(db_session)
    path = derivatives.assembly_thumb_path(settings, revision.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_BYTES)
    db_session.add(
        AssemblyThumb(revision_id=revision.id, status=DerivativeStatus.OK, local_path=str(path))
    )
    await db_session.commit()

    response = await authenticated_client.get(f"/api/revisions/{revision.id}/assembly-thumb")

    assert response.status_code == 200
    assert response.content == _PNG_BYTES
    assert response.headers["cache-control"] == "no-cache"
    assert "etag" in response.headers


async def test_get_assembly_thumb_if_none_match_returns_304(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    settings = get_settings()
    _, revision = await _seed_model_and_revision(db_session)
    path = derivatives.assembly_thumb_path(settings, revision.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_BYTES)
    db_session.add(
        AssemblyThumb(revision_id=revision.id, status=DerivativeStatus.OK, local_path=str(path))
    )
    await db_session.commit()

    first = await authenticated_client.get(f"/api/revisions/{revision.id}/assembly-thumb")
    etag = first.headers["etag"]

    second = await authenticated_client.get(
        f"/api/revisions/{revision.id}/assembly-thumb", headers={"If-None-Match": etag}
    )

    assert second.status_code == 304
