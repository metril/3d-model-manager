import io
import zipfile
from pathlib import Path

import httpx

from app.models.enums import PrinterKind, PrintJobState
from app.printers.base import PrinterConnection, PrintSpec
from app.printers.moonraker import (
    MoonrakerAdapter,
    extract_gcode_bytes,
    normalize_base_url,
)
from app.printers.registry import build_adapter

_ORIG_CLIENT = httpx.Client

CONN = PrinterConnection(host="192.168.1.100:7125", serial="QIDIQ201", access_code="my-api-key")


def test_normalize_base_url():
    assert normalize_base_url("192.168.1.50") == "http://192.168.1.50"
    assert normalize_base_url("192.168.1.50:7125") == "http://192.168.1.50:7125"
    assert normalize_base_url("http://192.168.1.50:7125/") == "http://192.168.1.50:7125"
    assert normalize_base_url("https://printer.local") == "https://printer.local"


def test_moonraker_adapter_instantiation():
    adapter = build_adapter(PrinterKind.MOONRAKER, CONN)
    assert isinstance(adapter, MoonrakerAdapter)
    assert adapter.base_url == "http://192.168.1.100:7125"
    assert adapter._get_headers() == {"X-Api-Key": "my-api-key"}


def test_extract_gcode_bytes_raw(tmp_path: Path):
    gcode_file = tmp_path / "test.gcode"
    gcode_file.write_bytes(b"G28\nG1 X10 Y10\n")
    name, data = extract_gcode_bytes(gcode_file)
    assert name == "test.gcode"
    assert data == b"G28\nG1 X10 Y10\n"


def test_extract_gcode_bytes_3mf(tmp_path: Path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/plate_1.gcode", b"; Plate 1 gcode\nM104 S200\n")
        zf.writestr("3D/3dmodel.model", b"<model/>")
    archive = tmp_path / "model.gcode.3mf"
    archive.write_bytes(buf.getvalue())

    name, data = extract_gcode_bytes(archive, plate=1)
    assert name.endswith(".gcode")
    assert data == b"; Plate 1 gcode\nM104 S200\n"


def test_test_connection_success(monkeypatch):
    adapter = MoonrakerAdapter(CONN)

    def handler(request: httpx.Request):
        if request.url.path == "/server/info":
            return httpx.Response(200, json={"result": {"moonraker_version": "0.8.0"}})
        if request.url.path == "/printer/info":
            return httpx.Response(200, json={"result": {"state": "ready"}})
        if request.url.path == "/printer/objects/query":
            return httpx.Response(
                200, json={"result": {"status": {"print_stats": {"state": "standby"}}}}
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _ORIG_CLIENT(transport=transport, **kw))

    result = adapter.test_connection()
    assert result.ok is True
    assert "Moonraker v0.8.0" in result.detail
    assert result.gcode_state == "IDLE"


def test_test_connection_auth_failure(monkeypatch):
    adapter = MoonrakerAdapter(CONN)

    def handler(request: httpx.Request):
        return httpx.Response(401, text="Unauthorized")

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _ORIG_CLIENT(transport=transport, **kw))

    result = adapter.test_connection()
    assert result.ok is False
    assert "API Key required" in result.detail


def test_request_full_status(monkeypatch):
    adapter = MoonrakerAdapter(CONN)

    status_data = {
        "print_stats": {
            "state": "printing",
            "filename": "qidi_cube.gcode",
            "print_duration": 300.0,
            "info": {"current_layer": 15, "total_layer": 100},
        },
        "display_status": {"progress": 0.25},
        "extruder": {"temperature": 220.5, "target": 220.0},
        "heater_bed": {"temperature": 60.0, "target": 60.0},
    }

    def handler(request: httpx.Request):
        return httpx.Response(200, json={"result": {"status": status_data}})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _ORIG_CLIENT(transport=transport, **kw))

    reports = []
    adapter.set_report_handler(reports.append)
    adapter.request_full_status()

    assert len(reports) == 1
    snap = reports[0]
    assert snap["gcode_state"] == "RUNNING"
    assert snap["mc_percent"] == 25
    assert snap["layer_num"] == 15
    assert snap["total_layer_num"] == 100
    assert snap["nozzle_temper"] == 220.5
    assert snap["bed_temper"] == 60.0
    assert snap["subtask_name"] == "qidi_cube.gcode"

    pub = adapter.public_state(snap)
    assert pub.gcode_state == "RUNNING"
    assert pub.mc_percent == 25

    job_st = adapter.job_state(pub)
    assert job_st == PrintJobState.PRINTING


def test_pause_resume_stop(monkeypatch):
    adapter = MoonrakerAdapter(CONN)
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"result": "ok"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _ORIG_CLIENT(transport=transport, **kw))

    adapter.pause()
    adapter.resume()
    adapter.stop()

    assert calls == [
        "/printer/print/pause",
        "/printer/print/resume",
        "/printer/print/cancel",
    ]


def test_upload_and_start(tmp_path: Path, monkeypatch):
    adapter = MoonrakerAdapter(CONN)
    gcode = tmp_path / "print.gcode"
    gcode.write_bytes(b"G28\n")

    uploaded = {}

    def handler(request: httpx.Request):
        if request.url.path == "/server/files/upload":
            uploaded["content_type"] = request.headers.get("content-type", "")
            return httpx.Response(201, json={"result": {"print_started": True}})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _ORIG_CLIENT(transport=transport, **kw))

    spec = PrintSpec(
        source_path=gcode,
        remote_name="qidi.gcode",
        plate=1,
        subtask_name="print.gcode",
    )
    adapter.upload_and_start(spec)
    assert "multipart/form-data" in uploaded["content_type"]


def test_cancelled_maps_to_canceled_job_state():
    from app.printers.base import PrinterPublicState

    adapter = MoonrakerAdapter(CONN)
    merged = {"gcode_state": "CANCELED"}
    public = adapter.public_state(merged)
    assert isinstance(public, PrinterPublicState)
    assert adapter.job_state(public) == PrintJobState.CANCELED
    from app.printers.moonraker import _GCODE_STATE_TO_JOB, _MOONRAKER_STATE_MAP

    assert _GCODE_STATE_TO_JOB[_MOONRAKER_STATE_MAP["cancelled"]] == PrintJobState.CANCELED


def test_set_light_falls_back_to_m355_on_http_error(monkeypatch):
    adapter = MoonrakerAdapter(CONN)
    scripts = []

    def handler(request: httpx.Request):
        import json

        script = json.loads(request.content)["script"]
        scripts.append(script)
        return httpx.Response(400 if script.startswith("SET_PIN") else 200)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _ORIG_CLIENT(transport=transport, **kw))
    adapter.set_light(True)
    assert scripts == ["SET_PIN PIN=caselight VALUE=1", "M355 S1"]


def test_upload_uses_remote_name_for_gcode(tmp_path: Path, monkeypatch):
    adapter = MoonrakerAdapter(CONN)
    src = tmp_path / "orig.gcode.3mf"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/plate_1.gcode", "G28\n")
    src.write_bytes(buf.getvalue())
    uploaded = {}

    def handler(request: httpx.Request):
        uploaded["body"] = request.content
        return httpx.Response(201, json={})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _ORIG_CLIENT(transport=transport, **kw))
    adapter.upload_and_start(
        PrintSpec(source_path=src, remote_name="tdmm-7.gcode.3mf", plate=1, subtask_name="x")
    )
    assert b'filename="tdmm-7.gcode"' in uploaded["body"]


def test_camera_urls_reject_foreign_absolute_hosts(monkeypatch):
    adapter = MoonrakerAdapter(PrinterConnection(host="10.0.0.5", serial="S", access_code=""))
    payload = {
        "result": {
            "webcams": [
                {
                    "stream_url": "http://evil.example/x",
                    "snapshot_url": "http://10.0.0.5:8080/snap",
                }
            ]
        }
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200, json=payload))
    cam = adapter.get_camera_urls()
    assert cam["stream_url"] == "http://10.0.0.5/webcam/?action=stream"
    assert cam["snapshot_url"] == "http://10.0.0.5:8080/snap"


def test_test_connection_port_fallback_resets_cached_client(monkeypatch):
    adapter = MoonrakerAdapter(PrinterConnection(host="10.0.0.5", serial="S", access_code=""))
    old = adapter._http_client()

    def handler(request: httpx.Request):
        if request.url.port == 7125:
            return httpx.Response(200, json={"result": {"moonraker_version": "v1"}})
        raise httpx.ConnectError("refused")

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _ORIG_CLIENT(transport=transport, **kw))
    assert adapter.test_connection().ok
    assert adapter.base_url.endswith(":7125")
    assert old.is_closed and adapter._client is None


def _cam_for(monkeypatch, url: str, host: str = "10.0.0.5") -> dict:
    adapter = MoonrakerAdapter(PrinterConnection(host=host, serial="S", access_code=""))
    payload = {"result": {"webcams": [{"stream_url": url, "snapshot_url": url}]}}
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200, json=payload))
    return adapter.get_camera_urls()


def test_camera_urls_never_leave_printer_host(monkeypatch):
    from urllib.parse import urlparse

    for bad in ("@evil.com/x", ".evil.com/x", "//evil.com/x"):
        cam = _cam_for(monkeypatch, bad)
        for key in ("stream_url", "snapshot_url"):
            assert urlparse(cam[key]).hostname == "10.0.0.5", (bad, cam[key])


def test_camera_urls_plain_relative_and_same_host_port(monkeypatch):
    cam = _cam_for(monkeypatch, "webcam/snap")
    assert cam["snapshot_url"] == "http://10.0.0.5/webcam/snap"
    cam = _cam_for(monkeypatch, "http://10.0.0.5:8080/snap")
    assert cam["snapshot_url"] == "http://10.0.0.5:8080/snap"
