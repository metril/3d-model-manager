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

import contextlib
import json
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

from app.models.enums import PrinterKind, PrintJobState
from app.printers import probe
from app.printers.base import (
    CameraSource,
    PrinterAdapter,
    PrinterConnection,
    PrinterPublicState,
    PrintSpec,
    ProbeResult,
    RemoteFile,
    ReportHandler,
    public_state_from,
)
from app.printers.capabilities import capabilities_for, model_key
from app.printers.registry import register_adapter
from app.printers.stages import stage_name
from app.services.drying import ACTIVE_DRY_STATUSES, ams_humidity_percent
from app.services.spool_inventory import EXTERNAL_SLOT, EXTERNAL_UNIT, global_tray

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


def dual_ams_mapping(mapping) -> tuple[list[int], list[dict]]:
    """Dual-nozzle (H2D) mapping pair, as BambuStudio sends it: virtual trays
    (254 = left external, 255 = right external) and unmapped slots become -1 in
    the flat array (raw 254/255 there makes the firmware fail with 0700_8012)
    and are named in ``ams_mapping2`` as ``{ams_id, slot_id}``."""
    flat: list[int] = []
    detail: list[dict] = []
    for raw in mapping:
        t = int(raw) if raw is not None else -1
        if t < 0:
            flat.append(-1)
            detail.append({"ams_id": 255, "slot_id": 255})
        elif t >= 254:
            flat.append(-1)
            detail.append({"ams_id": t, "slot_id": 0})
        elif t >= 128:  # AMS-HT: the global id is the unit id
            flat.append(t)
            detail.append({"ams_id": t, "slot_id": 0})
        else:
            flat.append(t)
            detail.append({"ams_id": t // 4, "slot_id": t % 4})
    return flat, detail


def project_file_payload(spec: PrintSpec, dual: bool = False) -> dict:
    """Mirrors the vendored ``start_print_3mf`` payload (bambulabs_api
    v2.6.6, ``PrinterMQTTClient.start_print_3mf``) key-for-key, except
    ``bed_leveling``/``timelapse`` come from ``spec`` instead of the lib's
    hardcoded ``True``/missing value."""
    flat, detail = dual_ams_mapping(spec.ams_mapping) if dual else (list(spec.ams_mapping), None)
    payload = {
        "print": {
            "command": "project_file",
            "param": f"Metadata/plate_{spec.plate}.gcode",
            "file": spec.remote_name,
            "url": f"ftp:///{spec.remote_name}",
            "bed_leveling": spec.bed_levelling,
            "bed_type": "textured_plate",
            "flow_cali": spec.flow_cali,
            "vibration_cali": True,
            "layer_inspect": False,
            "timelapse": spec.timelapse,
            "use_ams": spec.use_ams,
            "ams_mapping": flat,
            "sequence_id": "10000000",
            "skip_objects": None,
        }
    }
    if detail is not None:
        payload["print"]["ams_mapping2"] = detail
    return payload


def select_extruder_payload(extruder: int) -> dict:
    """0 = right, 1 = left (captured from OrcaSlicer by Bambuddy)."""
    return {"print": {"command": "select_extruder", "extruder_index": extruder, "sequence_id": "0"}}


def nozzle_temp_gcode(extruder: int, value: int) -> str:
    return f"M104 T{extruder} S{value}"


def _decode_ext_temp(raw) -> tuple[float | None, float | None]:
    """``device.extruder.info[].temp``: above 500 it is ``target * 65536 +
    current`` (heater on), else the plain current temperature."""
    v = _as_int(raw)
    if v is None:
        return None, None
    if v > 500:
        cur, tgt = v % 65536, v // 65536
        return (float(cur) if -50 < cur < 500 else None, float(tgt) if 0 < tgt < 500 else 0.0)
    return (float(v), 0.0) if -50 < v < 500 else (None, None)


def _device_extruder(dump: dict) -> dict:
    device = _dump_get(dump, "device")
    ext = device.get("extruder") if isinstance(device, dict) else None
    return ext if isinstance(ext, dict) else {}


def _nozzle_hw(dump: dict) -> dict[int, dict]:
    """Per-extruder ``{type, diameter}`` (id 0 = right, 1 = left) from
    ``device.nozzle.info`` with the flat ``left_/right_nozzle_*`` fallbacks."""
    hw: dict[int, dict] = {0: {}, 1: {}}
    device = _dump_get(dump, "device")
    nozzle = device.get("nozzle") if isinstance(device, dict) else None
    info = nozzle.get("info") if isinstance(nozzle, dict) else None
    for n in info if isinstance(info, list) else []:
        i = _as_int(n.get("id")) if isinstance(n, dict) else None
        if i in (0, 1):
            hw[i] = {"type": n.get("type") or None, "diameter": _as_float(n.get("diameter"))}
    for side, i in (("right", 0), ("left", 1)):
        if not hw[i].get("diameter"):
            d = _as_float(_dump_get(dump, f"{side}_nozzle_diameter"))
            if d:
                hw[i]["diameter"] = d
        if not hw[i].get("type"):
            t = _dump_get(dump, f"{side}_nozzle_type")
            if t:
                hw[i]["type"] = str(t)
    return hw


def _extruders(dump: dict) -> list[dict]:
    """Per-nozzle state of a dual-nozzle printer (empty unless the report
    carries >= 2 ``device.extruder.info`` entries). id 0 = right, 1 = left."""
    info = _device_extruder(dump).get("info")
    if not isinstance(info, list) or len(info) < 2:
        return []
    hw = _nozzle_hw(dump)
    out: dict[int, dict] = {}
    for e in info:
        i = _as_int(e.get("id")) if isinstance(e, dict) else None
        if i not in (0, 1):
            continue
        cur, tgt = _decode_ext_temp(e.get("temp"))
        out[i] = {
            "id": i,
            "side": "right" if i == 0 else "left",
            "temp": cur,
            "target": tgt,
            "heating": bool(tgt and cur is not None and cur < tgt),
            "diameter": hw[i].get("diameter"),
            "type": hw[i].get("type"),
        }
    return [out[i] for i in sorted(out)] if len(out) == 2 else []


def _active_extruder(dump: dict) -> int | None:
    """``device.extruder.state`` bit 8: 0 = right, 1 = left."""
    state = _as_int(_device_extruder(dump).get("state"))
    return None if state is None else (state >> 8) & 1


def _ams_extruder_map(dump: dict) -> dict[str, int]:
    """AMS unit -> feeding extruder from the hex ``info`` bits 8-11
    (0 = right, 1 = left; 0xE = routed via a filament switch, omitted)."""
    section = _dump_get(dump, "ams")
    units = section.get("ams") if isinstance(section, dict) else None
    out: dict[str, int] = {}
    for u in units if isinstance(units, list) else []:
        if not isinstance(u, dict) or u.get("info") is None:
            continue
        uid = _as_int(u.get("id"))
        try:
            ext = (int(str(u["info"]), 16) >> 8) & 0xF
        except ValueError:
            continue
        if uid is not None and ext in (0, 1):
            out[str(uid)] = ext
    return out


def _publish_raw(p, payload: dict) -> None:
    """Publish ``payload`` on the lib's own command topic, bypassing its
    typed helpers (which hardcode ``bed_leveling=True`` and drop
    ``timelapse``). Mirrors the lib's own ``__publish_command`` connected/
    confirmed checks so a silent drop surfaces as an ``OSError``."""
    client = p.mqtt_client._client
    if not client.is_connected():
        raise OSError("not connected to the MQTT server")
    result = client.publish(p.mqtt_client.command_topic, json.dumps(payload))
    result.wait_for_publish(timeout=5)
    if not result.is_published():
        raise OSError("command publish failed")


_HMS_RESUME = {
    "RESUME_PRINTING",
    "RESUME_PRINTING_DEFECTS",
    "RESUME_PRINTING_PROBELM_SOLVED",
    "PROBLEM_SOLVED_RESUME",
    "FILAMENT_LOAD_RESUME",
    "PROCEED",
    "DBL_CHECK_RESUME",
}
_HMS_IGNORE = {"IGNORE_RESUME", "IGNORE_NO_REMINDER_NEXT_TIME", "DONT_REMIND_NEXT_TIME"}
_HMS_AMS = {
    "FILAMENT_EXTRUDED": "done",
    "DBL_CHECK_DONE": "done",
    "RETRY_FILAMENT_EXTRUDED": "resume",
    "CONTINUE": "resume",
    "RETRY_PROBLEM_SOLVED": "resume",
    "DBL_CHECK_RETRY": "resume",
    "ABORT": "abort",
}
_HMS_SIMPLE = {
    "REFRESH_NOZZLE": {"command": "refresh_nozzle"},
    "TURN_OFF_FIRE_ALARM": {"command": "buzzer_ctrl", "mode": 0},
    "STOP_DRYING": {"command": "auto_stop_ams_dry"},
    "DISABLE_PURIFICATION": {"command": "close_air_filt"},
    "OK_BUTTON": {"command": "clean_print_error"},
    "DBL_CHECK_OK": {"command": "clean_print_error"},
}


def hms_action_payload(print_error: str, action: str, job_id: str | None) -> list[dict] | None:
    """MQTT payloads for an HMS remedy action (BambuStudio's command_hms_*
    shapes; ported from bambuddy). ``None`` = unsupported action; an empty
    list = UI-only action (nothing to send)."""
    try:
        err = str(int(print_error, 16))  # firmware compares the DECIMAL value
    except ValueError:
        err = print_error
    if action in _HMS_RESUME:
        return [{"print": {"command": "resume", "param": "", "sequence_id": "0"}}]
    if action == "STOP_PRINTING":
        return [{"print": {"command": "stop", "param": "", "sequence_id": "0"}}]
    if action in _HMS_IGNORE:
        return [
            {
                "print": {
                    "command": "ignore",
                    "err": err,
                    "param": "reserve",
                    "job_id": job_id or "",
                    "sequence_id": "0",
                }
            }
        ]
    if action == "NO_REMINDER_NEXT_TIME":
        return [{"print": {"command": "idle_ignore", "err": err, "type": 0, "sequence_id": "0"}}]
    if action in _HMS_AMS:
        return [
            {"print": {"command": "ams_control", "param": _HMS_AMS[action], "sequence_id": "0"}}
        ]
    if action in _HMS_SIMPLE:
        out = [{"print": {**_HMS_SIMPLE[action], "sequence_id": "0"}}]
        if action == "DBL_CHECK_OK":
            out.append(
                {
                    "system": {
                        "command": "uiop",
                        "name": "print_error",
                        "action": "close",
                        "source": 1,
                        "type": "dialog",
                        "err": print_error.upper(),
                        "sequence_id": "0",
                    }
                }
            )
        return out
    if action in {
        "CHECK_ASSISTANT", "JUMP_TO_LIVEVIEW", "OK_JUMP_RACK", "REMOVE_CLOSE_BTN",
        "LOAD_VIRTUAL_TRAY", "CANCLE", "DBL_CHECK_CANCEL",
    }:  # fmt: skip
        return []
    return None


# -- T3 print options / AMS slot / SD helpers ---------------------------------
ECHO_HOLD_S = 3.0  # ignore printer echoes of a value we just set (Studio's pattern)
_CFG_SENS = {0: "low", 1: "medium", 2: "high"}
# xcam `cfg` bitmask: [sens(2 bits), enabled(1 bit)] per detector, by start bit.
_CFG_DETECTORS = {
    "spaghetti_detector": 5,
    "pileup_detector": 8,
    "clump_detector": 11,
    "airprint_detector": 14,
}
_XCAM_BOOLS = (
    "first_layer_inspector",
    "printing_monitor",
    "buildplate_marker_detector",
    "allow_skip_parts",
)


def xcam_payload(module: str, enable: bool, sensitivity: str) -> dict:
    """OrcaSlicer ``xcam_control_set`` shape; ``print_halt`` is always true and
    ``halt_print_sensitivity`` is omitted for ``never_halt``."""
    body = {
        "command": "xcam_control_set",
        "sequence_id": "0",
        "module_name": module,
        "control": enable,
        "enable": enable,
        "print_halt": True,
    }
    if sensitivity and sensitivity != "never_halt":
        body["halt_print_sensitivity"] = sensitivity
    return {"xcam": body}


def print_option_payload(option: str, value: bool) -> dict:
    """``print.print_option`` (auto_recovery | auto_switch_filament)."""
    return {"print": {"command": "print_option", "sequence_id": "0", option: value}}


def drying_payload(
    ams_id: int, temp: int, duration_hours: int, filament: str, rotate_tray: bool, *, start: bool
) -> dict:
    """``print.ams_filament_drying`` (upstream ``send_drying_command``);
    ``duration`` is hours on the wire. Stop = mode 0, temp/duration 0."""
    return {
        "print": {
            "sequence_id": "0",
            "command": "ams_filament_drying",
            "ams_id": ams_id,
            "temp": temp if start else 0,
            "cooling_temp": 20 if start else 0,
            "duration": duration_hours if start else 0,
            "humidity": 0,
            "mode": 1 if start else 0,
            "rotate_tray": rotate_tray if start else False,
            "filament": filament if start else "",
            "close_power_conflict": False,
        }
    }


def parse_print_options(dump: dict) -> dict:
    """Decode xcam (top-level or under ``print``) + print_option flags."""
    xcam = dump.get("xcam")
    if not isinstance(xcam, dict):
        nested = dump.get("print")
        xcam = nested.get("xcam") if isinstance(nested, dict) else None
    modules: dict[str, dict] = {}
    out: dict = {"xcam": modules, "print_halt": None, "auto_recovery": None,
                 "auto_switch_filament": None}  # fmt: skip
    if isinstance(xcam, dict):
        cfg = xcam.get("cfg")
        if isinstance(cfg, int) and not isinstance(cfg, bool):
            for name, bit in _CFG_DETECTORS.items():
                modules[name] = {
                    "enable": bool((cfg >> (bit + 2)) & 1),
                    "sensitivity": _CFG_SENS.get((cfg >> bit) & 0x3, "medium"),
                }
        for name in _XCAM_BOOLS:
            if name in xcam:
                modules[name] = {"enable": bool(xcam[name]), "sensitivity": None}
        if "print_halt" in xcam:
            out["print_halt"] = bool(xcam["print_halt"])
        if "auto_recovery_step_loss" in xcam:
            out["auto_recovery"] = bool(xcam["auto_recovery_step_loss"])
    # AMS filament backup lives in bit 18 of the hex `print.cfg` string.
    raw_cfg = _dump_get(dump, "cfg")
    if isinstance(raw_cfg, str) and raw_cfg:
        with contextlib.suppress(ValueError):
            out["auto_switch_filament"] = bool((int(raw_cfg, 16) >> 18) & 1)
    return out


def apply_option_holds(parsed: dict, holds: dict, now: float) -> dict:
    """Overlay still-fresh optimistic values (``holds``) over ``parsed``;
    expired holds are dropped. ``holds`` keys: ``("xcam", module)`` ->
    ``{"enable","sensitivity"}``, ``("opt", name)`` -> bool; values are
    ``(payload, set_at)``."""
    for key in [k for k, (_, at) in holds.items() if now - at > ECHO_HOLD_S]:
        del holds[key]
    for (kind, name), (val, _) in holds.items():
        if kind == "xcam":
            cur = parsed["xcam"].get(name) or {"enable": False, "sensitivity": None}
            parsed["xcam"][name] = {
                "enable": val["enable"],
                "sensitivity": val["sensitivity"] or cur.get("sensitivity"),
            }
        else:
            parsed[name] = val
    return parsed


def slot_wire_ids(ams_id: int, tray_id: int) -> tuple[int, int, int]:
    """``(ams_id, tray_id, slot_id)`` as the printer expects them in
    ``ams_filament_setting``: regular AMS = local tray, AMS-HT = single tray
    (slot 0), external spool = global tray 254 on unit 255."""
    if ams_id == 255:
        return 255, 254, 0
    if ams_id <= 3:
        return ams_id, tray_id, tray_id
    return ams_id, tray_id, 0


def slot_configure_payload(
    ams_id: int,
    tray_id: int,
    tray_info_idx: str,
    tray_type: str,
    sub_brand: str,
    color: str,
    nozzle_min: int,
    nozzle_max: int,
) -> dict:
    a, t, slot = slot_wire_ids(ams_id, tray_id)
    return {
        "print": {
            "command": "ams_filament_setting",
            "ams_id": a,
            "tray_id": t,
            "slot_id": slot,
            "tray_info_idx": tray_info_idx,
            "tray_type": tray_type,
            "tray_sub_brands": sub_brand,
            "tray_color": color,
            "nozzle_temp_min": nozzle_min,
            "nozzle_temp_max": nozzle_max,
            "sequence_id": "0",
        }
    }


def slot_reset_payload(ams_id: int, tray_id: int) -> dict:
    return slot_configure_payload(ams_id, tray_id, "", "", "", "00000000", 0, 0)


# -- T2 K-profiles (pressure advance) -------------------------------------


def _guard_cali_merge(mqtt) -> None:
    """The lib merges EVERY message into its print dict, so an
    ``extrusion_cali_*`` reply would overwrite ``nozzle_diameter`` with the
    size we asked about (bambuddy #2663). Skip the merge for those; they are
    still forwarded to our tap."""
    orig = getattr(mqtt, "manual_update", None)
    if orig is None:
        return

    def guarded(doc):
        pr = doc.get("print") if isinstance(doc, dict) else None
        if isinstance(pr, dict) and str(pr.get("command", "")).startswith("extrusion_cali"):
            return None
        return orig(doc)

    mqtt.manual_update = guarded


def kprofile_get_payload(nozzle_diameter: str) -> dict:
    return {
        "print": {
            "command": "extrusion_cali_get",
            "filament_id": "",
            "nozzle_diameter": nozzle_diameter,
            "sequence_id": "0",
        }
    }


def kprofile_set_payload(entry: dict, dual: bool) -> dict:
    """``extrusion_cali_set`` for one profile. ``tray_id`` is ALWAYS 0: single-
    nozzle firmware answers ``invalid tray_id`` to -1 yet applies the write
    anyway (bambuddy #2718). ``cali_idx`` -1 creates a new slot."""
    nozzle = entry["nozzle_diameter"]
    nozzle_id = entry.get("nozzle_id") or f"{'HH' if dual else 'HS'}00-{nozzle}"
    item = {
        "ams_id": 0,
        "cali_idx": entry.get("cali_idx") if entry.get("cali_idx") is not None else -1,
        "extruder_id": entry.get("extruder_id", 0),
        "filament_id": entry["filament_id"],
        "k_value": f"{float(entry['k_value']):.6f}",
        "n_coef": f"{float(entry.get('n_coef') or 0):.6f}",
        "name": entry["name"],
        "nozzle_diameter": nozzle,
        "nozzle_id": nozzle_id,
        "setting_id": entry.get("setting_id") or "",
        "tray_id": 0,
    }
    return {
        "print": {
            "command": "extrusion_cali_set",
            "filaments": [item],
            "nozzle_diameter": nozzle,
            "sequence_id": "0",
        }
    }


def kprofile_del_payload(entry: dict, dual: bool) -> dict:
    """``extrusion_cali_del``: H2D (dual nozzle) is keyed by extruder/nozzle,
    every other model by ``setting_id``."""
    nozzle = entry["nozzle_diameter"]
    body: dict = {
        "command": "extrusion_cali_del",
        "sequence_id": "0",
        "filament_id": entry["filament_id"],
        "cali_idx": entry["cali_idx"],
        "nozzle_diameter": nozzle,
    }
    if dual:
        body.update(
            extruder_id=entry.get("extruder_id", 0),
            nozzle_id=entry.get("nozzle_id") or f"HH00-{nozzle}",
        )
    else:
        body.update(
            setting_id=entry.get("setting_id") or "",
            nozzle_id=entry.get("nozzle_id") or f"HS00-{nozzle}",
            extruder_id=entry.get("extruder_id", 0),
        )
    return {"print": body}


def kprofile_sel_payload(
    ams_id: int,
    tray_id: int,
    cali_idx: int,
    filament_id: str,
    nozzle_diameter: str,
    dual: bool = False,
) -> dict:
    """``extrusion_cali_sel`` uses the GLOBAL tray id (ams*4+slot), unlike
    ``ams_filament_setting``; never carries ``setting_id`` (mislinks on X1/P1S)."""
    if ams_id == 255:
        # Dual-nozzle: each external spool is its own virtual unit (254 + 0/1).
        ext = 254 + (tray_id if tray_id in (0, 1) else 0) if dual else 254
        wire_ams, wire_tray, slot = ext, ext, 0
    elif ams_id <= 3:
        wire_ams, wire_tray, slot = ams_id, ams_id * 4 + tray_id, tray_id
    else:
        wire_ams, wire_tray, slot = ams_id, tray_id, 0
    return {
        "print": {
            "command": "extrusion_cali_sel",
            "cali_idx": cali_idx,
            "filament_id": filament_id,
            "nozzle_diameter": nozzle_diameter,
            "ams_id": wire_ams,
            "tray_id": wire_tray,
            "slot_id": slot,
            "sequence_id": "0",
        }
    }


def sd_project_file_payload(path: str, plate: int | None, use_ams: bool) -> dict:
    """``project_file`` for a file already on the SD card (``path`` is the
    normalised absolute path under the card root)."""
    name = path.rsplit("/", 1)[-1]
    stem = name[: -len(".gcode.3mf")] if name.lower().endswith(".gcode.3mf") else name[:-4]
    return {
        "print": {
            "command": "project_file",
            "param": f"Metadata/plate_{plate or 1}.gcode",
            "file": name,
            "url": f"file:///sdcard{path}",
            "subtask_name": stem,
            "md5": "",
            "bed_type": "auto",
            "timelapse": False,
            # Calibrations follow BambuStudio's "auto" contract (not forced on).
            "bed_leveling": False,
            "auto_bed_leveling": 2,
            "flow_cali": False,
            "extrude_cali_flag": 2,
            "extrude_cali_manual_mode": 0,
            "nozzle_offset_cali": 0,
            "vibration_cali": True,
            "layer_inspect": False,
            "use_ams": use_ams,
            # No ams_mapping: the printer resolves the file's own mapping.
            "sequence_id": "20000",
        }
    }


_FREE_RE = re.compile(r"(\d+)\s*(?:bytes?\s*)?(?:free|available)", re.I)


def _ftp_run(client, fn):
    """Connect + login + PROT P on ``client.ftps``, run ``fn(ftps)``, always
    close the socket. FTP 550 -> ``FileNotFoundError``; other FTP errors ->
    ``OSError`` (type only: never echoes the access code). Tests monkeypatch
    ``_build_ftp``."""
    import ftplib

    ftps = client.ftps
    try:
        ftps.connect(host=client.server_ip, port=client.port)
        ftps.login(client.user, client.access_code)
        ftps.prot_p()
        return fn(ftps)
    except ftplib.error_perm as e:
        if str(e).startswith("550"):
            raise FileNotFoundError("not found") from None
        raise OSError("FTPS permission error") from None
    except ftplib.Error as e:
        raise OSError(f"FTPS {type(e).__name__}") from None
    finally:
        with contextlib.suppress(Exception):
            ftps.close()


def _ftp_commands(client, cmds: list[str]) -> list[str | None]:
    """Run raw FTP control commands on one FTPS session (``None`` per command
    that errored). Tests monkeypatch this."""
    import ftplib

    def run(ftps) -> list[str | None]:
        out: list[str | None] = []
        for c in cmds:
            try:
                out.append(ftps.sendcmd(c))
            except (OSError, ftplib.Error):
                out.append(None)
        return out

    return _ftp_run(client, run)


def parse_storage(avbl: str | None, stat: str | None) -> dict | None:
    """``AVBL`` -> ``213 <free bytes>``; ``STAT`` is free text (best effort)."""
    if avbl and avbl.startswith("213"):
        parts = avbl.split()
        if len(parts) >= 2 and parts[1].isdigit():
            return {"total": None, "used": None, "free": int(parts[1]), "source": "avbl"}
    if stat:
        m = _FREE_RE.search(stat)
        if m:
            return {"total": None, "used": None, "free": int(m.group(1)), "source": "stat"}
    return None


FTP_TIMEOUT_S = 30
DRY_TARGET_GRACE_S = 120  # status lags the command; keep the target meanwhile


def _build_ftp(conn: PrinterConnection):
    """Construct the vendored FTPS client (lazy import -- flag-off safety).
    Tests monkeypatch this to inject a stub."""
    from bambulabs_api.ftp_client import PrinterFTPClient

    client = PrinterFTPClient(conn.host, conn.access_code)
    client.ftps.timeout = FTP_TIMEOUT_S  # ftplib.connect() honours the attribute
    return client


_MONTHS = {
    name: i + 1
    for i, name in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
}
# Fallback when the LIST columns don't parse: the A1's timelapse/snapshot
# filenames stamp their own capture time, e.g. "ipcam_2026-10-22_14-03-05.mp4".
_NAME_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")


def parse_list_line(line: str, now: datetime) -> RemoteFile | None:
    """Parse one Unix-style FTP ``LIST`` line (same column layout
    ``list_dir`` already relies on: ``perms links user group size month day
    time-or-year name``). ``month day time`` omits the year (e.g. ``Oct 22
    14:03``) -- ``now``'s year is assumed, rolled back one year if that
    would land in the future (an FTP LIST server quirk: once a file is >6
    months old some servers switch to printing the year instead, e.g.
    ``Dec 31  2025``). Falls back to a timestamp embedded in the filename
    itself when the columns don't parse. Returns ``None`` for unparseable
    lines (too few columns, ``.``/``..``)."""
    parts = line.split(maxsplit=8)
    if len(parts) < 9:
        return None
    name = parts[8]
    if name in (".", ".."):
        return None
    try:
        size = int(parts[4])
    except ValueError:
        size = None
    mtime: datetime | None = None
    month = _MONTHS.get(parts[5])
    if month is not None:
        try:
            day = int(parts[6])
        except ValueError:
            day = None
        token = parts[7]
        if day is not None:
            if ":" in token:
                hh, _, mm = token.partition(":")
                try:
                    candidate = datetime(now.year, month, day, int(hh), int(mm), tzinfo=UTC)
                except ValueError:
                    candidate = None
                if candidate is not None and candidate > now + timedelta(days=1):
                    candidate = candidate.replace(year=candidate.year - 1)
                mtime = candidate
            else:
                try:
                    mtime = datetime(int(token), month, day, tzinfo=UTC)
                except ValueError:
                    mtime = None
    if mtime is None:
        m = _NAME_STAMP.search(name)
        if m:
            y, mo, d, h, mi, s = (int(g) for g in m.groups())
            try:
                mtime = datetime(y, mo, d, h, mi, s, tzinfo=UTC)
            except ValueError:
                mtime = None
    return RemoteFile(name=name, size=size, mtime=mtime, is_dir=parts[0].startswith("d"))


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


def _nozzle_mm(dump: dict) -> float | None:
    """The lib's own ``nozzle_diameter()`` defaults to ``0`` (never raises)
    when the printer hasn't reported one -- read the raw dump directly
    (same ``_dump_get`` path as ``print_error``/``wifi_signal``) and treat
    a non-positive value as unknown, never a real 0mm nozzle."""
    value = _dump_get(dump, "nozzle_diameter")
    try:
        mm = float(value)
    except (TypeError, ValueError):
        return None
    return mm if mm > 0 else None


def _fan_pct(value) -> int | None:
    """Bambu fan speed 0-15 (sometimes 0-255) -> percent (same rule as
    bambuddy's ``parse_fan_speed``)."""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    if n <= 15:
        return round(n * 100 / 15)
    if n <= 255:
        return round(n * 100 / 255)
    return None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _light_on(p, dump: dict) -> bool | None:
    """Lib getter first ("on"/"off"), else the raw ``lights_report``."""
    try:
        state = str(p.get_light_state()).strip().lower()
    except Exception:  # noqa: BLE001
        state = ""
    if state in ("on", "off"):
        return state == "on"
    reports = _dump_get(dump, "lights_report")
    if isinstance(reports, list):
        for r in reports:
            if isinstance(r, dict) and r.get("node") == "chamber_light":
                return r.get("mode") == "on"
    return None


def _hms(dump: dict) -> list[dict]:
    out: list[dict] = []
    raw = _dump_get(dump, "hms")
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict):
                attr, code = _as_int(e.get("attr")), _as_int(e.get("code"))
                if attr is not None and code is not None:
                    out.append({"attr": attr, "code": code})
    return out


def _modules(dump: dict) -> list[dict]:
    info = dump.get("info")
    mods = info.get("module") if isinstance(info, dict) else None
    return [m for m in mods if isinstance(m, dict)] if isinstance(mods, list) else []


# AMS / AMS Lite / AMS 2 Pro / AMS HT firmware modules.
_AMS_MODULE = re.compile(r"^(ams|ams_f1|n3f|n3s)/\d+$")


def _firmware(dump: dict) -> dict | None:
    ota: str | None = None
    ams: list[dict] = []
    for m in _modules(dump):
        name = str(m.get("name") or "")
        if name == "ota":
            ota = m.get("sw_ver")
        elif _AMS_MODULE.match(name):
            uid = _as_int(name.split("/", 1)[1])
            if uid is not None:
                ams.append({"id": uid, "sw_ver": m.get("sw_ver")})
    if ota is None and not ams:
        return None
    return {"ota": ota, "ams": ams}


def _ams_units(dump: dict, firmware: dict | None) -> list[dict]:
    section = _dump_get(dump, "ams")
    units = section.get("ams") if isinstance(section, dict) else None
    if not isinstance(units, list):
        return []
    fw = {m["id"]: m.get("sw_ver") for m in (firmware or {}).get("ams", [])}
    out: list[dict] = []
    for pos, u in enumerate(units):
        if not isinstance(u, dict):
            continue
        uid = _as_int(u.get("id"))
        uid = pos if uid is None else uid
        out.append(
            {
                "id": uid,
                # humidity_raw is relative-humidity %; the 1-5 `humidity` is an
                # inverted drop index and is deliberately never used.
                "temp": _as_float(u.get("temp")),
                "serial": u.get("sn") or None,
                "fw": fw.get(uid),
                **_dry_fields(u),
            }
        )
    return out


def _dry_fields(u: dict) -> dict:
    """Drying telemetry for one raw AMS unit: ``info`` is a hex bitmask
    (dry_status = bits 4-7, dry_sub_status = bits 22-25); ``dry_sf_reason``
    lists the codes that currently block a start."""
    status = sub = None
    try:
        info = int(str(u.get("info")), 16)
        status, sub = (info >> 4) & 0xF, (info >> 22) & 0xF
    except (TypeError, ValueError):
        pass
    reasons = u.get("dry_sf_reason")
    return {
        "dry_time_min": _as_int(u.get("dry_time")),
        "dry_status": status,
        "dry_sub_status": sub,
        "dry_sf_reason": [
            int(r)
            for r in (reasons if isinstance(reasons, list) else [])
            if isinstance(r, int)
            and not isinstance(r, bool)
            or (isinstance(r, str) and r.isdigit())
        ],
        "humidity_index": _as_int(u.get("humidity")),
        # humidity (%) comes from humidity_raw only; never from the inverted index
        "humidity": ams_humidity_percent(u),
    }


def _tray_now(dump: dict) -> int | None:
    """AMS report `tray_now` (str or int): 0-253 loaded slot, 254 external, 255 none."""
    section = _dump_get(dump, "ams")
    raw = section.get("tray_now") if isinstance(section, dict) else None
    n = _as_int(raw)
    return n if n is not None and 0 <= n <= 255 else None


_DOOR_BIT = 0x00800000


def _door_open(dump: dict, model: str | None, serial: str | None) -> bool | None:
    """Door state, only for models with a door sensor. X1 reports it in
    `home_flag` bit 23; other families in the hex `stat` field (same bit)."""
    if not capabilities_for(model, serial).door:
        return None
    raw = _dump_get(dump, "home_flag")
    if raw is None or not str(model_key(model, serial) or "").startswith("x1"):
        raw = _dump_get(dump, "stat")
    try:
        value = int(raw, 16) if isinstance(raw, str) else int(raw)
    except (TypeError, ValueError):
        return None
    return bool(value & _DOOR_BIT)


def _ipcam(dump: dict) -> bool | None:
    cam = _dump_get(dump, "ipcam")
    if isinstance(cam, dict) and "ipcam_record" in cam:
        return cam.get("ipcam_record") == "enable"
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


def _all_zero_uuid(uuid: str) -> bool:
    """A reported ``tray_uuid`` of all zeros means "no RFID tag read" --
    never a real spool identity."""
    return set(uuid) <= {"0"}


def _tray_entry(tray: dict, ams_unit: int, tray_num: int) -> dict:
    color = _hex6(tray.get("tray_color"))
    material = tray.get("tray_type") or None
    uuid = tray.get("tray_uuid") or None
    if uuid is not None and _all_zero_uuid(uuid):
        uuid = None
    remain = _as_int(tray.get("remain"))
    remain_pct = remain if remain is not None and remain >= 0 else None
    return {
        "slot": global_tray(ams_unit, tray_num),
        "ams_unit": ams_unit,
        "tray": tray_num,
        "color": color,
        "material": material,
        "tray_type": material,
        "tray_uuid": uuid,
        "remain_pct": remain_pct,
        # T2: the printer's selected K-profile (-1 = default) and filament preset.
        "cali_idx": _as_int(tray.get("cali_idx")),
        "k_value": _as_float(tray.get("k")),
        "tray_info_idx": tray.get("tray_info_idx") or None,
    }


def _ams_trays(dump: dict) -> list[dict]:
    """Every AMS tray (loaded or empty) plus the external spool, from the raw
    mqtt dump (M8 G3 + P5 spool tray plan). Path (per the installed
    bambulabs_api): ``dump["print"]["ams"]["ams"][unit]["tray"][slot]`` with
    ``tray_color`` (8-hex), ``tray_type``, ``tray_uuid`` (RFID) and
    ``remain`` (percent); the external spool is ``dump["print"]["vt_tray"]``.
    Every tray is reported (not just loaded ones) so printerd's RFID
    auto-assign can clear a slot whose tray went empty. ``ams_unit``/``tray``
    come from the unit's/tray's own ``id`` (falling back to its position in
    the list when absent); ``slot`` keeps its old meaning -- the global id
    ``app.services.spool_inventory.global_tray`` computes -- so existing
    chips are unchanged. Fully tolerant -- a printer with no AMS (or an
    unexpected shape) yields ``[]``, never raises. DOCUMENTED-not-live-
    verified (no physical A1+AMS in CI): isolated here so a real capture can
    correct the path/field names in one place."""
    section = dump.get("print") if isinstance(dump.get("print"), dict) else dump
    section = section or {}
    ams_units = (section.get("ams") or {}).get("ams")
    trays: list[dict] = []
    if isinstance(ams_units, list):
        for u_pos, unit in enumerate(ams_units):
            if not isinstance(unit, dict):
                continue
            unit_id = _as_int(unit.get("id"))
            ams_unit = unit_id if unit_id is not None else u_pos
            tray_list = unit.get("tray")
            if not isinstance(tray_list, list):
                continue
            for t_pos, tray in enumerate(tray_list):
                if not isinstance(tray, dict):
                    continue
                tray_id = _as_int(tray.get("id"))
                tray_num = tray_id if tray_id is not None else t_pos
                trays.append(_tray_entry(tray, ams_unit, tray_num))
    vt = section.get("vt_tray")
    if isinstance(vt, dict):
        trays.append(_tray_entry(vt, EXTERNAL_UNIT, EXTERNAL_SLOT))
    return trays


_HOT_MATERIALS = ("PETG", "ABS", "ASA", "PC", "PA", "PET")


def _load_temp(dump: dict, tray: int) -> int:
    """Nozzle temp for a filament load: the tray's reported
    ``nozzle_temp_max`` if known, else 250 for PETG/ABS-class, else 220."""
    section = dump.get("print") if isinstance(dump.get("print"), dict) else dump
    raw: dict | None = None
    vt = section.get("vt_tray")
    if tray == global_tray(EXTERNAL_UNIT, EXTERNAL_SLOT) and isinstance(vt, dict):
        raw = vt
    else:
        units = (section.get("ams") or {}).get("ams")
        for u_pos, unit in enumerate(units if isinstance(units, list) else []):
            if not isinstance(unit, dict):
                continue
            uid = _as_int(unit.get("id"))
            uid = u_pos if uid is None else uid
            trays = unit.get("tray")
            for t_pos, t in enumerate(trays if isinstance(trays, list) else []):
                tid = _as_int(t.get("id")) if isinstance(t, dict) else None
                tid = t_pos if tid is None else tid
                if isinstance(t, dict) and global_tray(uid, tid) == tray:
                    raw = t
    if raw:
        tmax = _as_int(raw.get("nozzle_temp_max"))
        if tmax and 150 <= tmax <= 300:
            return tmax
        kind = str(raw.get("tray_type") or "").upper()
        if kind.startswith(_HOT_MATERIALS):
            return 250
    return 220


@register_adapter
class BambuLanAdapter(PrinterAdapter):
    kind: ClassVar[PrinterKind] = PrinterKind.BAMBU_LAN
    emits_replies: ClassVar[bool] = True

    def __init__(self, conn: PrinterConnection) -> None:
        super().__init__(conn)
        self._printer = None
        self._handler: ReportHandler | None = None
        self._dump_sig: str | None = None
        self._report_at: str | None = None
        self._opt_holds: dict[tuple[str, str], tuple[object, float]] = {}
        # T1: the printer never echoes a drying cycle's target, so cache what
        # we sent per AMS unit; dropped once the unit reports status 0 again.
        self._dry_targets: dict[int, dict] = {}

    def _with_dry_targets(self, units: list[dict]) -> list[dict]:
        now = time.time()
        for u in units:
            tgt = self._dry_targets.get(u["id"])
            status = u.get("dry_status")
            if tgt is None or status is None:
                continue
            if status in ACTIVE_DRY_STATUSES:
                tgt["seen"] = True
            elif status == 0 and (tgt["seen"] or now - tgt["started_at"] > DRY_TARGET_GRACE_S):
                del self._dry_targets[u["id"]]
                continue
            u["dry_target_temp"] = tgt["temp"]
            u["dry_filament"] = tgt["filament"]
            u["dry_started_at"] = tgt["started_at"]
        return units

    def _report_time(self, dump: dict) -> str:
        """ISO time the printer last REPORTED something: bumped only when the
        lib's merged dump changes (printerd re-reads it every few seconds)."""
        sig = json.dumps(dump, sort_keys=True, default=str)
        if sig != self._dump_sig or self._report_at is None:
            self._dump_sig = sig
            self._report_at = datetime.now(UTC).isoformat()
        return self._report_at

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
        firmware = _firmware(dump)
        vt = _dump_get(dump, "vt_tray")
        # Raw dump, not p.nozzle_type(): the lib defaults to "stainless_steel"
        # when unreported and raises on enum-unknown types.
        nozzle_type = _dump_get(dump, "nozzle_type") or None
        sdcard = _dump_get(dump, "sdcard")
        extruders = _extruders(dump)
        left = next((e for e in extruders if e["id"] == 1), None)
        return {
            "extruders": extruders,
            "active_extruder": _active_extruder(dump) if extruders else None,
            "ams_extruder_map": _ams_extruder_map(dump) if extruders else {},
            "nozzle_target": left["target"]
            if left
            else _as_float(_dump_get(dump, "nozzle_target_temper")),
            "bed_target": _as_float(_dump_get(dump, "bed_target_temper")),
            "chamber_temper": _as_float(_dump_get(dump, "chamber_temper")),
            "chamber_target": _as_float(_dump_get(dump, "chamber_target_temper")),
            "fan_part_pct": _fan_pct(_dump_get(dump, "cooling_fan_speed")),
            "fan_aux_pct": _fan_pct(_dump_get(dump, "big_fan1_speed")),
            "fan_chamber_pct": _fan_pct(_dump_get(dump, "big_fan2_speed")),
            "light_on": _light_on(p, dump),
            "speed_level": _as_int(_dump_get(dump, "spd_lvl")),
            "stage": stage_name(_dump_get(dump, "stg_cur")),
            "hms": _hms(dump),
            "sdcard": sdcard if isinstance(sdcard, bool) else None,
            "nozzle_type": nozzle_type,
            "firmware": firmware,
            "ams_units": self._with_dry_targets(_ams_units(dump, firmware)),
            "ext_tray": _tray_entry(vt, EXTERNAL_UNIT, EXTERNAL_SLOT)
            if isinstance(vt, dict)
            else None,
            "ipcam": _ipcam(dump),
            "dev_mode": None,
            # Raw inputs for printers/devmode.py (not PrinterPublicState fields).
            "fun": _dump_get(dump, "fun"),
            "ext_tray_raw": vt if isinstance(vt, dict) else None,
            "last_report_at": self._report_time(dump),
            "gcode_state": _state_token(p.get_state()),
            "mc_percent": _as_int(p.get_percentage()),
            "layer_num": _as_int(p.current_layer_num()),
            "total_layer_num": _as_int(p.total_layer_num()),
            "mc_remaining_time": _as_int(p.get_time()),
            "print_error": _as_int(_dump_get(dump, "print_error")),
            # Dual nozzle: the single-nozzle fields mirror the LEFT tile.
            "nozzle_temper": left["temp"]
            if left and left["temp"] is not None
            else p.get_nozzle_temperature(),
            "bed_temper": p.get_bed_temperature(),
            "subtask_name": p.subtask_name() or p.get_file_name() or None,
            "wifi_signal": _dump_get(dump, "wifi_signal"),
            "trays": _ams_trays(dump),
            "nozzle_mm": _nozzle_mm(dump),
            "tray_now": _tray_now(dump),
            "door_open": _door_open(dump, self.conn.model, self.conn.serial),
            "print_options": apply_option_holds(
                parse_print_options(dump), self._opt_holds, time.monotonic()
            ),
        }

    def merge_report(self, prev: dict | None, report: dict) -> dict:
        # The lib delivers a FULL merged snapshot (not a diff), so the latest
        # snapshot IS the merged state -- keep the ABC contract, ignore prev.
        return dict(report)

    def public_state(self, merged: dict) -> PrinterPublicState:
        return public_state_from(merged)

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
        p = self._client()
        mqtt = getattr(p, "mqtt_client", None)
        if mqtt is not None:
            mqtt.on_message_handler = self._tap_message
            _guard_cali_merge(mqtt)
        p.mqtt_start()

    def _tap_message(self, _mqtt, _client, _userdata, msg) -> None:
        """Forward command replies (a ``print`` dict carrying ``result``) to
        printerd. Runs on the lib's paho thread; must never raise."""
        tap = self._raw_tap
        if tap is not None:
            with contextlib.suppress(Exception):
                tap(str(msg.topic), bytes(msg.payload))
        try:
            doc = json.loads(msg.payload)
            pr = doc.get("print") if isinstance(doc, dict) else None
            if (
                isinstance(pr, dict)
                and ("result" in pr or pr.get("command") == "extrusion_cali_get")
                and self._reply_handler is not None
            ):
                self._reply_handler(pr)
        except Exception:  # noqa: BLE001
            pass

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

    # -- Q2 controls ---------------------------------------------------
    @staticmethod
    def _sent(ok) -> None:
        # The lib returns False when its publish was refused (not connected).
        if ok is False:
            raise OSError("command not sent (printer not connected?)")

    def set_temperature(self, target: str, value: int, extruder: int | None = None) -> None:
        p = self._client()
        if target == "nozzle" and extruder is not None:
            self._sent(p.gcode(nozzle_temp_gcode(extruder, value), gcode_check=False))
        elif target == "nozzle":
            # override=True: the lib otherwise silently drops targets < 40 C,
            # which is exactly how a user turns heating off.
            self._sent(p.mqtt_client.set_nozzle_temperature(value, override=True))
        elif target == "bed":
            self._sent(p.mqtt_client.set_bed_temperature(value, override=True))
        elif target == "chamber":
            self._sent(p.gcode(f"M141 S{value}", gcode_check=False))
        else:
            raise ValueError(target)

    def select_extruder(self, extruder: int) -> None:
        _publish_raw(self._client(), select_extruder_payload(extruder))

    def set_fan(self, fan: str, pct: int) -> None:
        p = self._client()
        raw = round(pct * 255 / 100)  # lib int speeds are 0-255
        setter = {
            "part": p.set_part_fan_speed,
            "aux": p.set_aux_fan_speed,
            "chamber": p.set_chamber_fan_speed,
        }[fan]
        self._sent(setter(raw))

    def set_light(self, on: bool) -> None:
        p = self._client()
        self._sent(p.turn_light_on() if on else p.turn_light_off())

    def set_speed(self, level: int) -> None:
        self._sent(self._client().set_print_speed(level))

    def home(self) -> None:
        self._sent(self._client().home_printer())

    def jog(self, axis: str, mm: float) -> None:
        # The lib's only Z helper (move_z_axis) is ABSOLUTE; a relative jog
        # needs G91/G0/G90 for every axis (deviation noted in the Q2 plan).
        feed = 600 if axis == "Z" else 3000
        self._sent(
            self._client().gcode(["G91", f"G0 {axis}{mm:g} F{feed}", "G90"], gcode_check=False)
        )

    def extrude(self, mm: float) -> None:
        self._sent(self._client().gcode(["M83", f"G1 E{mm:g} F300"], gcode_check=False))

    def send_gcode(self, gcode: str) -> None:
        self._sent(self._client().gcode(gcode.splitlines() or [gcode]))

    def skip_objects(self, object_ids: list[int]) -> None:
        self._sent(self._client().skip_objects(object_ids))

    def load_filament(self, tray: int) -> None:
        try:
            dump = self._client().mqtt_dump() or {}
        except Exception:  # noqa: BLE001
            dump = {}
        temp = _load_temp(dump, tray)
        _publish_raw(
            self._client(),
            {
                "print": {
                    "command": "ams_change_filament",
                    "target": tray,
                    "curr_temp": temp,
                    "tar_temp": temp,
                    "sequence_id": "0",
                }
            },
        )

    def unload_filament(self) -> None:
        self._sent(self._client().unload_filament_spool())

    def calibrate(self, bed_leveling: bool, vibration: bool, motor_noise: bool) -> None:
        self._sent(self._client().calibrate_printer(bed_leveling, motor_noise, vibration))

    def set_airduct(self, mode: str) -> None:
        _publish_raw(
            self._client(),
            {
                "print": {
                    "command": "set_airduct",
                    "modeId": 0 if mode == "cooling" else 1,
                    "sequence_id": "0",
                    "submode": -1,
                }
            },
        )

    # -- Q5 HMS + firmware ---------------------------------------------
    def clear_hms(self) -> None:
        _publish_raw(
            self._client(), {"print": {"command": "clean_print_error", "sequence_id": "0"}}
        )

    def send_devmode_probe(self, slot: dict, seq: str) -> None:
        """Echo the external slot's exact current values (a no-op when
        accepted); ported from the upstream probe."""
        payload = {
            "command": "ams_filament_setting",
            "ams_id": 255,
            "tray_id": 0,
            "slot_id": 0,
            "tray_info_idx": slot["tray_info_idx"],
            "tray_type": slot["tray_type"],
            "tray_sub_brands": slot.get("tray_sub_brands", ""),
            "tray_color": slot["tray_color"],
            "nozzle_temp_min": slot["nozzle_temp_min"],
            "nozzle_temp_max": slot["nozzle_temp_max"],
            "sequence_id": seq,
        }
        if slot.get("setting_id"):
            payload["setting_id"] = slot["setting_id"]
        _publish_raw(self._client(), {"print": payload})

    def execute_hms_action(self, print_error: str, action: str, job_id: str | None = None) -> None:
        p = self._client()
        payload = hms_action_payload(print_error, action, job_id)
        if payload is None:
            raise ValueError(f"unsupported HMS action {action!r}")
        for item in payload:
            _publish_raw(p, item)
        _publish_raw(p, {"pushing": {"command": "pushall", "sequence_id": "0"}})

    def upload_file(self, remote: str, local: Path) -> None:
        with local.open("rb") as fh:
            res = _build_ftp(self.conn).upload_file(fh, remote)
        if res is None:
            raise OSError(f"FTPS upload {remote} failed")

    # -- send ----------------------------------------------------------
    def upload_and_start(self, spec: PrintSpec) -> None:
        p = self._client()
        with spec.source_path.open("rb") as fh:
            p.upload_file(fh, spec.remote_name)  # -> /cache/<remote_name> on the SD
        # The lib's start_print/start_print_3mf hardcodes bed_leveling=True
        # and drops timelapse entirely, so we publish the raw project_file
        # command ourselves (project_file_payload) to carry both from spec.
        _publish_raw(p, project_file_payload(spec, self._dual()))

    # -- T3 print options / slots / SD ---------------------------------
    def set_xcam(self, module: str, enable: bool, sensitivity: str) -> None:
        _publish_raw(self._client(), xcam_payload(module, enable, sensitivity))
        sens = None if sensitivity == "never_halt" else sensitivity
        self._opt_holds[("xcam", module)] = (
            {"enable": enable, "sensitivity": sens},
            time.monotonic(),
        )

    def set_print_option(self, option: str, value: bool) -> None:
        _publish_raw(self._client(), print_option_payload(option, value))
        self._opt_holds[("opt", option)] = (value, time.monotonic())

    def ams_slot_configure(
        self,
        ams_id: int,
        tray_id: int,
        tray_info_idx: str,
        tray_type: str,
        sub_brand: str,
        color: str,
        nozzle_min: int,
        nozzle_max: int,
    ) -> None:
        _publish_raw(
            self._client(),
            slot_configure_payload(
                ams_id, tray_id, tray_info_idx, tray_type, sub_brand, color, nozzle_min, nozzle_max
            ),
        )

    def ams_slot_reset(self, ams_id: int, tray_id: int) -> None:
        _publish_raw(self._client(), slot_reset_payload(ams_id, tray_id))

    def drying_start(
        self, ams_id: int, temp: int, duration_hours: int, filament: str, rotate_tray: bool
    ) -> None:
        _publish_raw(
            self._client(),
            drying_payload(ams_id, temp, duration_hours, filament, rotate_tray, start=True),
        )
        self._dry_targets[ams_id] = {
            "temp": temp,
            "filament": filament,
            "started_at": time.time(),
            "seen": False,
        }

    def drying_stop(self, ams_id: int) -> None:
        _publish_raw(self._client(), drying_payload(ams_id, 0, 0, "", False, start=False))
        self._dry_targets.pop(ams_id, None)

    def start_sd_print(self, path: str, plate: int | None, use_ams: bool) -> None:
        _publish_raw(self._client(), sd_project_file_payload(path, plate, use_ams))

    # -- T2 K-profiles ---------------------------------------------------
    def _dual(self) -> bool:
        from app.printers.capabilities import capabilities_for

        return capabilities_for(self.conn.model, self.conn.serial).dual_nozzle

    def kprofile_list(self, nozzle_diameter: str, extruder_id: int | None = None) -> None:
        _publish_raw(self._client(), kprofile_get_payload(nozzle_diameter))

    def kprofile_set(self, entry: dict) -> None:
        _publish_raw(self._client(), kprofile_set_payload(entry, self._dual()))

    def kprofile_delete(self, entry: dict) -> None:
        _publish_raw(self._client(), kprofile_del_payload(entry, self._dual()))

    def kprofile_select(
        self, ams_id: int, tray_id: int, cali_idx: int, filament_id: str, nozzle_diameter: str
    ) -> None:
        _publish_raw(
            self._client(),
            kprofile_sel_payload(
                ams_id, tray_id, cali_idx, filament_id, nozzle_diameter, self._dual()
            ),
        )

    def delete_file(self, path: str) -> None:
        _ftp_run(_build_ftp(self.conn), lambda ftps: ftps.delete(path))

    def storage_info(self) -> dict | None:
        res = _ftp_commands(_build_ftp(self.conn), ["AVBL", "STAT"])
        return parse_storage(res[0], res[1] if len(res) > 1 else None)

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
        with Path(dest).open("wb") as fh:
            _ftp_run(
                _build_ftp(self.conn),
                lambda ftps: ftps.retrbinary(f"RETR {remote}", fh.write, blocksize=524288),
            )

    def list_files(self, path: str) -> list[RemoteFile]:
        res = _build_ftp(self.conn).list_directory(path)
        if res is None:
            raise OSError(f"FTPS list {path} failed")
        now = datetime.now(UTC)
        files: list[RemoteFile] = []
        for line in res[1]:
            rf = parse_list_line(line, now)
            if rf is not None:
                files.append(rf)
        return files

    # -- camera (P4 camera plan) ---------------------------------------
    def open_camera(self, stop: threading.Event) -> CameraSource:
        """a1 | rtsp | external | none by capabilities/options (Q4). Lazy
        imports (flag-off import safety: only printerd's camera supervisor
        ever calls this)."""
        from app.printers.camera_select import open_camera_for

        return open_camera_for(self.conn, stop)

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
