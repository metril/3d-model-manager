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
            "subtask_name": "w",
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


class _FakeFtp:
    def __init__(self, printer, result="226 ok", raises=None, listing=()) -> None:
        self._printer = printer
        self._result = result
        self._raises = raises
        self._listing = list(listing)

    def upload_file(self, fh, remote):
        self._printer.uploaded.append(remote)
        if self._raises:
            raise self._raises
        return self._result

    def list_directory(self, path):
        return "226", self._listing


def _adapter_with(monkeypatch, printer, ftp_result="226 ok", **ftp_kw) -> BambuLanAdapter:
    monkeypatch.setattr(bambu, "_build_printer", lambda conn: printer)
    monkeypatch.setattr(bambu, "_build_ftp", lambda conn: _FakeFtp(printer, ftp_result, **ftp_kw))
    return BambuLanAdapter(CONN)


def test_start_project_publishes_payload_and_never_calls_start_print(monkeypatch):
    mqtt = _FakeMqttClient(connected=True, published=True)
    printer = _FakePrinter(mqtt)
    a = _adapter_with(monkeypatch, printer)
    spec = _spec()

    a.start_project(spec)

    assert printer.uploaded == []  # FTPS is the worker's job, not start's
    assert printer.start_print_called is False
    assert len(mqtt.published_calls) == 1
    topic, payload = mqtt.published_calls[0]
    assert topic == "device/0309ABC/request"
    assert json.loads(payload) == project_file_payload(spec)


def test_start_project_raises_when_mqtt_disconnected(monkeypatch):
    printer = _FakePrinter(_FakeMqttClient(connected=False, published=True))
    a = _adapter_with(monkeypatch, printer)
    with pytest.raises(OSError):
        a.start_project(_spec())


def test_start_project_raises_when_publish_not_confirmed(monkeypatch):
    printer = _FakePrinter(_FakeMqttClient(connected=True, published=False))
    a = _adapter_with(monkeypatch, printer)
    with pytest.raises(OSError):
        a.start_project(_spec())


def test_upload_file_ftp_failure_raises(monkeypatch, tmp_path):
    mqtt = _FakeMqttClient(connected=True, published=True)
    printer = _FakePrinter(mqtt)
    a = _adapter_with(monkeypatch, printer, ftp_result=None)
    src = tmp_path / "job.gcode.3mf"
    src.write_bytes(b"zip")
    with pytest.raises(OSError):
        a.upload_file("tdmm-7.gcode.3mf", src)
    assert mqtt.published_calls == []


def _listing(size):
    return [f"-rw-r--r-- 1 root root {size} Jan 01 00:00 tdmm-7.gcode.3mf"]


def test_upload_timeout_after_all_bytes_sent_proceeds_when_size_matches(monkeypatch, tmp_path):
    mqtt = _FakeMqttClient(connected=True, published=True)
    printer = _FakePrinter(mqtt)
    a = _adapter_with(monkeypatch, printer, raises=TimeoutError("no 226"), listing=_listing(3))
    src = tmp_path / "job.gcode.3mf"
    src.write_bytes(b"zip")
    a.upload_file("tdmm-7.gcode.3mf", src)
    assert printer.uploaded == ["tdmm-7.gcode.3mf"]


def test_upload_error_with_size_mismatch_raises(monkeypatch, tmp_path):
    mqtt = _FakeMqttClient(connected=True, published=True)
    printer = _FakePrinter(mqtt)
    a = _adapter_with(monkeypatch, printer, raises=TimeoutError("no 226"), listing=_listing(1))
    src = tmp_path / "job.gcode.3mf"
    src.write_bytes(b"zip")
    with pytest.raises(OSError):
        a.upload_file("tdmm-7.gcode.3mf", src)
    assert mqtt.published_calls == []


def test_payload_includes_sanitised_subtask_name():
    p = project_file_payload(_spec(subtask_name="  My\x00 \n  Model\tName  "))["print"]
    assert p["subtask_name"] == "My Model Name"
    long = project_file_payload(_spec(subtask_name="a" * 200))["print"]["subtask_name"]
    assert len(long) == bambu.SUBTASK_NAME_MAX


@pytest.mark.parametrize("blank", ["", "  \x01\n "])
def test_payload_subtask_name_falls_back_to_remote_stem(blank):
    assert project_file_payload(_spec(subtask_name=blank))["print"]["subtask_name"] == "tdmm-7"
