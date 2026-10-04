"""Moonraker / Klipper PrinterAdapter (SPEC "Printer integration").

Moonraker is the HTTP / WebSocket API server for Klipper (used by Fluidd,
Mainsail, and modern Klipper printers like the Qidi Q2 / Qidi series).
This adapter interacts with Moonraker over its REST API:
- Probe: GET /server/info & GET /printer/info
- Status: GET /printer/objects/query?print_stats&display_status&extruder&heater_bed
- Control: POST /printer/print/pause, POST /printer/print/resume, POST /printer/print/cancel
- Upload & Print: POST /server/files/upload (multipart with print=true)
"""

from __future__ import annotations

import contextlib
import io
import re
import zipfile
from typing import ClassVar
from urllib.parse import urlparse

import httpx

from app.models.enums import PrinterKind, PrintJobState
from app.printers.base import (
    PrinterAdapter,
    PrinterConnection,
    PrinterPublicState,
    PrintSpec,
    ProbeResult,
    ReportHandler,
)
from app.printers.registry import register_adapter

_PLATE_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")

_MOONRAKER_STATE_MAP = {
    "standby": "IDLE",
    "ready": "IDLE",
    "printing": "RUNNING",
    "paused": "PAUSE",
    "complete": "FINISH",
    "error": "FAILED",
    "cancelled": "CANCELED",
}

_GCODE_STATE_TO_JOB = {
    "PREPARE": PrintJobState.STARTING,
    "RUNNING": PrintJobState.PRINTING,
    "PAUSE": PrintJobState.PAUSED,
    "FINISH": PrintJobState.FINISHED,
    "FAILED": PrintJobState.FAILED,
    "CANCELED": PrintJobState.CANCELED,
}


def normalize_base_url(host: str) -> str:
    """Ensure host has a scheme (default http://) and clean trailing slashes."""
    host = host.strip()
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    parsed = urlparse(host)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def extract_gcode_bytes(source_path, plate: int = 1) -> tuple[str, bytes]:
    """Extract gcode bytes and target filename from raw .gcode or .gcode.3mf."""
    raw = source_path.read_bytes()
    name = source_path.name
    if zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            target_name = f"Metadata/plate_{plate}.gcode"
            if target_name in names:
                clean_name = (
                    name.removesuffix(".3mf").removesuffix(".gcode") + f"_plate_{plate}.gcode"
                )
                return clean_name, zf.read(target_name)
            # fallback: look for any .gcode file in the archive
            for n in names:
                if n.endswith(".gcode"):
                    clean_name = name.removesuffix(".3mf").removesuffix(".gcode") + ".gcode"
                    return clean_name, zf.read(n)
    # Raw gcode
    if not name.endswith(".gcode"):
        name = f"{name}.gcode"
    return name, raw


@register_adapter
class MoonrakerAdapter(PrinterAdapter):
    kind: ClassVar[PrinterKind] = PrinterKind.MOONRAKER

    def __init__(self, conn: PrinterConnection) -> None:
        super().__init__(conn)
        self.base_url = normalize_base_url(conn.host)
        self._handler: ReportHandler | None = None
        self._client: httpx.Client | None = None

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.conn.access_code and self.conn.access_code.strip():
            headers["X-Api-Key"] = self.conn.access_code.strip()
        return headers

    def _http_client(self, timeout: float = 10.0) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=self._get_headers(),
                timeout=timeout,
            )
        return self._client

    # -- probe ---------------------------------------------------------
    def test_connection(self, *, timeout: float = 10.0) -> ProbeResult:
        """Probes Moonraker / Klipper API via HTTP GET /server/info and /printer/info."""
        # If no port was specified in host and port 80 fails with connection error,
        # we try port 7125 (standard Moonraker port) as a fallback.
        parsed = urlparse(self.base_url)
        urls_to_try = [self.base_url]
        if not parsed.port and parsed.hostname:
            urls_to_try.append(f"{parsed.scheme}://{parsed.hostname}:7125")

        last_error = ""
        for url in urls_to_try:
            try:
                with httpx.Client(
                    base_url=url, headers=self._get_headers(), timeout=timeout
                ) as client:
                    # Check server info
                    resp = client.get("/server/info")
                    if resp.status_code == 401:
                        return ProbeResult(ok=False, detail="Moonraker API Key required or invalid")
                    resp.raise_for_status()
                    data = resp.json().get("result", {})
                    moonraker_ver = data.get("moonraker_version", "unknown")

                    # Check printer info
                    klipper_state = "unknown"
                    gcode_state = "IDLE"
                    try:
                        p_resp = client.get("/printer/info")
                        if p_resp.is_success:
                            p_data = p_resp.json().get("result", {})
                            klipper_state = p_data.get("state", "ready")
                    except Exception:
                        pass

                    # Query print stats for actual gcode state
                    try:
                        s_resp = client.get("/printer/objects/query?print_stats")
                        if s_resp.is_success:
                            ps = (
                                s_resp.json()
                                .get("result", {})
                                .get("status", {})
                                .get("print_stats", {})
                            )
                            st = str(ps.get("state", "standby")).lower()
                            gcode_state = _MOONRAKER_STATE_MAP.get(st, "IDLE")
                    except Exception:
                        pass

                    if url != self.base_url:
                        self.base_url = url
                        # The cached client is bound to the old base_url.
                        self.close()
                        self._client = None

                    detail = f"Connected to Moonraker v{moonraker_ver} (Klipper: {klipper_state})"
                    return ProbeResult(ok=True, detail=detail, gcode_state=gcode_state)
            except httpx.HTTPStatusError as exc:
                last_error = f"HTTP {exc.response.status_code}: {exc.response.text}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"

        return ProbeResult(
            ok=False, detail=f"Could not connect to Moonraker at {self.base_url} ({last_error})"
        )

    # -- lifecycle -----------------------------------------------------
    def connect(self) -> None:
        self._http_client()

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            self._client.close()
        self._client = None

    def set_report_handler(self, handler: ReportHandler) -> None:
        self._handler = handler

    def request_full_status(self) -> None:
        """Polls Moonraker /printer/objects/query and emits snapshot to handler."""
        if self._handler is None:
            return
        try:
            client = self._http_client(timeout=5.0)
            resp = client.get(
                "/printer/objects/query?print_stats&display_status&extruder&heater_bed&heater_generic%20chamber&output_pin%20caselight"
            )
            if not resp.is_success:
                return
            status_data = resp.json().get("result", {}).get("status", {})
            snapshot = self._normalize_status(status_data)
            self._handler(snapshot)
        except Exception:
            # Tolerant: poll failure does not crash printerd
            pass

    def _normalize_status(self, status: dict) -> dict:
        print_stats = status.get("print_stats") or {}
        display_status = status.get("display_status") or {}
        extruder = status.get("extruder") or {}
        heater_bed = status.get("heater_bed") or {}
        caselight = status.get("output_pin caselight") or {}
        light_on = (
            (caselight.get("value") or 0.0) > 0.0 if "output_pin caselight" in status else None
        )

        ps_state = str(print_stats.get("state", "standby")).lower()
        gcode_state = _MOONRAKER_STATE_MAP.get(ps_state, ps_state.upper())

        progress = display_status.get("progress")
        mc_percent = int(progress * 100) if progress is not None else None

        info = print_stats.get("info") or {}
        layer_num = info.get("current_layer")
        total_layer_num = info.get("total_layer")

        print_duration = print_stats.get("print_duration") or 0.0
        remaining_time = None
        if progress and progress > 0.01 and print_duration > 0:
            total_est = print_duration / progress
            remaining_time = max(0, int(total_est - print_duration))

        return {
            "gcode_state": gcode_state,
            "mc_percent": mc_percent,
            "layer_num": layer_num,
            "total_layer_num": total_layer_num,
            "mc_remaining_time": remaining_time,
            "print_error": 1 if ps_state == "error" else None,
            "nozzle_temper": extruder.get("temperature"),
            "bed_temper": heater_bed.get("temperature"),
            "subtask_name": print_stats.get("filename") or None,
            "wifi_signal": None,
            "trays": [],
            "light_on": light_on,
        }

    # -- state conversion ----------------------------------------------
    def merge_report(self, prev: dict | None, report: dict) -> dict:
        return dict(report)

    def public_state(self, merged: dict) -> PrinterPublicState:
        return PrinterPublicState(
            gcode_state=merged.get("gcode_state"),
            mc_percent=merged.get("mc_percent"),
            layer_num=merged.get("layer_num"),
            total_layer_num=merged.get("total_layer_num"),
            mc_remaining_time=merged.get("mc_remaining_time"),
            print_error=merged.get("print_error"),
            nozzle_temper=merged.get("nozzle_temper"),
            bed_temper=merged.get("bed_temper"),
            subtask_name=merged.get("subtask_name"),
            wifi_signal=merged.get("wifi_signal"),
            trays=merged.get("trays") or [],
            light_on=merged.get("light_on"),
        )

    def set_light(self, on: bool) -> None:
        """Control enclosure/chamber light via Moonraker."""
        val = 1 if on else 0
        cmd = f"SET_PIN PIN=caselight VALUE={val}"
        client = self._http_client(timeout=5.0)
        try:
            client.post("/printer/gcode/script", json={"script": cmd}).raise_for_status()
        except Exception:
            with contextlib.suppress(Exception):
                client.post("/printer/gcode/script", json={"script": f"M355 S{val}"})

    def job_state(self, public: PrinterPublicState) -> PrintJobState | None:
        if public.print_error:
            return PrintJobState.FAILED
        return _GCODE_STATE_TO_JOB.get(public.gcode_state or "")

    # -- commands ------------------------------------------------------
    def pause(self) -> None:
        client = self._http_client()
        resp = client.post("/printer/print/pause")
        resp.raise_for_status()

    def resume(self) -> None:
        client = self._http_client()
        resp = client.post("/printer/print/resume")
        resp.raise_for_status()

    def stop(self) -> None:
        client = self._http_client()
        resp = client.post("/printer/print/cancel")
        resp.raise_for_status()

    # -- send ----------------------------------------------------------
    def upload_and_start(self, spec: PrintSpec) -> None:
        """Uploads file to Moonraker and initiates print."""
        _, gcode_data = extract_gcode_bytes(spec.source_path, plate=spec.plate)
        # Per-job remote name; the bytes sent are always plain gcode.
        target_name = spec.remote_name.removesuffix(".3mf")
        if not target_name.endswith(".gcode"):
            target_name = f"{target_name}.gcode"
        client = self._http_client(timeout=60.0)

        # Moonraker file upload endpoint: POST /server/files/upload
        # with multipart form fields: file=(filename, bytes), print="true"
        files = {
            "file": (target_name, gcode_data, "application/octet-stream"),
        }
        data = {
            "print": "true",
            "root": "gcodes",
        }
        resp = client.post("/server/files/upload", files=files, data=data)
        if not resp.is_success:
            raise RuntimeError(f"Moonraker upload failed ({resp.status_code}): {resp.text}")

    def get_camera_urls(self) -> dict[str, str | None]:
        """Fetch configured webcams from Moonraker or fallback to standard /webcam/ URLs."""
        base_url = normalize_base_url(self.conn.host)
        parsed = urlparse(base_url)
        hostname = parsed.hostname or self.conn.host
        headers = {}
        if self.conn.access_code:
            headers["X-Api-Key"] = self.conn.access_code

        # Attempt to probe Moonraker's /server/webcams/list across common ports
        candidates = [base_url]
        if parsed.port != 7125:
            candidates.append(f"{parsed.scheme}://{hostname}:7125")
        if parsed.port and parsed.port != 80:
            candidates.append(f"{parsed.scheme}://{hostname}")

        for endpoint in candidates:
            try:
                resp = httpx.get(
                    f"{endpoint}/server/webcams/list",
                    headers=headers,
                    timeout=3.0,
                    follow_redirects=False,
                )
                if resp.is_success:
                    data = resp.json()
                    webcams = data.get("result", {}).get("webcams", [])
                    for cam in webcams:
                        if cam.get("enabled", True):
                            stream_rel = cam.get("stream_url") or "/webcam/?action=stream"
                            snapshot_rel = cam.get("snapshot_url") or "/webcam/?action=snapshot"

                            def to_abs(url: str, default: str) -> str:
                                # Webcam config is remote-controlled data: whatever
                                # the final URL is, it must stay on the printer's host.
                                if url.startswith(("http://", "https://")):
                                    result = url
                                else:
                                    path = url if url.startswith("/") else f"/{url}"
                                    result = f"http://{hostname}{path}"
                                if urlparse(result).hostname == hostname:
                                    return result
                                return f"http://{hostname}{default}"

                            return {
                                "name": cam.get("name", "Camera"),
                                "stream_url": to_abs(stream_rel, "/webcam/?action=stream"),
                                "snapshot_url": to_abs(snapshot_rel, "/webcam/?action=snapshot"),
                                "aspect_ratio": cam.get("aspect_ratio", "4:3"),
                            }
            except Exception:
                continue

        return {
            "name": "Camera",
            "stream_url": f"http://{hostname}/webcam/?action=stream",
            "snapshot_url": f"http://{hostname}/webcam/?action=snapshot",
            "aspect_ratio": "4:3",
        }
