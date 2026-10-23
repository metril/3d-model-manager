"""Contract tests for `app.services.slicer_client` against our slicer
sidecar (`slicer/`), with mocked responses via respx. The wrapper's own
tests (`slicer/tests`) build requests with `_build_slice_request`, so the
two stay in sync.
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
    SlicerBusy,
    SlicerClient,
    SlicerError,
    SlicerProtocolError,
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
        return_value=httpx.Response(
            200, json={"version": "v02.08", "status": "ok", "busy": True, "max_concurrency": 1}
        )
    )
    client = SlicerClient(BASE_URL)
    result = client.health()
    assert result == {"version": "v02.08", "ok": True, "busy": True, "max_concurrency": 1}


@respx.mock
@pytest.mark.parametrize(
    "body", [{"version": "1"}, {"status": "ok"}, {"status": 1, "version": "1"}]
)
def test_health_strict_missing_fields_raise_protocol_error(body):
    respx.get(f"{BASE_URL}/health").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(SlicerProtocolError):
        SlicerClient(BASE_URL).health()


@respx.mock
def test_health_non_ok_status_is_not_ok():
    respx.get(f"{BASE_URL}/health").mock(
        return_value=httpx.Response(200, json={"version": "1", "status": "degraded"})
    )
    assert SlicerClient(BASE_URL).health()["ok"] is False


@respx.mock
def test_slice_429_raises_slicer_busy_which_is_unavailable():
    respx.post(f"{BASE_URL}/slice").mock(
        return_value=httpx.Response(429, json={"error": "busy", "message": "slicer is busy"})
    )
    with pytest.raises(SlicerBusy) as exc_info:
        SlicerClient(BASE_URL).slice(
            model=b"x",
            filename="m.stl",
            printer_profile={},
            process_profile={},
            filament_profiles=[],
            bed_type="textured_plate",
            options=SliceOptions(),
        )
    assert isinstance(exc_info.value, SlicerUnavailable)


@respx.mock
def test_slice_error_json_surfaces_error_and_message_but_not_log_tail():
    respx.post(f"{BASE_URL}/slice").mock(
        return_value=httpx.Response(
            422,
            json={
                "error": "slice_failed",
                "message": "bambu-studio exited 3",
                "exit_code": 3,
                "log_tail": "secret-log-tail",
            },
        )
    )
    with pytest.raises(SlicerError) as exc_info:
        SlicerClient(BASE_URL).slice(
            model=b"x",
            filename="m.stl",
            printer_profile={},
            process_profile={},
            filament_profiles=[],
            bed_type="textured_plate",
            options=SliceOptions(),
        )
    assert "slice_failed: bambu-studio exited 3" in str(exc_info.value)
    assert "secret-log-tail" not in str(exc_info.value)


@respx.mock
def test_profiles_flatten_param_and_non_list():
    route = respx.get(f"{BASE_URL}/profiles").mock(
        return_value=httpx.Response(200, json=[{"kind": "printer", "name": "x"}])
    )
    assert SlicerClient(BASE_URL).profiles(flatten=True) == [{"kind": "printer", "name": "x"}]
    assert route.calls.last.request.url.params["flatten"] == "1"
    route.mock(return_value=httpx.Response(200, json={"a": 1}))
    assert SlicerClient(BASE_URL).profiles() is None


@respx.mock
def test_health_non_dict_body_raises_slicer_protocol_error():
    respx.get(f"{BASE_URL}/health").mock(return_value=httpx.Response(200, json=[1, 2, 3]))
    client = SlicerClient(BASE_URL)
    with pytest.raises(SlicerProtocolError):
        client.health()


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
def test_slice_non_2xx_never_leaks_response_body_to_the_exception():
    """M5: the sidecar's error body must only ever be logged server-side,
    never put in the exception message that can surface out to a queue
    entry's `last_error`."""
    secret_body = "super-secret-internal-stack-trace-detail"
    respx.post(f"{BASE_URL}/slice").mock(return_value=httpx.Response(500, text=secret_body))
    client = SlicerClient(BASE_URL)
    with pytest.raises(SlicerError) as exc_info:
        client.slice(
            model=b"x",
            filename="model.3mf",
            printer_profile={},
            process_profile={},
            filament_profiles=[],
            bed_type="textured_pei",
            options=SliceOptions(),
        )
    assert secret_body not in str(exc_info.value)


@respx.mock
def test_slice_response_over_size_cap_raises_slicer_error():
    """M5: a response over the configured cap is rejected instead of being
    buffered/written out."""
    from app.services import slicer_client as slicer_client_module

    oversized = _zip_bytes() + b"\x00" * 10
    respx.post(f"{BASE_URL}/slice").mock(
        return_value=httpx.Response(
            200, content=oversized, headers={"content-type": "application/zip"}
        )
    )
    client = SlicerClient(BASE_URL)
    old_cap = slicer_client_module._MAX_SLICE_RESPONSE_BYTES
    slicer_client_module._MAX_SLICE_RESPONSE_BYTES = len(oversized) - 1
    try:
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
    finally:
        slicer_client_module._MAX_SLICE_RESPONSE_BYTES = old_cap


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


def test_connect_timeout_raises_slicer_unavailable():
    client = SlicerClient(
        BASE_URL,
        http=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out"))
            )
        ),
    )
    with pytest.raises(SlicerUnavailable):
        client.health()


def test_read_timeout_raises_slicer_error_not_unavailable():
    """I1: a ReadTimeout means the sidecar WAS reached but took too long
    responding -- a real slicer-side failure that consumes a retry attempt,
    unlike `SlicerUnavailable` (couldn't reach it at all)."""
    client = SlicerClient(
        BASE_URL,
        http=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("read timed out"))
            )
        ),
    )
    with pytest.raises(SlicerError) as exc_info:
        client.health()
    assert not isinstance(exc_info.value, SlicerUnavailable)


def test_client_from_config_builds_client_with_slice_timeout():
    cfg = AppConfig(
        printer_enabled=False,
        scan_interval_s=0,
        collection_sync_interval_s=0,
        watch_interval_s=0,
        watch_stable_s=10.0,
        slicer_url=BASE_URL,
        slice_timeout_s=42,
    )
    client = client_from_config(cfg)
    assert client._slice_timeout_s == 42


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
