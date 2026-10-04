"""Stream a remote file straight to the upload spool, blake3-hashing while
it streams -- the importer analog of app.api.uploads' tee-to-spool loop
(surface map §3a), but reading an httpx streaming response instead of an
ASGI request body. Runs in the worker's SYNC world. ``_download_client`` is
the ONE construction seam tests monkeypatch with an httpx.MockTransport
(Global Constraints M5 EXCEPTION); the default gate makes no real network
call.

``stage_zip_member`` is the same spool+blake3 machinery's sibling for
``app.importers.archives``' zip extraction: tees an already-open zip
member's bytes to a NEW spool file instead of an HTTP response body.
``stage_local_file`` is the same sibling again for
``app.tasks.slicer_watch`` (Round 8 Task 5): tees an already-on-disk local
file instead."""

from __future__ import annotations

import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from blake3 import blake3

from app.config import Settings
from app.models.enums import BlobFormat, BlobKind
from app.services import layout, spool

_CHUNK_SIZE = 1024 * 1024  # 1 MiB, matching store_to_backend's read chunking
_TIMEOUT = httpx.Timeout(30.0, read=300.0)
_USER_AGENT = "3d-model-manager/1.0 (+https://github.com/metril/3d-model-manager)"

# T2 (site cover + gallery images): a plain image URL often carries no
# recognizable extension (a signed CDN path, a bare numeric id, ...) -- the
# response's own Content-Type is the fallback signal ``app.tasks.importing``
# uses to pick the final ``rel_path`` extension. Kept here (not in the task
# module) since it's a property of the HTTP response this module already
# owns fetching.
_IMAGE_EXT_FROM_CONTENT_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


def image_ext_from_content_type(content_type: str | None) -> str | None:
    """Normalize a response ``Content-Type`` header to a bare image
    extension (``png``/``jpg``/``webp``), or ``None`` for anything else
    (including a missing header) -- any ``; charset=...`` suffix is ignored."""
    if not content_type:
        return None
    media_type = content_type.split(";", 1)[0].strip().lower()
    return _IMAGE_EXT_FROM_CONTENT_TYPE.get(media_type)


@dataclass(frozen=True)
class StagedFile:
    token: uuid.UUID
    spool_path: Path
    blob_hash: str
    size: int
    rel_path: str
    kind: BlobKind
    format_: BlobFormat


def _download_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True, timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}
    )


def stream_remote_to_spool(
    settings: Settings,
    *,
    url: str,
    rel_path: str,
    headers: dict[str, str] | None = None,
    rel_path_from_response: Callable[[httpx.Response], str] | None = None,
) -> StagedFile:
    """``rel_path_from_response`` (T2 gallery-image download): when the
    caller doesn't yet know the real extension (a plain image URL with no
    recognizable suffix), it passes a placeholder ``rel_path`` plus this
    callback -- called once the response headers are in (status already
    raised for) to compute the REAL ``rel_path`` from e.g. Content-Type,
    overriding the placeholder before anything is written. Raising from it
    (an unrecognized Content-Type) aborts the download exactly like any
    other mid-stream failure -- the ``except BaseException`` cleanup below
    still fires, so no spool file is left behind."""
    spool.ensure_spool_dir(settings)
    token = uuid.uuid4()
    path = spool.spool_path(settings, token)
    hasher = blake3()
    size = 0
    try:
        with _download_client() as client, client.stream("GET", url, headers=headers or {}) as resp:
            resp.raise_for_status()
            if rel_path_from_response is not None:
                rel_path = rel_path_from_response(resp)
            with path.open("wb") as fh:
                for chunk in resp.iter_bytes(_CHUNK_SIZE):
                    if not chunk:
                        continue
                    hasher.update(chunk)
                    size += len(chunk)
                    if size > settings.max_download_bytes:
                        raise ValueError(
                            f"remote file {rel_path!r} exceeds {settings.max_download_bytes} bytes"
                        )
                    fh.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)  # never orphan a spool file on a failed stream
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise ValueError(f"remote file {rel_path!r} was empty")
    kind, format_ = layout.infer_blob_kind_format(rel_path)
    return StagedFile(
        token=token,
        spool_path=path,
        blob_hash=hasher.hexdigest(),
        size=size,
        rel_path=rel_path,
        kind=kind,
        format_=format_,
    )


def stage_local_file(settings: Settings, source: Path, *, rel_path: str) -> StagedFile:
    """Sibling of ``stream_remote_to_spool``/``stage_zip_member`` for
    ``app.tasks.slicer_watch`` (Round 8 Task 5: watched-folder auto-import):
    tees an already-on-disk local file's bytes to a NEW spool file, blake3-
    hashing while it streams -- constant memory, chunked read, exactly like
    ``stage_zip_member``'s member-copy loop, just reading a plain file handle
    instead of an open zip member. The result is a first-class ``StagedFile``
    indistinguishable from a directly-downloaded or zip-extracted one.

    A zero-byte ``source`` raises, same posture as ``stream_remote_to_spool``
    (a strong signal of an interrupted/corrupt write, not a legitimate empty
    export) -- unlike ``stage_zip_member``, which allows empty archive
    members.
    """
    spool.ensure_spool_dir(settings)
    token = uuid.uuid4()
    path = spool.spool_path(settings, token)
    hasher = blake3()
    size = 0
    try:
        with source.open("rb") as src, path.open("wb") as fh:
            while chunk := src.read(_CHUNK_SIZE):
                hasher.update(chunk)
                size += len(chunk)
                fh.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)  # never orphan a spool file on a failed copy
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise ValueError(f"local file {rel_path!r} was empty")
    kind, format_ = layout.infer_blob_kind_format(rel_path)
    return StagedFile(
        token=token,
        spool_path=path,
        blob_hash=hasher.hexdigest(),
        size=size,
        rel_path=rel_path,
        kind=kind,
        format_=format_,
    )


def stage_zip_member(
    settings: Settings, zf: zipfile.ZipFile, info: zipfile.ZipInfo, rel_path: str
) -> StagedFile:
    """Sibling of ``stream_remote_to_spool`` for ``app.importers.archives``:
    tees one already-open zip member to a NEW spool file, blake3-hashing
    while it streams -- ``zf.open(info)`` + chunked ``.read()`` rather than
    an httpx response, but otherwise the exact same spool+hash machinery
    (own token, own spool path) so the result is a first-class
    ``StagedFile`` indistinguishable from a directly-downloaded one.
    Constant memory: never reads the whole member into memory at once, even
    though ``zipfile`` itself has no streaming ``.iter_bytes`` equivalent.

    Unlike ``stream_remote_to_spool``, a zero-byte member is NOT an error --
    a legitimate archive can contain empty placeholder files, whereas a
    zero-byte HTTP download is a strong signal of a broken remote URL.
    """
    spool.ensure_spool_dir(settings)
    token = uuid.uuid4()
    path = spool.spool_path(settings, token)
    hasher = blake3()
    size = 0
    try:
        with zf.open(info) as member, path.open("wb") as fh:
            while chunk := member.read(_CHUNK_SIZE):
                hasher.update(chunk)
                size += len(chunk)
                fh.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)  # never orphan a spool file on a failed stream
        raise
    kind, format_ = layout.infer_blob_kind_format(rel_path)
    return StagedFile(
        token=token,
        spool_path=path,
        blob_hash=hasher.hexdigest(),
        size=size,
        rel_path=rel_path,
        kind=kind,
        format_=format_,
    )
