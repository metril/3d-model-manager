"""Task 5 (P4 camera plan): the adapter publishes the raw ``project_file``
MQTT command itself (bypassing the vendored ``start_print``) so
``bed_leveling``/``timelapse`` can come from ``PrintSpec`` instead of the
lib's hardcoded ``True``/missing value."""

import json

import pytest

from app.printers import bambu
from app.printers.bambu import BambuLanAdapter, project_file_payload
from app.printers.base import PrinterConnection, PrintSpec

CONN = PrinterConnection(host="1.2.3.4", serial="0309ABC", access_code="12345678")


def _spec(**overrides) -> PrintSpec:
    defaults = dict(
        source_path=overrides.pop("source_path", None),
        remote_name="tdmm-7.gcode.3mf",
        plate=2,
        subtask_name="w",
        use_ams=True,
        ams_mapping=(1,),
        bed_levelling=False,
        flow_cali=True,
        timelapse=True,
    )
    defaults.update(overrides)
    return PrintSpec(**defaults)


def test_project_file_payload_golden():
    spec = _spec(source_path=None)
    assert project_file_payload(spec) == {
        "print": {
            "command": "project_file",
            "param": "Metadata/plate_2.gcode",
            "file": "tdmm-7.gcode.3mf",
            "url": "ftp:///tdmm-7.gcode.3mf",
            "bed_leveling": False,
            "bed_type": "textured_plate",
            "flow_cali": True,
            "vibration_cali": True,
            "layer_inspect": False,
            "timelapse": True,
            "use_ams": True,
            "ams_mapping": [1],
            "sequence_id": "10000000",
            "skip_objects": None,
        }
    }


class _FakePublishResult:
    def __init__(self, published: bool) -> None:
        self._published = published

    def wait_for_publish(self, timeout=None):
        pass

    def is_published(self):
        return self._published


class _FakeMqttClient:
    def __init__(self, *, connected: bool, published: bool) -> None:
        self._connected = connected
        self._published = published
        self.published_calls: list[tuple[str, str]] = []

        class _Inner:
            def __init__(self, outer):
                self._outer = outer

            def is_connected(self):
                return self._outer._connected

            def publish(self, topic, payload):
                self._outer.published_calls.append((topic, payload))
                return _FakePublishResult(self._outer._published)

        self._client = _Inner(self)
        self.command_topic = "device/0309ABC/request"


class _FakePrinter:
    def __init__(self, mqtt_client) -> None:
        self.mqtt_client = mqtt_client
        self.uploaded: list[str] = []
        self.start_print_called = False

    def upload_file(self, file, filename):
        self.uploaded.append(filename)
        return f"/cache/{filename}"

    def start_print(self, *args, **kwargs):
        self.start_print_called = True
        return True


def _adapter_with(monkeypatch, printer) -> BambuLanAdapter:
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: printer)
    return BambuLanAdapter(CONN)


def test_upload_and_start_publishes_payload_and_never_calls_start_print(monkeypatch, tmp_path):
    mqtt = _FakeMqttClient(connected=True, published=True)
    printer = _FakePrinter(mqtt)
    a = _adapter_with(monkeypatch, printer)
    src = tmp_path / "job.gcode.3mf"
    src.write_bytes(b"zip")
    spec = _spec(source_path=src)

    a.upload_and_start(spec)

    assert printer.uploaded == ["tdmm-7.gcode.3mf"]
    assert printer.start_print_called is False
    assert len(mqtt.published_calls) == 1
    topic, payload = mqtt.published_calls[0]
    assert topic == "device/0309ABC/request"
    assert json.loads(payload) == project_file_payload(spec)


def test_upload_and_start_raises_when_mqtt_disconnected(monkeypatch, tmp_path):
    mqtt = _FakeMqttClient(connected=False, published=True)
    printer = _FakePrinter(mqtt)
    a = _adapter_with(monkeypatch, printer)
    src = tmp_path / "job.gcode.3mf"
    src.write_bytes(b"zip")

    with pytest.raises(OSError):
        a.upload_and_start(_spec(source_path=src))


def test_upload_and_start_raises_when_publish_not_confirmed(monkeypatch, tmp_path):
    mqtt = _FakeMqttClient(connected=True, published=False)
    printer = _FakePrinter(mqtt)
    a = _adapter_with(monkeypatch, printer)
    src = tmp_path / "job.gcode.3mf"
    src.write_bytes(b"zip")

    with pytest.raises(OSError):
        a.upload_and_start(_spec(source_path=src))
