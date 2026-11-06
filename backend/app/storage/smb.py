"""SMB StorageBackend (SPEC "Storage layer" SMB row).

Built on smbprotocol's high-level ``smbclient`` API. Sessions are registered
LAZILY -- never at import time or in ``__init__`` -- because Celery prefork
forks worker processes, and a session (and its underlying open socket)
established in the parent is unusable/unsafe to share from a forked child.
Every method call re-asserts the session via ``smbclient.register_session``
before touching the wire.

Note on that re-assertion: ``register_session`` is itself cheap and
idempotent when a live connection is already cached (a dict lookup plus a
``transport.connected`` check, no network round trip), so there is no
benefit -- and there is an actual correctness hazard -- in adding our own
"registered once" memoization on top of it. A once-only guard would go stale
the moment something external calls ``smbclient.reset_connection_cache()``
(worker shutdown; also what the test fixtures around this module do between
tests), leaving our memo saying "already registered" while the real
connection cache is empty -- the next bare ``smbclient.stat(...)`` call would
then try to open a brand new, credential-less session and fail
authentication. Always re-asserting the session sidesteps that entirely.

UUID-temp + ``smbclient.replace`` gives rename-based atomicity (SMB2
rename-with-replace, same guarantee ``os.replace`` gives locally). SMB2
COPYCHUNK (server-side) via ``smbclient.copyfile`` makes same-share copies
near-instant on capable servers. ``scandir`` reads size/mtime from
``SMBDirEntry.smb_info`` (the ``FILE_ID_FULL_DIRECTORY_INFORMATION`` already
returned by the directory enumeration) -- no per-entry ``stat`` call.
"""

from __future__ import annotations

import contextlib
import errno
import uuid
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

import smbclient
from blake3 import blake3
from smbprotocol.exceptions import SMBOSError

from app.storage.base import EntryInfo, StatResult, WriteResult
from app.storage.config import SmbConfig
from app.storage.errors import StorageError, StorageKeyNotFound

_READ_CHUNK_SIZE = 1024 * 1024  # 1 MiB

# Same reservation contract as the local backend: staging files created by
# write()/copy() live under this prefix so walk() can filter them out (an
# in-flight write, or one abandoned by a crash before cleanup ran).
_TMP_PREFIX = ".tdmm-tmp-"


def _safe_key(key: str) -> str:
    """Validate ``key`` with the same rules as the local backend.

    Rejects empty/``.``/``..``, absolute (leading ``/``), backslashes
    (Windows-style separators are never accepted as *input* -- they're an
    SMB wire-format detail this module owns internally), and any path
    component in the reserved ``.tdmm-tmp-`` staging namespace.
    """
    if key in ("", ".") or key.startswith("/") or "\\" in key:
        raise StorageError(f"unsafe storage key: {key!r}")
    parts = key.split("/")
    if any(p in ("", ".", "..") or p.startswith(_TMP_PREFIX) for p in parts):
        raise StorageError(f"unsafe storage key: {key!r}")
    return key


def _safe_prefix(prefix: str) -> str:
    """Like :func:`_safe_key` but allows ``""`` to mean the root itself."""
    if prefix in ("", "."):
        return ""
    return _safe_key(prefix)


def _UNC(host: str, share: str, key: str) -> str:
    """Build a UNC path (``\\\\host\\share\\a\\b.bin``) from a POSIX key."""
    tail = key.replace("/", "\\")
    return rf"\\{host}\{share}\{tail}" if tail else rf"\\{host}\{share}"


def _filetime_to_utc(value: datetime) -> datetime:
    """Coerce an ``smb_info``/``stat`` timestamp to a tz-aware UTC datetime.

    smbprotocol's ``DateTimeField`` already parses SMB FILETIMEs into
    tz-aware ``datetime.timezone.utc`` values, so this is normally a no-op;
    ``astimezone`` also correctly handles the (currently unseen in practice)
    case of a naive value by assuming local time before converting.
    """
    return value.astimezone(UTC)


def _is_not_found(e: SMBOSError) -> bool:
    """Only a genuine missing object (ENOENT, i.e. STATUS_OBJECT_NAME_NOT_FOUND
    / PATH_NOT_FOUND) means "no such key"; access-denied, sharing violations,
    network errors etc. must surface, not read as absent."""
    return e.errno == errno.ENOENT


class SmbStorageBackend:
    """Stores files on an SMB share, rooted at ``config.root`` within it."""

    def __init__(self, config: SmbConfig) -> None:
        self._c = config
        self._root = config.root.strip("/")  # "" = share root
        # smbclient resolves a cached connection by *(host, port)* -- every
        # top-level smbclient call defaults port=445 unless told otherwise,
        # so a non-standard port (the norm in tests, where a testcontainer
        # publishes SMB on a random host port) must be threaded through on
        # every single call, not just the initial session registration.
        self._kwargs = {
            "username": self._c.username,
            "password": self._c.password.get_secret_value(),
            "port": self._c.port,
            "encrypt": self._c.encrypt,
        }

    # -- session -------------------------------------------------------

    def _ensure_session(self) -> None:
        """Register (or re-assert) the session for this backend's server.

        See the module docstring for why this is unconditional rather than
        memoized: it's already cheap when a live connection is cached, and
        memoizing it introduces a staleness hazard.
        """
        smbclient.register_session(self._c.host, **self._kwargs)

    # -- path helpers ----------------------------------------------------

    def _full(self, key: str) -> str:
        """``key`` joined onto ``self._root``, POSIX-style, no leading slash."""
        return f"{self._root}/{key}".strip("/") if self._root else key

    def _path(self, key: str) -> str:
        return _UNC(self._c.host, self._c.share, self._full(key))

    def _dir_of(self, key: str) -> str:
        """UNC path of the directory containing ``key``."""
        full = self._full(key)
        parent = "/".join(full.split("/")[:-1])
        return _UNC(self._c.host, self._c.share, parent)

    def _tmp_path(self, key: str) -> str:
        """A fresh UUID-named staging path in the same directory as ``key``."""
        return self._dir_of(key) + "\\" + f"{_TMP_PREFIX}{uuid.uuid4().hex}"

    @staticmethod
    def _parent_prefix(key: str) -> str:
        return "/".join(key.split("/")[:-1])

    # -- write / read -----------------------------------------------------

    def write(self, key: str, chunks: Iterable[bytes]) -> WriteResult:
        _safe_key(key)
        self._ensure_session()
        self.mkdirs(self._parent_prefix(key))
        tmp = self._tmp_path(key)
        hasher = blake3()
        size = 0
        try:
            with smbclient.open_file(tmp, mode="wb", **self._kwargs) as fh:
                for chunk in chunks:
                    fh.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
            smbclient.replace(tmp, self._path(key), **self._kwargs)
        except BaseException:
            with contextlib.suppress(Exception):
                smbclient.remove(tmp, **self._kwargs)
            raise
        return WriteResult(hash=hasher.hexdigest(), size=size)

    def read(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        _safe_key(key)
        self._ensure_session()
        try:
            fh = smbclient.open_file(self._path(key), mode="rb", **self._kwargs)
        except SMBOSError as e:
            if not _is_not_found(e):
                raise
            raise StorageKeyNotFound(key) from e
        return self._read_range(fh, start, end)

    @staticmethod
    def _read_range(fh: object, start: int, end: int | None) -> Iterator[bytes]:
        with fh:
            if start:
                fh.seek(start)
            remaining = None if end is None else max(0, end - start)
            while remaining is None or remaining > 0:
                read_size = (
                    _READ_CHUNK_SIZE if remaining is None else min(_READ_CHUNK_SIZE, remaining)
                )
                chunk = fh.read(read_size)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk

    # -- stat / exists / delete -------------------------------------------

    def stat(self, key: str) -> StatResult:
        _safe_key(key)
        self._ensure_session()
        try:
            info = smbclient.stat(self._path(key), **self._kwargs)
        except SMBOSError as e:
            if not _is_not_found(e):
                raise
            raise StorageKeyNotFound(key) from e
        return StatResult(
            size=info.st_size, mtime=_filetime_to_utc(datetime.fromtimestamp(info.st_mtime, tz=UTC))
        )

    def exists(self, key: str) -> bool:
        _safe_key(key)
        self._ensure_session()
        try:
            smbclient.stat(self._path(key), **self._kwargs)
        except SMBOSError as e:
            if not _is_not_found(e):
                raise
            return False
        return True

    def delete(self, key: str) -> None:
        _safe_key(key)
        self._ensure_session()
        try:
            smbclient.remove(self._path(key), **self._kwargs)
        except SMBOSError as e:
            if not _is_not_found(e):
                raise
            raise StorageKeyNotFound(key) from e

    # -- move / copy -------------------------------------------------------

    def move(self, src: str, dst: str) -> None:
        _safe_key(src)
        _safe_key(dst)
        self._ensure_session()
        if not self.exists(src):
            raise StorageKeyNotFound(src)
        self.mkdirs(self._parent_prefix(dst))
        try:
            smbclient.replace(self._path(src), self._path(dst), **self._kwargs)
        except SMBOSError as e:
            if not _is_not_found(e):
                raise
            raise StorageKeyNotFound(src) from e

    def copy(self, src: str, dst: str) -> None:
        _safe_key(src)
        _safe_key(dst)
        self._ensure_session()
        if not self.exists(src):
            raise StorageKeyNotFound(src)
        self.mkdirs(self._parent_prefix(dst))
        tmp = self._tmp_path(dst)
        try:
            smbclient.copyfile(self._path(src), tmp, **self._kwargs)  # SMB2 COPYCHUNK, same share
            smbclient.replace(tmp, self._path(dst), **self._kwargs)
        except BaseException:
            with contextlib.suppress(Exception):
                smbclient.remove(tmp, **self._kwargs)
            raise

    # -- walk / mkdirs -------------------------------------------------------

    def walk(self, prefix: str = "") -> Iterator[EntryInfo]:
        self._ensure_session()
        base = _safe_prefix(prefix)
        yield from self._walk_dir(self._full(base), base)

    def _walk_dir(self, full_dir: str, rel_dir: str) -> Iterator[EntryInfo]:
        unc = _UNC(self._c.host, self._c.share, full_dir)
        try:
            entries = sorted(smbclient.scandir(unc, **self._kwargs), key=lambda e: e.name)
        except SMBOSError as e:
            if _is_not_found(e):
                return  # missing prefix (or dir removed mid-walk): nothing to list
            raise  # permission/network errors must not look like an empty tree
        for entry in entries:
            if entry.name.startswith(_TMP_PREFIX):
                continue
            child_rel = f"{rel_dir}/{entry.name}".strip("/")
            child_full = f"{full_dir}/{entry.name}".strip("/")
            if entry.is_dir():
                yield from self._walk_dir(child_full, child_rel)
            else:
                info = entry.smb_info  # sizes/mtimes without a per-entry stat
                yield EntryInfo(
                    key=child_rel,
                    size=info.end_of_file,
                    mtime=_filetime_to_utc(info.last_write_time),
                )

    def mkdirs(self, key_prefix: str) -> None:
        self._ensure_session()
        rel = _safe_prefix(key_prefix)
        full = self._full(rel)
        if not full:
            return
        smbclient.makedirs(_UNC(self._c.host, self._c.share, full), exist_ok=True, **self._kwargs)
