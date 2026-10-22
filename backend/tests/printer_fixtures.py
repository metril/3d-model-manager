"""Printer test fixtures (M4 carve-out). Provides the fake-adapter patch
fixture used by the API / printerd / send-flow tests. Registered from
conftest.py's pytest_plugins."""

from __future__ import annotations

import importlib

import pytest

from app.models.enums import PrinterKind
from app.printers.base import PrinterConnection
from app.printers.fake import FakePrinterAdapter

_BUILD_ADAPTER_CALL_SITES = (
    "app.printers.registry",
    "app.api.printers",
    "app.tasks.printing",
    "app.printerd",
    "app.services.archive",
    "app.services.archive_media",
)


@pytest.fixture
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> FakePrinterAdapter:
    """Return the single FakePrinterAdapter that build_adapter now hands back
    in every call-site module, so a test can drive it (emit snapshots, assert
    uploaded/paused/...)."""
    adapter = FakePrinterAdapter(PrinterConnection(host="fake", serial="FAKE", access_code="x"))

    def _build(kind, conn):
        assert kind == PrinterKind.BAMBU_LAN
        return adapter

    for modname in _BUILD_ADAPTER_CALL_SITES:
        try:
            mod = importlib.import_module(modname)
        except ImportError:
            continue  # module lands in a later task
        monkeypatch.setattr(mod, "build_adapter", _build, raising=False)
    return adapter
