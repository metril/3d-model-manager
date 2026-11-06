import stat

from pydantic import SecretStr

from app.services import launcher_token as lt
from app.services.server_config import ServerConfig


def test_shared_token_created_once_with_0600(tmp_path):
    a = lt.shared_token(tmp_path)
    assert a and len(a) >= 32
    assert lt.shared_token(tmp_path) == a
    assert stat.S_IMODE((tmp_path / ".launcher-token").stat().st_mode) == 0o600


def test_shared_token_none_when_dir_unwritable(tmp_path):
    assert lt.shared_token(tmp_path / "missing") is None


def test_explicit_token_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(
        lt, "get_settings", lambda: type("S", (), {"orcaslicer_exchange_dir": tmp_path})()
    )
    cfg = ServerConfig(orcaslicer_launcher_token=SecretStr("explicit"))
    assert lt.launcher_headers(cfg) == {"X-Launcher-Token": "explicit"}
    assert not (tmp_path / ".launcher-token").exists()


def test_falls_back_to_shared_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        lt, "get_settings", lambda: type("S", (), {"orcaslicer_exchange_dir": tmp_path})()
    )
    h = lt.launcher_headers(ServerConfig())
    assert h == {"X-Launcher-Token": (tmp_path / ".launcher-token").read_text()}
