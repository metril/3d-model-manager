"""bambu_lan PrinterAdapter over the bambulabs_api Printer client (SPEC
"Printer integration": "Lib: bambulabs-api behind the adapter"). The lib
owns the MQTT session, FTPS upload, pushall, and the incremental status
merge; this adapter only NORMALIZES the lib's typed accessors into our
PrinterPublicState and forwards upload/start/commands. bambulabs_api is
lazy-imported inside _build_printer, so importing this module (and thus the
registry + the printers API router) is safe with the printer flag OFF.

``test_connection`` is the one exception (Round 8 T1/T2): it delegates to
``app.printers.probe.staged_probe``, a RAW paho MQTT probe independent of
the bambulabs_api client above -- see that module's docstring."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from app.models.enums import PrinterKind, PrintJobState
from app.printers import probe
from app.printers.base import (
    PrinterAdapter,
    PrinterConnection,
    PrinterPublicState,
    PrintSpec,
    ProbeResult,
    ReportHandler,
)
from app.printers.registry import register_adapter

_GCODE_STATE_TO_JOB = {
    "PREPARE": PrintJobState.STARTING,
    "RUNNING": PrintJobState.PRINTING,
    "PAUSE": PrintJobState.PAUSED,
    "FINISH": PrintJobState.FINISHED,
    "FAILED": PrintJobState.FAILED,
}


def _build_printer(conn: PrinterConnection):
    """Construct the bambulabs_api client (lazy import -- flag-off safety).
    Positional arg order is (ip, access_code, serial), verified against
    v2.6.6. Tests monkeypatch this to inject a stub."""
    import bambulabs_api as bl

    return bl.Printer(conn.host, conn.access_code, conn.serial)


FTP_TIMEOUT_S = 30


def _build_ftp(conn: PrinterConnection):
    """Construct the vendored FTPS client (lazy import -- flag-off safety).
    Tests monkeypatch this to inject a stub."""
    from bambulabs_api.ftp_client import PrinterFTPClient

    client = PrinterFTPClient(conn.host, conn.access_code)
    client.ftps.timeout = FTP_TIMEOUT_S  # ftplib.connect() honours the attribute
    return client


def _state_token(state) -> str | None:
    """Normalize the lib's get_state() (a GcodeState enum or a str) to an
    UPPER-CASE token like 'RUNNING'/'IDLE'/'FINISH'. Confirmed against the
    installed v2.6.6 wheel: GcodeState members are
    IDLE/PREPARE/RUNNING/PAUSE/FINISH/FAILED/UNKNOWN, name == value (already
    upper-case), so this also just passes through a plain str unchanged."""
    raw = getattr(state, "value", None) or getattr(state, "name", None) or state
    text = str(raw or "").rsplit(".", 1)[-1].upper()
    return text or None


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dump_get(dump: dict, key: str):
    """Best-effort lookup of a field in the lib's mqtt_dump(). The real
    v2.6.6 Printer.mqtt_dump() returns the RAW top-level MQTT payload
    (``{"print": {...}, "info": {...}}``) -- fields like "print_error" and
    "wifi_signal" live nested under ``dump["print"][key]`` (confirmed by
    reading PrinterMQTTClient.__get_print in the installed wheel, which is
    exactly how the lib's own print_error_code()/wifi_signal() helpers read
    them). Edge-stub tests exercise a flatter shape (``{"print_error": 0,
    ...}``) directly at the top level, so this checks top-level first, then
    falls back to the nested "print" dict, and degrades to None -- never
    raises -- if neither is present."""
    if key in dump:
        return dump[key]
    nested = dump.get("print")
    if isinstance(nested, dict):
        return nested.get(key)
    return None


def _hex6(color) -> str | None:
    """AMS ``tray_color`` is 8-hex ``RRGGBBAA`` (the lib appends an alpha) --
    strip it to a plain ``#RRGGBB`` the viewer/CSS can use. Tolerant: returns
    None for anything that isn't at least 6 hex digits."""
    if not isinstance(color, str):
        return None
    hexpart = color.strip().lstrip("#")
    if len(hexpart) < 6 or any(ch not in "0123456789abcdefABCDEF" for ch in hexpart[:6]):
        return None
    return f"#{hexpart[:6].upper()}"


def _ams_trays(dump: dict) -> list[dict]:
    """Loaded AMS filament slots from the raw mqtt dump (M8 G3). Path (per the
    installed bambulabs_api): ``dump["print"]["ams"]["ams"][unit]["tray"][slot]``
    with ``tray_color`` (8-hex) + ``tray_type``. Fully tolerant -- a printer
    with no AMS (or an unexpected shape) yields ``[]``, never raises. Slots are
    numbered sequentially across all AMS units. DOCUMENTED-not-live-verified
    (no physical A1+AMS in CI): isolated here so a real capture can correct the
    path/field names in one place."""
    section = dump.get("print") if isinstance(dump.get("print"), dict) else dump
    ams_units = ((section or {}).get("ams") or {}).get("ams")
    if not isinstance(ams_units, list):
        return []
    trays: list[dict] = []
    slot = 0
    for unit in ams_units:
        for tray in (unit.get("tray") or []) if isinstance(unit, dict) else []:
            if not isinstance(tray, dict):
                continue
            color = _hex6(tray.get("tray_color"))
            material = tray.get("tray_type") or None
            if color is not None or material is not None:
                trays.append({"slot": slot, "color": color, "material": material})
            slot += 1
    return trays


@register_adapter
class BambuLanAdapter(PrinterAdapter):
    kind: ClassVar[PrinterKind] = PrinterKind.BAMBU_LAN

    def __init__(self, conn: PrinterConnection) -> None:
        super().__init__(conn)
        self._printer = None
        self._handler: ReportHandler | None = None

    def _client(self):
        if self._printer is None:
            self._printer = _build_printer(self.conn)
        return self._printer

    # -- normalization (the logic WE own; unit-tested) -----------------
    def _snapshot(self) -> dict:
        """Read the lib's current (already-merged) state into our normalized
        snapshot dict. Cheap LOCAL reads -- the lib's MQTT thread keeps its
        state fresh, so there is no printer round-trip per call."""
        p = self._client()
        try:
            dump = p.mqtt_dump() or {}
        except Exception:  # noqa: BLE001 -- mqtt_dump is best-effort for the two extras
            dump = {}
        return {
            "gcode_state": _state_token(p.get_state()),
            "mc_percent": _as_int(p.get_percentage()),
            "layer_num": _as_int(p.current_layer_num()),
            "total_layer_num": _as_int(p.total_layer_num()),
            "mc_remaining_time": _as_int(p.get_time()),
            "print_error": _as_int(_dump_get(dump, "print_error")),
            "nozzle_temper": p.get_nozzle_temperature(),
            "bed_temper": p.get_bed_temperature(),
            "subtask_name": p.subtask_name() or p.get_file_name() or None,
            "wifi_signal": _dump_get(dump, "wifi_signal"),
            "trays": _ams_trays(dump),
        }

    def merge_report(self, prev: dict | None, report: dict) -> dict:
        # The lib delivers a FULL merged snapshot (not a diff), so the latest
        # snapshot IS the merged state -- keep the ABC contract, ignore prev.
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
        )

    def job_state(self, public: PrinterPublicState) -> PrintJobState | None:
        if public.print_error:
            return PrintJobState.FAILED
        return _GCODE_STATE_TO_JOB.get(public.gcode_state or "")

    # -- lifecycle -----------------------------------------------------
    def connect(self) -> None:
        # bl.Printer.connect() == mqtt_start() + camera_start(). We only ever
        # read MQTT-derived state (see _snapshot/test_connection below) and
        # never touch the camera (no get_camera_frame/camera_client_alive
        # call anywhere in this app), so we call mqtt_start() directly and
        # deliberately never start the camera worker thread. This matters
        # because bambulabs_api's PrinterCamera.stop() (invoked from
        # disconnect()/close() below) does an UNBOUNDED Thread.join() on a
        # thread blocked inside a timeout-less
        # socket.create_connection((host, 6000)) -- against an unreachable
        # printer that blocks for the OS TCP retry ceiling (measured
        # ~136s), far past any adapter-level timeout. Never starting that
        # thread means PrinterCamera.stop()'s "if self.__thread is not
        # None" guard is always False, so close() can never hang on it.
        # (paho-mqtt's own loop_stop() join is bounded by its default 5s
        # socket connect timeout, polled every <=1s -- confirmed in
        # site-packages/paho/mqtt/client.py -- so it stays well inside a
        # probe's timeout.)
        self._client().mqtt_start()

    def close(self) -> None:
        if self._printer is not None:
            try:
                self._printer.disconnect()
            finally:
                self._printer = None

    def set_report_handler(self, handler: ReportHandler) -> None:
        self._handler = handler

    def request_full_status(self) -> None:
        # For the lib-backed adapter this is a cheap LOCAL read of the lib's
        # merged state, emitted to printerd's handler (no printer traffic --
        # the lib already ran pushall on connect and keeps state current).
        if self._handler is not None:
            self._handler(self._snapshot())

    # -- commands ------------------------------------------------------
    def pause(self) -> None:
        self._client().pause_print()

    def resume(self) -> None:
        self._client().resume_print()

    def stop(self) -> None:
        self._client().stop_print()

    # -- send ----------------------------------------------------------
    def upload_and_start(self, spec: PrintSpec) -> None:
        p = self._client()
        with spec.source_path.open("rb") as fh:
            p.upload_file(fh, spec.remote_name)  # -> /cache/<remote_name> on the SD
        # The lib builds the exact project_file payload + url internally; we
        # forward only what its start_print accepts (no bed_levelling/timelapse).
        p.start_print(
            spec.remote_name,
            spec.plate,
            use_ams=spec.use_ams,
            ams_mapping=list(spec.ams_mapping),
            flow_calibration=spec.flow_cali,
        )

    # -- files (FTPS) --------------------------------------------------
    # The vendored client's connect_and_run swallows exceptions and returns
    # None, so None means failure. Messages never include the access code.
    def list_dir(self, path: str) -> list[str]:
        res = _build_ftp(self.conn).list_directory(path)
        if res is None:
            raise OSError(f"FTPS list {path} failed")
        names: list[str] = []
        for line in res[1]:
            parts = line.split(maxsplit=8)
            if len(parts) >= 9 and parts[8] not in (".", ".."):
                names.append(parts[8])
        return names

    def download_file(self, remote: str, dest: Path) -> None:
        buf = _build_ftp(self.conn).download_file(remote)
        if buf is None:
            raise OSError(f"FTPS download {remote} failed")
        Path(dest).write_bytes(buf.getvalue())

    # -- probe ---------------------------------------------------------
    def test_connection(self, *, timeout: float = 10.0) -> ProbeResult:
        """Delegates to the staged Developer-Mode probe (Round 8 T1/T2:
        ``app.printers.probe.staged_probe``) -- TCP reachability -> TLS cert
        serial cross-match -> raw MQTT auth + status report -- instead of
        driving the bambulabs_api lib client, whose only failure signal was
        a black-box "connected but no state" covering every distinct
        failure (wrong code, wrong serial, LAN mode off, not a Bambu
        printer) under one unhelpful message.

        Keeps the pre-existing access-code redaction wrapper: a raw-socket/
        paho exception could still echo the plaintext code back in its
        message, and ``detail`` flows verbatim into
        ``POST /api/printers/{id}/test``'s response. ``staged_probe`` opens
        and closes its own sockets and never touches ``self._client()`` (the
        lib client), so ``self._printer`` stays ``None`` here and
        ``self.close()`` remains the safe no-op it always was when nothing
        was opened on ``self``.
        """
        try:
            return probe.staged_probe(self.conn, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 -- a probe never raises to the caller
            detail = f"{type(exc).__name__}: {exc}"
            if self.conn.access_code and self.conn.access_code in detail:
                detail = detail.replace(self.conn.access_code, "***")
            return ProbeResult(ok=False, detail=detail)
        finally:
            self.close()
