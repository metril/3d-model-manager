"""Fernet encryption for at-rest secrets -- printer access codes, SMB/S3
storage credentials, and the Thingiverse import token -- all under one key
at ``{data_dir}/secrets/printer.key`` (SPEC ``printers.access_code_enc
/*fernet*/``; M4; extended to storage/import secrets by M6 A1). The key
lives there (0600) or comes from ``PRINTER_KEY``; it is loaded ONLY
where a secret is actually encrypted/decrypted (the send task, printerd, the
test-connection probe, the storage-config/import-token seams) -- never
eagerly at import, and decrypted plaintext is never logged, returned, or
published (Global Constraints).
"""

from __future__ import annotations

import os
import threading
import time
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import Settings


def printer_key_path(settings: Settings) -> Path:
    return settings.data_dir / "secrets" / "printer.key"


_key_cache: dict[tuple[str, str], bytes] = {}
_key_lock = threading.Lock()


def _read_existing_key(path: Path) -> bytes:
    # Defensively tighten perms on load in case the file was ever restored
    # or created with looser permissions than we require. Keys are published
    # by atomic rename, but retry briefly on an empty file written by an
    # older version that created it non-atomically.
    path.chmod(0o600)
    for _ in range(50):
        key = path.read_bytes().strip()
        if key:
            return key
        time.sleep(0.05)
    raise RuntimeError(f"printer key file {path} is empty")


def _create_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = Fernet.generate_key()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, key)
    finally:
        os.close(fd)
    try:
        # link() fails if the key already exists, so the first writer wins
        # and the file is never visible empty or partially written.
        os.link(tmp, path)
    except FileExistsError:
        return _read_existing_key(path)
    finally:
        tmp.unlink(missing_ok=True)
    return key


def load_or_create_printer_key(settings: Settings) -> bytes:
    if settings.printer_key:
        return settings.printer_key.get_secret_value().encode()
    path = printer_key_path(settings)
    ck = (str(path), "")
    with _key_lock:
        cached = _key_cache.get(ck)
        if cached is not None and path.exists():
            return cached
        key = _read_existing_key(path) if path.exists() else _create_key(path)
        _key_cache[ck] = key
        return key


@lru_cache(maxsize=16)
def _hkdf(key: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(key)


def derive_subkey(settings: Settings, info: bytes) -> bytes:
    """HKDF-SHA256 subkey so HMAC use never shares raw key bytes with Fernet."""
    return _hkdf(load_or_create_printer_key(settings), info)


def get_fernet(settings: Settings) -> Fernet:
    return Fernet(load_or_create_printer_key(settings))


def encrypt_secret(settings: Settings, plaintext: str) -> str:
    return get_fernet(settings).encrypt(plaintext.encode()).decode()


def decrypt_secret(settings: Settings, token: str) -> str:
    return get_fernet(settings).decrypt(token.encode()).decode()
