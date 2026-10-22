import io
from types import SimpleNamespace

import pytest

from app.printers import bambu
from app.printers.bambu import BambuLanAdapter
from app.printers.base import PrinterAdapter, PrinterConnection
from app.printers.fake import FakePrinterAdapter

CONN = PrinterConnection(host="1.2.3.4", serial="SN", access_code="secret")


def _adapter():
    return BambuLanAdapter(CONN)


def test_bambu_list_dir_parses_names_with_spaces(monkeypatch):
    stub = SimpleNamespace(
        list_directory=lambda p: (
            "226",
            [
                "-rw-r--r-- 1 root root 2298680 Jan 01 00:00 DotC parts - Part 1_plate_1.3mf",
                "drwxr-xr-x 1 root root 0 Jan 01 00:00 .",
                "drwxr-xr-x 1 root root 0 Jan 01 00:00 ..",
                "short line",
            ],
        )
    )
    monkeypatch.setattr(bambu, "_build_ftp", lambda conn: stub)
    assert _adapter().list_dir("/cache") == ["DotC parts - Part 1_plate_1.3mf"]


def test_bambu_list_dir_none_raises(monkeypatch):
    stub = SimpleNamespace(list_directory=lambda p: None)
    monkeypatch.setattr(bambu, "_build_ftp", lambda conn: stub)
    with pytest.raises(OSError) as ei:
        _adapter().list_dir("/cache")
    assert "secret" not in str(ei.value)


def test_bambu_download_writes_dest_and_raises_on_none(monkeypatch, tmp_path):
    stub = SimpleNamespace(download_file=lambda p: io.BytesIO(b"3mf"))
    monkeypatch.setattr(bambu, "_build_ftp", lambda conn: stub)
    _adapter().download_file("/cache/x.3mf", tmp_path / "x")
    assert (tmp_path / "x").read_bytes() == b"3mf"
    stub.download_file = lambda p: None
    with pytest.raises(OSError) as ei:
        _adapter().download_file("/cache/x.3mf", tmp_path / "y")
    assert "secret" not in str(ei.value)


def test_base_methods_default_not_implemented(tmp_path):
    class Min(PrinterAdapter):
        pass

    Min.__abstractmethods__ = frozenset()
    m = Min(CONN)
    with pytest.raises(NotImplementedError):
        m.list_dir("/cache")
    with pytest.raises(NotImplementedError):
        m.download_file("/cache/a", tmp_path / "a")


def test_fake_files_and_errors(tmp_path):
    a = FakePrinterAdapter(CONN)
    a.files["/cache/a.3mf"] = b"z"
    a.download_errors = 1
    assert a.list_dir("/cache") == ["a.3mf"]
    assert a.list_dir("/cache/") == ["a.3mf"]
    with pytest.raises(OSError):
        a.download_file("/cache/a.3mf", tmp_path / "a")
    a.download_file("/cache/a.3mf", tmp_path / "a")
    assert (tmp_path / "a").read_bytes() == b"z"
    assert a.downloads == ["/cache/a.3mf", "/cache/a.3mf"]
    with pytest.raises(FileNotFoundError):
        a.download_file("/cache/missing", tmp_path / "m")
