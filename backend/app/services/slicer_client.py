"""Client for the slicer sidecar (P6 "slicer, profiles, recipes and
dispatch" plan, Task 2) -- pinned `ghcr.io/maziggy/bambu-studio-api` image
(`compose.slicer.yaml`, profile `slicer`).

The image's real HTTP surface is UNVERIFIED (no live instance was available
while writing this) -- every image-specific detail (paths, response
shapes) is confined to `_ENDPOINTS`, `_build_slice_request`, and
`_parse_health` below. Swapping to our own FastAPI-over-CLI wrapper later
only edits those three; the `SlicerClient` public contract
(`health`/`profiles`/`slice`) stays put.

Profile JSON (printer/process/filament) is sent as FILES on each `slice`
call, never as names the sidecar would have to look up -- it never touches
our DB, and a profile edited here takes effect on the very next slice with
no sync step.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import httpx

from app.services.app_config import AppConfig

# The ONLY place that knows this image's actual paths. Unverified against a
# live instance -- update here (and nowhere else) once confirmed.
_ENDPOINTS = {
    "health": "/health",
    "slice": "/slice",
    "profiles": "/profiles",
}

_DEFAULT_TIMEOUT = 120.0


class SlicerError(Exception):
    """The sidecar responded, but with an error (non-2xx, or a body that
    isn't the gcode.3mf archive it was supposed to be)."""


class SlicerUnavailable(SlicerError):
    """Could not reach the sidecar at all (connection refused/timeout)."""


@dataclass(frozen=True)
class SliceOptions:
    auto_orient: bool = False
    arrange: bool = False
    plate: int | None = None


def _parse_health(data: dict) -> dict:
    """Image-specific: pulls a version string out of whatever shape the
    health payload turns out to have, tolerating a couple of plausible key
    names since this is unverified."""
    version = data.get("version") or data.get("app_version") or ""
    status = data.get("status") or data.get("state")
    ok = bool(data.get("ok", True)) and (status in (None, "ok", "healthy", "up"))
    return {"version": str(version), "ok": ok}


def _build_slice_request(
    *,
    model: bytes,
    filename: str,
    printer_profile: dict,
    process_profile: dict,
    filament_profiles: list[dict],
    bed_type: str,
    options: SliceOptions,
) -> dict:
    """Image-specific: the multipart shape the `/slice` endpoint expects.
    Every profile travels as a JSON file part -- never a name/id -- so the
    sidecar never needs to query our database."""
    import json

    files = [
        ("model", (filename, model, "application/octet-stream")),
        (
            "printer_profile",
            ("printer_profile.json", json.dumps(printer_profile).encode(), "application/json"),
        ),
        (
            "process_profile",
            ("process_profile.json", json.dumps(process_profile).encode(), "application/json"),
        ),
        (
            "filament_profiles",
            (
                "filament_profiles.json",
                json.dumps(filament_profiles).encode(),
                "application/json",
            ),
        ),
    ]
    data = {
        "bed_type": bed_type,
        "options": json.dumps(
            {
                "auto_orient": options.auto_orient,
                "arrange": options.arrange,
                "plate": options.plate,
            }
        ),
    }
    return {"files": files, "data": data}


class SlicerClient:
    """Thin HTTP client for the slicer sidecar. See module docstring for
    which parts are image-specific vs. the stable public contract."""

    def __init__(self, base_url: str, *, http: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise SlicerUnavailable(f"could not reach slicer at {path!r}: {exc}") from exc
        if response.status_code >= 400:
            raise SlicerError(
                f"slicer returned {response.status_code} for {path!r}: {response.text[:200]}"
            )
        return response

    def health(self) -> dict:
        response = self._request("GET", _ENDPOINTS["health"])
        try:
            data = response.json()
        except ValueError as exc:
            raise SlicerError(f"slicer health returned non-JSON body: {exc}") from exc
        return _parse_health(data)

    def profiles(self) -> list[dict] | None:
        """The image's own bundled presets, if it exposes them. `None` when
        the endpoint doesn't exist/isn't supported -- callers fall back to
        our own imported profiles (`app.services.slicer_profiles`)."""
        try:
            response = self._request("GET", _ENDPOINTS["profiles"])
        except SlicerError:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        if isinstance(data, list):
            return data
        return None

    def slice(
        self,
        model: bytes | Path,
        filename: str,
        printer_profile: dict,
        process_profile: dict,
        filament_profiles: list[dict],
        bed_type: str,
        options: SliceOptions,
    ) -> bytes:
        model_bytes = Path(model).read_bytes() if isinstance(model, Path) else model
        request_kwargs = _build_slice_request(
            model=model_bytes,
            filename=filename,
            printer_profile=printer_profile,
            process_profile=process_profile,
            filament_profiles=filament_profiles,
            bed_type=bed_type,
            options=options,
        )
        response = self._request("POST", _ENDPOINTS["slice"], **request_kwargs)
        content = response.content
        if not zipfile.is_zipfile(BytesIO(content)):
            raise SlicerError("slicer response body is not a valid .gcode.3mf archive")
        return content


def client_from_config(cfg: AppConfig) -> SlicerClient | None:
    """`None` when no slicer sidecar is configured (`slicer_url` unset) --
    callers treat that as "slicer integration off", mirroring
    `printer_enabled`'s off-by-default posture."""
    if not cfg.slicer_url:
        return None
    return SlicerClient(cfg.slicer_url)
