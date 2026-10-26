"""Regression tests for the live smoke-test findings."""

import base64
import re
from pathlib import Path

import httpx
import respx

from app.config import Settings
from app.services.slicer_client import SliceOptions, SlicerClient

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://slicer.test"


@respx.mock
def test_client_reads_success_log_header(sliced_3mf_bytes: bytes) -> None:
    log = "line1\nline2"
    respx.post(f"{BASE}/slice").mock(
        return_value=httpx.Response(
            200,
            content=sliced_3mf_bytes,
            headers={"x-slice-log": base64.b64encode(log.encode()).decode()},
        )
    )
    client = SlicerClient(BASE)
    client.slice(b"x", "a.stl", {}, {}, [], "textured_plate", SliceOptions())
    assert client.last_log_tail == log


@respx.mock
def test_client_without_log_header_has_none(sliced_3mf_bytes: bytes) -> None:
    respx.post(f"{BASE}/slice").mock(return_value=httpx.Response(200, content=sliced_3mf_bytes))
    client = SlicerClient(BASE)
    client.slice(b"x", "a.stl", {}, {}, [], "textured_plate", SliceOptions())
    assert client.last_log_tail is None


def test_web_thumb_sizes_are_supported() -> None:
    """The API accepts size=256|1024 only; a UI request for anything else 422s."""
    bad = []
    for path in (ROOT / "web" / "src").rglob("*.ts*"):
        for m in re.finditer(r"/thumb\?size=(\d+)", path.read_text()):
            if m.group(1) not in {"256", "1024"}:
                bad.append(f"{path.name}: {m.group(0)}")
    assert not bad, bad


def test_obico_ml_version_defaults_to_the_pin(monkeypatch) -> None:
    monkeypatch.delenv("OBICO_ML_VERSION", raising=False)
    pin = re.search(
        r"obico-server\.git#([0-9a-f]{40}):ml_api", (ROOT / "compose.obico.yaml").read_text()
    )
    assert pin and Settings(_env_file=None).obico_ml_version == pin.group(1)


def test_compose_api_port_and_migration_gate() -> None:
    text = (ROOT / "compose.yaml").read_text()
    assert '"${TDMM_API_HOST_PORT:-8080}:8080"' in text
    for svc in ("worker-io", "worker-cpu", "beat", "printerd", "virtual-printer"):
        block = re.search(rf"\n  {svc}:\n(.*?)(?=\n  [a-z][a-z-]*:\n|\Z)", text, re.S).group(1)
        assert re.search(r"depends_on:.*?api:\s+condition: service_healthy", block, re.S), svc
