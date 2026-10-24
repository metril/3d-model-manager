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
from dataclasses import replace

import httpx
import paho.mqtt.client as mqtt
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import File, Printer, PrintQueueEntry
from app.models.enums import PrinterKind, QueueState, ReviewState, VirtualPrinterMode
from app.virtual_printer import __main__ as vp_main
from app.virtual_printer import service as vp_service
from app.virtual_printer.intake import VpConfig, VpNetwork
from app.virtual_printer.service import VirtualPrinterService, preflight
from app.virtual_printer.ssdp import SEARCH_TARGET

pytestmark = pytest.mark.usefixtures("library_root", "data_dir", "redis_url")

CODE = "12345678"
SERIAL = "03000A123456789"


ZERO_NET = VpNetwork(
    bind="127.0.0.1", port_ssdp=0, port_mqtt=0, port_ftp=0, pasv_min=0, pasv_max=0, pasv_address=""
)


@pytest.fixture
def vp_env():
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
        svc = VirtualPrinterService(vp_env, cfg, ZERO_NET)
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


def test_preflight_reports_busy_port():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    try:
        problems = preflight(replace(ZERO_NET, port_mqtt=port))
    finally:
        blocker.close()
    assert len(problems) == 1 and f"MQTT port {port}/tcp" in problems[0]


def _vp(**kw) -> VpConfig:
    base = dict(
        id=1, name="V", model="N1", serial=SERIAL, access_code=CODE,
        mode=VirtualPrinterMode.QUEUE, target_printer_id=None, created_by_user_id=None,
    )  # fmt: skip
    return VpConfig(**{**base, **kw})


class _Harness:
    """Drives ``vp_main.run`` with fake config + status sink, stopping after N polls."""

    def __init__(self, vp_env, wanted_seq, polls):
        self.wanted_seq, self.polls, self.statuses, self.services = list(wanted_seq), polls, [], []
        self.stop = asyncio.Event()
        self.vp_env, self.n = vp_env, 0

    def load(self, _s):
        w = self.wanted_seq[min(self.n - 1, len(self.wanted_seq) - 1)]
        if isinstance(w, Exception):
            raise w
        return w

    def publish(self, _s, status):
        self.statuses.append(status)

    def on_poll(self):
        self.n += 1
        if self.n > self.polls:
            self.stop.set()

    def make(self, settings, vp, net):
        svc = VirtualPrinterService(settings, vp, net)
        self.services.append(svc)
        return svc

    async def go(self):
        rc = await vp_main.run(
            self.vp_env, load=self.load, publish=self.publish, poll_s=0.01, stop=self.stop,
            on_poll=self.on_poll, make_service=self.make,
        )  # fmt: skip
        return rc


async def test_idle_binds_nothing(vp_env, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not bind")

    monkeypatch.setattr(vp_main, "preflight", boom)
    h = _Harness(vp_env, [(None, ZERO_NET)], polls=2)
    assert await h.go() == 0
    assert not h.services
    st = h.statuses[0]
    assert st["state"] == "idle" and st["vp_id"] is None and st["errors"] == []
    assert set(st) == {"state", "vp_id", "bind", "ports", "pasv", "errors", "ts"}


async def test_enabled_row_runs_and_status_shape(vp_env):
    h = _Harness(vp_env, [(_vp(), ZERO_NET)], polls=1)
    assert await h.go() == 0
    st = h.statuses[0]
    assert st["state"] == "running" and st["vp_id"] == 1 and st["bind"] == "127.0.0.1"
    assert st["ports"] == {"ssdp": 0, "mqtt": 0, "ftp": 0} and st["pasv"] == [0, 0]
    assert len(h.services) == 1


async def test_port_conflict_is_error_status_and_never_exits(vp_env):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    bad = replace(ZERO_NET, port_mqtt=port)
    h = _Harness(vp_env, [(_vp(), bad), (_vp(), bad), (_vp(), ZERO_NET)], polls=4)
    try:
        assert await h.go() == 0
    finally:
        blocker.close()
    states = [s["state"] for s in h.statuses]
    assert states[0] == "error" and "MQTT" in h.statuses[0]["errors"][0]
    assert states[1] == "error"  # retried while conflict persists
    assert states[-1] == "running"  # recovers once the network changes
    assert len(h.services) == 1


async def test_network_change_restarts_service(vp_env):
    def free(kind):
        with socket.socket(socket.AF_INET, kind) as t:
            t.bind(("127.0.0.1", 0))
            return t.getsockname()[1]

    fixed = replace(
        ZERO_NET,
        port_ssdp=free(socket.SOCK_DGRAM),
        port_mqtt=free(socket.SOCK_STREAM),
        port_ftp=free(socket.SOCK_STREAM),
    )
    other = replace(fixed, pasv_min=1, pasv_max=2)
    h = _Harness(vp_env, [(_vp(), fixed), (_vp(), fixed), (_vp(), other)], polls=4)
    await h.go()
    assert len(h.services) == 2
    assert "running" in [s["state"] for s in h.statuses]
    assert all(s["state"] != "error" for s in h.statuses)  # same ports rebound cleanly
    assert h.services[1].net == other and h.statuses[-1]["pasv"] == [1, 2]


async def test_disabling_row_stops_service(vp_env):
    h = _Harness(vp_env, [(_vp(), ZERO_NET), (None, ZERO_NET)], polls=3)
    await h.go()
    assert [s["state"] for s in h.statuses][0] == "running"
    assert h.statuses[-1]["state"] == "idle"


async def test_db_failure_publishes_error_and_keeps_looping(vp_env):
    h = _Harness(vp_env, [RuntimeError("db down"), (None, ZERO_NET)], polls=2)
    assert await h.go() == 0
    assert h.statuses[0]["state"] == "error" and "db down" in h.statuses[0]["errors"][0]
    assert h.statuses[-1]["state"] == "idle"


def test_publish_status_swallows_redis_failure(vp_env, monkeypatch):
    monkeypatch.setattr(
        vp_main.redis.Redis, "from_url", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
    )
    vp_main.publish_status(vp_env, {"state": "idle"})  # must not raise


def test_publish_status_writes_key_with_ttl(vp_env, monkeypatch):
    calls = []

    class Fake:
        def set(self, k, v, ex=None):
            calls.append((k, json.loads(v), ex))

        def close(self):
            pass

    monkeypatch.setattr(vp_main.redis.Redis, "from_url", lambda *a, **k: Fake())
    vp_main.publish_status(vp_env, {"state": "idle"})
    assert calls == [("tdmm:vp:status", {"state": "idle"}, 30)]


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


async def test_run_seeds_before_polling(vp_env):
    h = _Harness(vp_env, [(None, ZERO_NET)], polls=1)
    order = []
    h.on_poll_orig = h.on_poll
    await vp_main.run(
        vp_env,
        load=h.load,
        publish=h.publish,
        poll_s=0.01,
        stop=h.stop,
        on_poll=lambda: (order.append("poll"), h.on_poll())[1],
        seed=lambda: order.append("seed"),
        make_service=h.make,
    )
    assert order[0] == "seed" and "poll" in order
