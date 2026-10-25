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


def _ftps_stub(payload: bytes | None):
    """Raw ftplib-style session: ``retrbinary`` streams ``payload`` to the
    callback, or raises FTP 550 when ``payload`` is None (missing file)."""
    import ftplib

    def retrbinary(cmd, cb, blocksize=8192):
        if payload is None:
            raise ftplib.error_perm("550 secret: No such file")
        for i in range(0, len(payload), 2):
            cb(payload[i : i + 2])
        return "226"

    ftps = SimpleNamespace(
        connect=lambda host, port: None,
        login=lambda user, pw: None,
        prot_p=lambda: None,
        close=lambda: None,
        retrbinary=retrbinary,
    )
    return SimpleNamespace(
        ftps=ftps, server_ip="1.2.3.4", port=990, user="bblp", access_code="secret"
    )


def test_bambu_download_writes_dest_and_raises_on_none(monkeypatch, tmp_path):
    monkeypatch.setattr(bambu, "_build_ftp", lambda conn: _ftps_stub(b"3mf"))
    _adapter().download_file("/cache/x.3mf", tmp_path / "x")
    assert (tmp_path / "x").read_bytes() == b"3mf"
    monkeypatch.setattr(bambu, "_build_ftp", lambda conn: _ftps_stub(None))
    with pytest.raises(FileNotFoundError) as ei:
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


def test_build_ftp_sets_timeout(monkeypatch):
    import sys
    import types

    created = []

    class Client:
        def __init__(self, host, code):
            self.ftps = types.SimpleNamespace(timeout=None)
            created.append(self)

    mod = types.ModuleType("bambulabs_api.ftp_client")
    mod.PrinterFTPClient = Client
    monkeypatch.setitem(sys.modules, "bambulabs_api.ftp_client", mod)
    bambu._build_ftp(CONN)
    assert created[0].ftps.timeout == bambu.FTP_TIMEOUT_S == 30
