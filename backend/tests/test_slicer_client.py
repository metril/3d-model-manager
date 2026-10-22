"""Contract tests for `app.services.slicer_client` against the pinned
`ghcr.io/maziggy/bambu-studio-api` sidecar image. The real endpoints are
UNVERIFIED (no live image available) -- these tests pin this module's OWN
tolerant parsing/request-shape behavior against mocked responses via respx,
not a verified real API contract. Swapping to our own FastAPI-over-CLI
wrapper later only touches `_ENDPOINTS`/`_build_slice_request`/
`_parse_health` in slicer_client.py.
"""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest
import respx

from app.services.app_config import AppConfig
from app.services.slicer_client import (
    SliceOptions,
    SlicerClient,
    SlicerError,
    SlicerUnavailable,
    client_from_config,
)

BASE_URL = "http://slicer:3001"


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plate_1.gcode", "G1\n")
    return buf.getvalue()


@respx.mock
def test_health_contract():
    respx.get(f"{BASE_URL}/health").mock(
        return_value=httpx.Response(200, json={"version": "1.2.5.7", "status": "ok"})
    )
    client = SlicerClient(BASE_URL)
    result = client.health()
    assert result == {"version": "1.2.5.7", "ok": True}


@respx.mock
def test_slice_contract():
    body = _zip_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        content_type = request.headers["content-type"]
        assert content_type.startswith("multipart/form-data")
        # multipart body contains the model filename and all profile parts.
        raw = request.content
        assert b'filename="model.3mf"' in raw
        assert b'name="printer_profile"' in raw
        assert b'name="process_profile"' in raw
        assert b'name="filament_profiles"' in raw
        assert b'name="bed_type"' in raw
        assert b"textured_pei" in raw
        assert b'name="options"' in raw
        return httpx.Response(200, content=body, headers={"content-type": "application/zip"})

    respx.post(f"{BASE_URL}/slice").mock(side_effect=handler)
    client = SlicerClient(BASE_URL)
    result = client.slice(
        model=b"fake-3mf-bytes",
        filename="model.3mf",
        printer_profile={"name": "A1 mini"},
        process_profile={"name": "0.20mm Standard"},
        filament_profiles=[{"name": "PLA"}],
        bed_type="textured_pei",
        options=SliceOptions(auto_orient=True, plate=1),
    )
    assert result == body


@respx.mock
def test_slice_non_2xx_raises_slicer_error():
    respx.post(f"{BASE_URL}/slice").mock(return_value=httpx.Response(500, text="boom"))
    client = SlicerClient(BASE_URL)
    with pytest.raises(SlicerError):
        client.slice(
            model=b"x",
            filename="model.3mf",
            printer_profile={},
            process_profile={},
            filament_profiles=[],
            bed_type="textured_pei",
            options=SliceOptions(),
        )


@respx.mock
def test_slice_non_zip_body_raises_slicer_error():
    respx.post(f"{BASE_URL}/slice").mock(
        return_value=httpx.Response(
            200, content=b"not a zip", headers={"content-type": "application/zip"}
        )
    )
    client = SlicerClient(BASE_URL)
    with pytest.raises(SlicerError):
        client.slice(
            model=b"x",
            filename="model.3mf",
            printer_profile={},
            process_profile={},
            filament_profiles=[],
            bed_type="textured_pei",
            options=SliceOptions(),
        )


def test_slice_connect_error_raises_slicer_unavailable():
    client = SlicerClient(
        BASE_URL,
        http=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))
            )
        ),
    )
    with pytest.raises(SlicerUnavailable):
        client.health()


def test_client_from_config_none():
    cfg = AppConfig(
        printer_enabled=False,
        scan_interval_s=0,
        collection_sync_interval_s=0,
        watch_interval_s=0,
        watch_stable_s=10.0,
        slicer_url=None,
    )
    assert client_from_config(cfg) is None


def test_client_from_config_builds_client():
    cfg = AppConfig(
        printer_enabled=False,
        scan_interval_s=0,
        collection_sync_interval_s=0,
        watch_interval_s=0,
        watch_stable_s=10.0,
        slicer_url=BASE_URL,
    )
    client = client_from_config(cfg)
    assert isinstance(client, SlicerClient)
