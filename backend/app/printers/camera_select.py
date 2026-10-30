"""Camera source selection + ``options.camera`` / ``camera_rotation`` handling (Q4)."""

from __future__ import annotations

import threading

from app.config import Settings
from app.crypto import decrypt_secret, encrypt_secret
from app.printers.base import CameraSource, PrinterConnection
from app.printers.camera_external import KINDS, validate_url
from app.printers.capabilities import capabilities_for

ROTATIONS = (0, 90, 180, 270)
DEFAULT_FPS = 5


def camera_kind(model: str | None, serial: str | None, options: dict | None) -> str:
    """``external`` | ``a1`` | ``rtsp`` | ``none`` -- external wins when set."""
    cam = (options or {}).get("camera")
    if isinstance(cam, dict) and cam.get("kind") and (cam.get("url") or cam.get("url_enc")):
        return "external"
    return capabilities_for(model, serial).camera


def rotation_of(options: dict | None) -> int:
    r = (options or {}).get("camera_rotation", 0)
    return r if r in ROTATIONS else 0


def open_camera_for(conn: PrinterConnection, stop: threading.Event) -> CameraSource:
    """``conn.options['camera']['url']`` is the DECRYPTED url (filled by
    ``connection_from_printer``)."""
    kind = camera_kind(conn.model, conn.serial, conn.options)
    if kind == "external":
        from app.printers.camera_external import ExternalCameraSource

        cam = conn.options["camera"]
        return ExternalCameraSource(cam["kind"], cam["url"], stop)
    if kind == "a1":
        from app.printers.camera_a1 import CameraA1Client

        return CameraA1Client(conn.host, conn.access_code, stop=stop)
    if kind == "rtsp":
        from app.printers.camera_rtsp import open_bambu_rtsp

        return open_bambu_rtsp(conn.host, conn.access_code, stop)
    raise NotImplementedError("printer has no camera")


def normalize_options(settings: Settings, new: dict, existing: dict | None) -> dict:
    """Validate camera options and encrypt the external URL at rest. A
    ``camera`` without ``url`` keeps the stored ``url_enc`` (the API never
    returns it, so edits round-trip without re-entering the URL)."""
    out = dict(new)
    rot = out.get("camera_rotation")
    if rot is not None and rot not in ROTATIONS:
        raise ValueError("camera_rotation must be 0, 90, 180 or 270")
    if "layer_timelapse" in out and not isinstance(out["layer_timelapse"], bool):
        raise ValueError("layer_timelapse must be a boolean")
    from app.services.plate_detection import validate_options

    validate_options(out)
    cam = out.get("camera")
    if cam is None:
        out.pop("camera", None)
        return out
    if not isinstance(cam, dict) or cam.get("kind") not in KINDS:
        raise ValueError("camera.kind must be mjpeg, rtsp or snapshot")
    url = cam.get("url")
    if url:
        validate_url(cam["kind"], url)
        enc = encrypt_secret(settings, url)
    else:
        enc = cam.get("url_enc") or ((existing or {}).get("camera") or {}).get("url_enc")
        if not enc:
            raise ValueError("camera.url is required")
    out["camera"] = {"kind": cam["kind"], "url_enc": enc}
    return out


def redact_options(options: dict | None) -> dict:
    out = dict(options or {})
    cam = out.get("camera")
    if isinstance(cam, dict):
        out["camera"] = {"kind": cam.get("kind"), "has_url": bool(cam.get("url_enc"))}
    return out


def decrypt_options(settings: Settings, options: dict | None) -> dict:
    out = dict(options or {})
    cam = out.get("camera")
    if isinstance(cam, dict) and cam.get("url_enc"):
        out["camera"] = {"kind": cam["kind"], "url": decrypt_secret(settings, cam["url_enc"])}
    return out
