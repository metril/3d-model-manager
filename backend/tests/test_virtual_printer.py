"""Q11 virtual printer: SSDP / MQTT / FTPS servers on ephemeral ports driven
by paho + ftplib, the upload <-> ``project_file`` correlation into the print
queue, the startup port pre-flight, and the CRUD routes."""

from __future__ import annotations

import asyncio
import ftplib
import io
import json
import socket
import ssl
import threading
import time
import zipfile
from collections.abc import AsyncIterator, Callable

import httpx
import paho.mqtt.client as mqtt
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import File, Printer, PrintQueueEntry
from app.models.enums import PrinterKind, QueueState, ReviewState, VirtualPrinterMode
from app.virtual_printer import service as vp_service
from app.virtual_printer.intake import VpConfig
from app.virtual_printer.service import VirtualPrinterService, preflight
from app.virtual_printer.ssdp import SEARCH_TARGET

pytestmark = pytest.mark.usefixtures("library_root", "data_dir", "redis_url")

CODE = "12345678"
SERIAL = "03000A123456789"


@pytest.fixture
def vp_env(monkeypatch: pytest.MonkeyPatch):
    for k, v in {
        "TDMM_VP_BIND": "127.0.0.1",
        "TDMM_VP_PORT_SSDP": "0",
        "TDMM_VP_PORT_MQTT": "0",
        "TDMM_VP_PORT_FTP": "0",
        "TDMM_VP_PASV_MIN": "0",
        "TDMM_VP_PASV_MAX": "0",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
async def make_service(vp_env) -> AsyncIterator[Callable[..., VirtualPrinterService]]:
    started: list[VirtualPrinterService] = []

    async def make(mode=VirtualPrinterMode.QUEUE, target=None, user_id=None):
        cfg = VpConfig(
            id=1,
            name="Virtual A1 Mini",
            model="N1",
            serial=SERIAL,
            access_code=CODE,
            mode=mode,
            target_printer_id=target,
            created_by_user_id=user_id,
        )
        svc = VirtualPrinterService(vp_env, cfg)
        await svc.start()
        started.append(svc)
        return svc

    yield make
    for svc in started:
        await svc.stop()


def sliced_3mf(name_marker: str = "x") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/plate_1.gcode", f"; {name_marker}\nG1 X1\n" * 20)
        zf.writestr("3D/3dmodel.model", "<model/>")
    return buf.getvalue()


class ImplicitFTPS(ftplib.FTP_TLS):
    def connect(self, host="", port=0, timeout=10, source_address=None):
        self.host, self.port = host, port
        self.sock = socket.create_connection((host, port), timeout)
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


def _ftp_upload(port: int, name: str, data: bytes, code: str = CODE) -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ftp = ImplicitFTPS(context=ctx)
    ftp.connect("127.0.0.1", port)
    ftp.login("bblp", code)
    ftp.prot_p()
    ftp.storbinary(f"STOR {name}", io.BytesIO(data))
    ftp.quit()


class MqttSession:
    def __init__(self, port: int, code: str = CODE):
        self.reports: list[dict] = []
        self.rc: int | None = None
        self._connected = threading.Event()
        self._got = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="studio-test")
        self.client.username_pw_set("bblp", code)
        self.client.tls_set(cert_reqs=ssl.CERT_NONE)
        self.client.tls_insecure_set(True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.port = port

    def _on_connect(self, _c, _u, _f, rc, _p=None):
        self.rc = int(getattr(rc, "value", rc))
        self._connected.set()

    def _on_message(self, _c, _u, msg):
        self.reports.append(json.loads(msg.payload))
        self._got.set()

    def open(self) -> bool:
        self.client.connect("127.0.0.1", self.port)
        self.client.loop_start()
        return self._connected.wait(10)

    def wait_report(self, pred=lambda r: True, timeout=10) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            for r in list(self.reports):
                if pred(r):
                    return r
            self._got.clear()
            self._got.wait(0.2)
        return None

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


async def _wait_results(svc: VirtualPrinterService, n: int = 1, timeout: float = 20):
    end = time.time() + timeout
    while time.time() < end and len(svc.results) < n:
        await asyncio.sleep(0.1)
    assert len(svc.results) >= n, "upload was never ingested"


async def _studio_print(svc: VirtualPrinterService, name: str = "Benchy") -> MqttSession:
    s = MqttSession(svc.mqtt.port)
    assert await asyncio.to_thread(s.open)
    s.client.subscribe(f"device/{SERIAL}/report")
    info = s.client.publish(
        f"device/{SERIAL}/request",
        json.dumps(
            {
                "print": {
                    "command": "project_file",
                    "sequence_id": "7",
                    "param": "Metadata/plate_1.gcode",
                    "subtask_name": name,
                    "url": f"ftp://127.0.0.1/{name}.3mf",
                    "ams_mapping": [0, -1, -1, -1],
                }
            }
        ),
        qos=1,
    )
    await asyncio.to_thread(info.wait_for_publish, 10)
    return s


async def _entries(db: AsyncSession) -> list[PrintQueueEntry]:
    db.expire_all()
    return list((await db.scalars(select(PrintQueueEntry))).all())


# -- MQTT --------------------------------------------------------------
async def test_pushall_returns_idle_report(make_service):
    svc = await make_service()
    s = MqttSession(svc.mqtt.port)
    assert await asyncio.to_thread(s.open)
    assert s.rc == 0
    s.client.subscribe(f"device/{SERIAL}/report")
    s.client.publish(
        f"device/{SERIAL}/request",
        json.dumps({"pushing": {"sequence_id": "1", "command": "pushall"}}),
    )
    report = await asyncio.to_thread(s.wait_report, lambda r: "print" in r)
    s.close()
    assert report is not None
    assert report["print"]["command"] == "push_status"
    assert report["print"]["gcode_state"] == "IDLE"


async def test_get_version_reports_product_name(make_service):
    svc = await make_service()
    s = MqttSession(svc.mqtt.port)
    assert await asyncio.to_thread(s.open)
    s.client.subscribe(f"device/{SERIAL}/report")
    s.client.publish(
        f"device/{SERIAL}/request",
        json.dumps({"info": {"sequence_id": "3", "command": "get_version"}}),
    )
    report = await asyncio.to_thread(s.wait_report, lambda r: "info" in r)
    s.close()
    assert report is not None
    assert report["info"]["module"][0]["product_name"] == "A1 mini"
    assert report["info"]["module"][0]["sn"] == SERIAL


async def test_wrong_access_code_is_rejected(make_service):
    svc = await make_service()
    s = MqttSession(svc.mqtt.port, code="00000000")
    assert await asyncio.to_thread(s.open)
    assert s.rc not in (0, None)
    s.close()
    with pytest.raises(ftplib.error_perm):
        await asyncio.to_thread(_ftp_upload, svc.ftps.port, "a.3mf", sliced_3mf(), "00000000")


# -- upload + project_file -> queue -------------------------------------
async def test_upload_then_project_file_creates_queue_entry(make_service, db_session):
    svc = await make_service()
    await asyncio.to_thread(_ftp_upload, svc.ftps.port, "Benchy.3mf", sliced_3mf())
    s = await _studio_print(svc)
    await _wait_results(svc)
    ack = await asyncio.to_thread(
        s.wait_report, lambda r: r.get("print", {}).get("command") == "project_file"
    )
    s.close()
    assert ack is not None and ack["print"]["result"] == "SUCCESS"

    (entry,) = await _entries(db_session)
    assert entry.state == QueueState.READY
    assert entry.review_state == ReviewState.NONE
    assert entry.printer_id is None
    assert entry.plate == 1
    assert entry.ams_mapping is None  # Studio's mapping targets the VP, not a real AMS
    f = await db_session.get(File, entry.file_id)
    assert f.rel_path == "Benchy.gcode.3mf"
    assert svc.mqtt.gcode_state == "FINISH"


async def test_project_file_before_upload_also_queues(make_service, db_session):
    svc = await make_service()
    s = await _studio_print(svc, "Late")
    await asyncio.sleep(0.5)
    await asyncio.to_thread(_ftp_upload, svc.ftps.port, "Late.3mf", sliced_3mf("late"))
    await _wait_results(svc)
    s.close()
    assert len(await _entries(db_session)) == 1


async def test_review_mode_is_pending(make_service, db_session):
    svc = await make_service(VirtualPrinterMode.REVIEW)
    await asyncio.to_thread(_ftp_upload, svc.ftps.port, "Rev.3mf", sliced_3mf("rev"))
    (await _studio_print(svc, "Rev")).close()
    await _wait_results(svc)
    (entry,) = await _entries(db_session)
    assert entry.review_state == ReviewState.PENDING


async def test_proxy_mode_pins_target_printer(make_service, db_session):
    printer = Printer(
        name="Real A1M",
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.9",
        serial="R1",
        access_code_enc="x",
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)
    pid = printer.id
    svc = await make_service(VirtualPrinterMode.PROXY, target=pid)
    await asyncio.to_thread(_ftp_upload, svc.ftps.port, "Prox.3mf", sliced_3mf("prox"))
    (await _studio_print(svc, "Prox")).close()
    await _wait_results(svc)
    (entry,) = await _entries(db_session)
    assert entry.printer_id == pid and entry.state == QueueState.READY


async def test_proxy_without_target_falls_back_to_review(make_service, db_session):
    svc = await make_service(VirtualPrinterMode.PROXY, target=None)
    await asyncio.to_thread(_ftp_upload, svc.ftps.port, "Orph.3mf", sliced_3mf("orph"))
    (await _studio_print(svc, "Orph")).close()
    await _wait_results(svc)
    (entry,) = await _entries(db_session)
    assert entry.printer_id is None and entry.review_state == ReviewState.PENDING


async def test_archive_mode_stores_file_without_queueing(make_service, db_session):
    svc = await make_service(VirtualPrinterMode.ARCHIVE)
    await asyncio.to_thread(_ftp_upload, svc.ftps.port, "Arc.3mf", sliced_3mf("arc"))
    (await _studio_print(svc, "Arc")).close()
    await _wait_results(svc)
    assert await _entries(db_session) == []
    assert svc.results[0].queue_entry_id is None
    assert await db_session.get(File, svc.results[0].file_id) is not None


async def test_upload_without_command_is_archived_only(make_service, db_session, monkeypatch):
    monkeypatch.setattr(vp_service, "ORPHAN_UPLOAD_S", 0.3)
    svc = await make_service()
    await asyncio.to_thread(_ftp_upload, svc.ftps.port, "Solo.3mf", sliced_3mf("solo"))
    await _wait_results(svc)
    assert await _entries(db_session) == []


# -- SSDP / pre-flight -------------------------------------------------
async def test_ssdp_answers_msearch(make_service):
    svc = await make_service()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as c:
        c.settimeout(5)
        c.sendto(
            f"M-SEARCH * HTTP/1.1\r\nST: {SEARCH_TARGET}\r\n\r\n".encode(),
            ("127.0.0.1", svc.ssdp.port),
        )
        data, _ = await asyncio.to_thread(c.recvfrom, 2048)
    text = data.decode()
    assert f"USN: {SERIAL}" in text and "DevModel.bambu.com: N1" in text
    assert "DevName.bambu.com: Virtual A1 Mini" in text


def test_preflight_reports_busy_port(monkeypatch):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    monkeypatch.setenv("TDMM_VP_BIND", "127.0.0.1")
    monkeypatch.setenv("TDMM_VP_PORT_SSDP", "0")
    monkeypatch.setenv("TDMM_VP_PORT_MQTT", str(port))
    monkeypatch.setenv("TDMM_VP_PORT_FTP", "0")
    get_settings.cache_clear()
    try:
        problems = preflight(get_settings())
    finally:
        blocker.close()
        get_settings.cache_clear()
    assert len(problems) == 1 and f"MQTT port {port}/tcp" in problems[0]


# -- routes ------------------------------------------------------------
async def test_crud_routes(authenticated_client: httpx.AsyncClient, db_session):
    c = authenticated_client
    r = await c.post("/api/virtual-printers", json={"name": "Studio VP", "model": "N1"})
    assert r.status_code == 201, r.text
    vp = r.json()
    assert len(vp["access_code"]) == 8 and vp["access_code"].isdigit()
    assert vp["serial"].startswith("03000A") and len(vp["serial"]) == 15
    assert vp["mode"] == "queue" and vp["created_by_user_id"] is not None

    listing = (await c.get("/api/virtual-printers")).json()
    assert [v["id"] for v in listing] == [vp["id"]]

    r = await c.patch(
        f"/api/virtual-printers/{vp['id']}", json={"mode": "review", "regenerate_access_code": True}
    )
    assert r.json()["mode"] == "review" and r.json()["access_code"] != vp["access_code"]

    # proxy needs a real, existing target
    assert (
        await c.patch(f"/api/virtual-printers/{vp['id']}", json={"mode": "proxy"})
    ).status_code == 422
    r = await c.patch(
        f"/api/virtual-printers/{vp['id']}", json={"mode": "proxy", "target_printer_id": 99999}
    )
    assert r.status_code == 404
    assert (
        await c.post("/api/virtual-printers", json={"name": "x", "model": "nope"})
    ).status_code == 422

    assert (await c.delete(f"/api/virtual-printers/{vp['id']}")).status_code == 204
    assert (await c.get(f"/api/virtual-printers/{vp['id']}")).status_code == 404


async def test_features_flag(authenticated_client: httpx.AsyncClient, monkeypatch):
    monkeypatch.setenv("TDMM_VP_ENABLED", "true")
    get_settings.cache_clear()
    try:
        r = await authenticated_client.get("/api/features")
    finally:
        get_settings.cache_clear()
    assert r.json()["virtual_printer_enabled"] is True
