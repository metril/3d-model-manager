import os
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.crypto import (
    decrypt_secret,
    encrypt_secret,
    load_or_create_printer_key,
    printer_key_path,
)


def _settings(tmp_path, **kw) -> Settings:
    return Settings(data_dir=tmp_path / "data", **kw)


def test_settings_repr_masks_secrets(tmp_path):
    """M6 A2: ``admin_password``/``printer_key`` are ``SecretStr`` -- neither
    the raw value nor a decoded key ever appears in ``Settings``' own
    ``repr()``/``str()`` (object-dump/traceback leak)."""
    s = _settings(tmp_path, admin_password="hunter2", printer_key="zzz-printer-key-zzz")
    assert "hunter2" not in repr(s) and "hunter2" not in str(s)
    assert "zzz-printer-key-zzz" not in repr(s) and "zzz-printer-key-zzz" not in str(s)


def test_printer_key_from_settings_secret_str_returns_usable_bytes(tmp_path):
    """``load_or_create_printer_key`` must unwrap the ``SecretStr`` (not hand
    back the object, and not e.g. ``str(secretstr).encode()`` which would
    yield the constant ``b"**********"`` instead of the real key)."""
    key = Fernet.generate_key().decode()
    s = _settings(tmp_path, printer_key=key)
    raw = load_or_create_printer_key(s)
    assert raw == key.encode()
    Fernet(raw)  # a genuinely usable Fernet key, not the masked placeholder


def test_round_trip(tmp_path):
    s = _settings(tmp_path)
    token = encrypt_secret(s, "12345678")
    assert token != "12345678"
    assert decrypt_secret(s, token) == "12345678"


def test_key_persisted_0600_and_reused(tmp_path):
    s = _settings(tmp_path)
    k1 = load_or_create_printer_key(s)
    path = printer_key_path(s)
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_or_create_printer_key(s) == k1  # second call reuses, never regenerates
    # a token encrypted under the persisted key still decrypts on a fresh Settings
    token = encrypt_secret(s, "secret")
    assert decrypt_secret(_settings(tmp_path), token) == "secret"


def test_env_key_overrides_file(tmp_path):
    key = Fernet.generate_key().decode()
    s = _settings(tmp_path, printer_key=key)
    assert load_or_create_printer_key(s) == key.encode()
    assert not printer_key_path(s).exists()  # env key path never writes the file
    token = encrypt_secret(s, "x")
    # a Settings WITHOUT the env key (file-based, different key) cannot read it
    with pytest.raises(InvalidToken):
        decrypt_secret(_settings(tmp_path), token)


def test_tampered_token_raises(tmp_path):
    s = _settings(tmp_path)
    token = encrypt_secret(s, "hello")
    with pytest.raises(InvalidToken):
        decrypt_secret(s, token[:-4] + "AAAA")


def test_existing_loose_perms_tightened_on_load(tmp_path):
    s = _settings(tmp_path)
    path = printer_key_path(s)
    path.parent.mkdir(parents=True)
    written_key = Fernet.generate_key()
    path.write_bytes(written_key)
    path.chmod(0o644)

    loaded_key = load_or_create_printer_key(s)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert loaded_key == written_key  # existing key reused, not regenerated


def test_create_is_0600_under_permissive_umask(tmp_path, monkeypatch):
    """Creation must be atomically 0600, independent of umask, with no
    transient window where the file is visible at a looser mode.

    Note: a plain post-call ``stat()`` check cannot tell this apart from the
    old buggy code, because the old code's own trailing ``chmod(0o600)``
    always corrects the *final* on-disk mode by the time the function
    returns -- the defect is a transient window, not a wrong end state (we
    verified: right after the old code's ``write_bytes`` but before its
    ``chmod``, the file sits at 0o666 under umask 0). So this test also
    spies on ``Path.chmod`` to catch that window: any tightening call must
    already find the file at 0600 -- the fixed create path never needs a
    separate tightening call at all (mode is correct from the atomic
    ``os.open`` + ``fchmod``), so no window is ever exposed.
    """
    s = _settings(tmp_path)
    path = printer_key_path(s)

    windows: list[int] = []
    real_chmod = Path.chmod

    def spy_chmod(self, mode, *a, **kw):
        if self == path and self.exists():
            windows.append(stat.S_IMODE(self.stat().st_mode))
        return real_chmod(self, mode, *a, **kw)

    monkeypatch.setattr(Path, "chmod", spy_chmod)

    old_umask = os.umask(0)
    try:
        key = load_or_create_printer_key(s)
    finally:
        os.umask(old_umask)

    assert all(mode == 0o600 for mode in windows)
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes() == key


def test_concurrent_key_creation_never_sees_empty_key(tmp_path):
    import threading

    from app.crypto import _key_cache

    s = _settings(tmp_path)
    _key_cache.clear()
    results: list[bytes] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(8)

    def work():
        barrier.wait()
        try:
            _key_cache.pop((str(printer_key_path(s)), ""), None)
            results.append(load_or_create_printer_key(s))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert len(set(results)) == 1
    Fernet(results[0])
    assert not list(printer_key_path(s).parent.glob("*.tmp"))
