"""S3 StorageBackend (SPEC "Storage layer" S3 row).

boto3 only (RESEARCH §1 rejects s3fs/aiobotocore). No temp+rename: S3 has no
atomic rename, and an object is never partially visible -- a PUT or
CompleteMultipartUpload either fully materializes the key or nothing does, so
we write straight to the final key and let the DB commit be the transaction
boundary (unlike ``local``/``smb``, there is no staging file for ``walk`` to
filter out). An aborted multipart upload leaves its uploaded-so-far parts
invisible (never assembled into an object) but not necessarily reclaimed --
the *incomplete-MPU lifecycle rule*: an operator-configured bucket lifecycle
rule expires incomplete multipart uploads after N days so abandoned parts
don't accumulate storage cost forever. We document this (Settings UI copy);
we do not, and cannot, create the rule from here. ETags are NOT content
hashes for multipart objects (they're not even a plain MD5 for single-part
objects once server-side encryption is involved) -- the scanner compares
SIZE only on S3, never ETag; content identity is always the blake3 hash
computed here (and re-verified) after a real read.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC

import boto3
from blake3 import blake3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.storage.base import EntryInfo, StatResult, WriteResult
from app.storage.config import S3Config
from app.storage.errors import StorageError, StorageKeyNotFound

_READ_CHUNK = 1024 * 1024  # 1 MiB

# Buffer writes up to this size as a single PUT; larger streams go through
# multipart upload instead. Also the size of each multipart part (>= S3's
# 5 MiB part-size minimum for all but the last part).
_MPU_THRESHOLD = 8 * 1024 * 1024
_PART_SIZE = _MPU_THRESHOLD


def _safe_key(key: str) -> str:
    """Validate ``key`` with the same rules as the local/SMB backends.

    Rejects empty/``.``/``..``, absolute (leading ``/``), and backslashes.
    Unlike local/SMB, S3 needs no reserved staging-file namespace -- writes
    and copies go straight to the final key, so there's no ``.tdmm-tmp-*``
    prefix to guard against here.
    """
    if key in ("", ".") or key.startswith("/") or "\\" in key:
        raise StorageError(f"unsafe storage key: {key!r}")
    if any(part in ("", ".", "..") for part in key.split("/")):
        raise StorageError(f"unsafe storage key: {key!r}")
    return key


def _safe_prefix(prefix: str) -> str:
    """Like :func:`_safe_key` but allows ``""``/``"."`` to mean the root."""
    if prefix in ("", "."):
        return ""
    return _safe_key(prefix)


def _is_not_found(error: ClientError) -> bool:
    code = error.response.get("Error", {}).get("Code", "")
    return code in ("404", "NoSuchKey", "NotFound")


def _reorder_per_directory(entries: list[EntryInfo]) -> Iterator[EntryInfo]:
    """Rebuild the ``walk()`` bare-name, depth-first order (``app/storage/
    base.py``) from a flat, full-key-lexicographic ``list_objects_v2``
    listing.

    ``list_objects_v2`` sorts keys byte-for-byte over the *full* key string,
    which is not the walk() contract: within each directory, entries (files
    and subdirectories alike) must sort by their own *bare* name, with a
    subdirectory's entire subtree emitted, depth-first, at the point its
    bare name falls in that order. We rebuild the actual directory tree --
    a nested ``dict`` keyed by path segment, where a leaf value is the
    ``EntryInfo`` for a file and an internal ``dict`` is a subdirectory --
    then walk it, sorting each level's children by name as we go.
    """
    root: dict[str, dict | EntryInfo] = {}
    for entry in entries:
        *dirs, name = entry.key.split("/")
        node = root
        for part in dirs:
            node = node.setdefault(part, {})  # type: ignore[assignment]
        node[name] = entry

    def _walk(node: dict[str, dict | EntryInfo]) -> Iterator[EntryInfo]:
        for name in sorted(node):
            child = node[name]
            if isinstance(child, EntryInfo):
                yield child
            else:
                yield from _walk(child)

    yield from _walk(root)


class S3StorageBackend:
    """Stores files as S3 objects, rooted at ``config.prefix`` in the bucket."""

    def __init__(self, config: S3Config) -> None:
        self._c = config
        self._prefix = config.prefix.strip("/")  # "" = bucket root
        self._s3 = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            region_name=config.region or "us-east-1",
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key.get_secret_value(),
            config=BotoConfig(
                s3={"addressing_style": config.addressing},
                # botocore's newer flexible-checksum feature validates a
                # GetObject response against a checksum computed for the
                # *whole* object even when we sent a Range header -- against
                # MinIO (which echoes that whole-object checksum on ranged
                # responses) that always mismatches the partial body we
                # actually received. Ranged reads never need our own
                # integrity check anyway (`write` already returned a blake3
                # hash for the full object); only validate when boto3
                # requires it for an operation (multipart, etc).
                response_checksum_validation="when_required",
            ),
        )

    # -- key <-> object-key mapping --------------------------------------

    def _obj(self, key: str) -> str:
        """``key`` joined onto ``self._prefix`` -> the actual S3 object key."""
        return f"{self._prefix}/{key}".strip("/") if self._prefix else key

    def _rel(self, obj_key: str) -> str:
        """The reverse of :meth:`_obj`: an S3 object key -> a storage key."""
        return obj_key[len(self._prefix) + 1 :] if self._prefix else obj_key

    # -- write: PUT (small) or MPU (large), both all-or-nothing ----------

    def write(self, key: str, chunks: Iterable[bytes]) -> WriteResult:
        _safe_key(key)
        hasher = blake3()
        buf = bytearray()
        size = 0
        it = iter(chunks)
        # Buffer until either the stream ends (small object -> single PUT)
        # or we cross the MPU threshold (large object -> hand off to the
        # multipart path, which continues consuming `it`).
        for chunk in it:
            hasher.update(chunk)
            size += len(chunk)
            buf.extend(chunk)
            if len(buf) > _MPU_THRESHOLD:
                return self._write_mpu(key, bytes(buf), it, hasher, size)
        self._s3.put_object(Bucket=self._c.bucket, Key=self._obj(key), Body=bytes(buf))
        return WriteResult(hash=hasher.hexdigest(), size=size)

    def _write_mpu(
        self,
        key: str,
        first: bytes,
        rest: Iterator[bytes],
        hasher: blake3,
        size: int,
    ) -> WriteResult:
        obj = self._obj(key)
        upload = self._s3.create_multipart_upload(Bucket=self._c.bucket, Key=obj)
        upload_id = upload["UploadId"]
        parts: list[dict] = []
        pending = bytearray(first)

        def _upload_part(data: bytes) -> None:
            part_number = len(parts) + 1
            resp = self._s3.upload_part(
                Bucket=self._c.bucket,
                Key=obj,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=data,
            )
            parts.append({"ETag": resp["ETag"], "PartNumber": part_number})

        def _flush_full_parts() -> None:
            while len(pending) >= _PART_SIZE:
                _upload_part(bytes(pending[:_PART_SIZE]))
                del pending[:_PART_SIZE]

        try:
            _flush_full_parts()
            for chunk in rest:
                hasher.update(chunk)
                size += len(chunk)
                pending.extend(chunk)
                _flush_full_parts()
            if pending:  # final part may be under the 5 MiB minimum -- allowed
                _upload_part(bytes(pending))
            self._s3.complete_multipart_upload(
                Bucket=self._c.bucket,
                Key=obj,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except BaseException:
            # Abort so no parts linger toward the incomplete-MPU lifecycle
            # rule and no half-assembled object is ever visible at `obj`.
            self._s3.abort_multipart_upload(Bucket=self._c.bucket, Key=obj, UploadId=upload_id)
            raise
        return WriteResult(hash=hasher.hexdigest(), size=size)

    def read(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        _safe_key(key)
        range_kwargs = {}
        if start or end is not None:
            range_kwargs["Range"] = f"bytes={start}-{'' if end is None else end - 1}"
        try:
            resp = self._s3.get_object(Bucket=self._c.bucket, Key=self._obj(key), **range_kwargs)
        except ClientError as e:
            if _is_not_found(e):
                raise StorageKeyNotFound(key) from e
            raise
        return self._read_body(resp["Body"])

    @staticmethod
    def _read_body(body: object) -> Iterator[bytes]:
        with body:  # botocore StreamingBody is a context manager
            while True:
                chunk = body.read(_READ_CHUNK)
                if not chunk:
                    break
                yield chunk

    # -- stat / exists / delete -------------------------------------------

    def stat(self, key: str) -> StatResult:
        _safe_key(key)
        try:
            head = self._s3.head_object(Bucket=self._c.bucket, Key=self._obj(key))
        except ClientError as e:
            if _is_not_found(e):
                raise StorageKeyNotFound(key) from e
            raise
        return StatResult(size=head["ContentLength"], mtime=head["LastModified"].astimezone(UTC))

    def exists(self, key: str) -> bool:
        _safe_key(key)
        try:
            self._s3.head_object(Bucket=self._c.bucket, Key=self._obj(key))
        except ClientError as e:
            if _is_not_found(e):
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        _safe_key(key)
        # DeleteObject is a no-op (204) on a missing key -- unlike
        # local/SMB, S3 gives us no native "not found" signal here, so a
        # probe HEAD is the only way to satisfy "deleting twice raises".
        if not self.exists(key):
            raise StorageKeyNotFound(key)
        self._s3.delete_object(Bucket=self._c.bucket, Key=self._obj(key))

    # -- copy / move -------------------------------------------------------

    def copy(self, src: str, dst: str) -> None:
        _safe_key(src)
        _safe_key(dst)
        try:
            # Managed copy: HEADs the source, then CopyObject for small
            # objects or a multipart UploadPartCopy above the threshold
            # (plain copy_object fails for sources over 5 GiB).
            self._s3.copy(
                CopySource={"Bucket": self._c.bucket, "Key": self._obj(src)},
                Bucket=self._c.bucket,
                Key=self._obj(dst),
            )
        except ClientError as e:
            if _is_not_found(e):
                raise StorageKeyNotFound(src) from e
            raise

    def move(self, src: str, dst: str) -> None:
        self.copy(src, dst)
        self.delete(src)

    # -- walk / mkdirs -------------------------------------------------------

    def walk(self, prefix: str = "") -> Iterator[EntryInfo]:
        base = _safe_prefix(prefix)
        list_prefix = self._obj(base)
        if list_prefix and not list_prefix.endswith("/"):
            list_prefix += "/"
        collected: list[EntryInfo] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._c.bucket, Prefix=list_prefix):
            for obj in page.get("Contents", []):
                collected.append(
                    EntryInfo(
                        key=self._rel(obj["Key"]),
                        size=obj["Size"],
                        mtime=obj["LastModified"].astimezone(UTC),
                    )
                )
        yield from _reorder_per_directory(collected)

    def mkdirs(self, key_prefix: str) -> None:
        return  # S3 has no directories -- objects materialize their own prefix
