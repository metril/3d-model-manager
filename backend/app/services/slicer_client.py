"""Client for our slicer sidecar (`slicer/`, compose profile `slicer`): a
FastAPI wrapper around the Bambu Studio CLI. Endpoints: `GET /health`
(`{status, version, busy, max_concurrency, running, job_timeout_s,
mem_limit, bambu_version}`), `DELETE /jobs/{id}` (kill a running slice; the
job id is the `X-Job-Id` request header on `POST /slice`), `GET /profiles[?flatten=1]`
(bundled Bambu presets) and `POST /slice` (multipart, built by
`_build_slice_request`; responds with the `.gcode.3mf`). Errors are JSON
`{error, message, exit_code, log_tail}`; 429 means the sidecar is busy.

Profile JSON (printer/process/filament) is sent as FILES on each `slice`
call, never as names the sidecar would have to look up -- it never touches
our DB, and a profile edited here takes effect on the very next slice with
no sync step.
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:  # not imported at runtime: slicer/tests load this file standalone
    from app.services.server_config import ServerConfig


logger = logging.getLogger(__name__)

# The sidecar's paths.
_ENDPOINTS = {
    "health": "/health",
    "slice": "/slice",
    "profiles": "/profiles",
}

_DEFAULT_TIMEOUT = 120.0
_DEFAULT_SLICE_TIMEOUT_S = 600.0
# The client waits this much longer than the sidecar's job timeout so the
# sidecar's own 504 (with its log tail) lands before the client gives up.
_SLICE_TIMEOUT_GRACE_S = 60.0
# M5: a well-behaved sidecar's gcode.3mf is a few hundred MB at most -- this
# stops a misbehaving/compromised one from handing back something that
# blows up worker memory/disk.
_MAX_SLICE_RESPONSE_BYTES = 100 * 1024 * 1024


class SlicerError(Exception):
    """The sidecar responded, but with an error (non-2xx, or a body that
    isn't the gcode.3mf archive it was supposed to be). `status` is the HTTP
    status when there was one."""

    def __init__(
        self, message: str = "", status: int | None = None, log_tail: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        # The sidecar's CLI output tail (bounded), persisted for the UI's
        # "View log" -- never part of `str(exc)`.
        self.log_tail = log_tail


class SlicerCancelled(SlicerError):
    """The sidecar killed the job on request (`DELETE /jobs/{id}`)."""


class SlicerUnavailable(SlicerError):
    """Could not reach the sidecar at all (connection refused/timeout)."""


class SlicerBusy(SlicerUnavailable):
    """The sidecar answered 429 (all slice slots taken). Like an unreachable
    sidecar it doesn't consume a retry attempt -- the task just waits."""


class SlicerProtocolError(SlicerError):
    """The sidecar responded 2xx, but with a body shape this client can't
    make sense of at all (M4: e.g. `/health`'s body isn't even a JSON
    object) -- distinct from a well-formed-but-unhealthy report, which
    stays a normal `ok=False` result rather than an error."""


@dataclass(frozen=True)
class SliceOptions:
    auto_orient: bool = False
    arrange: bool = False
    plate: int | None = None


def _parse_health(data: Any) -> dict:
    """Strict: the body must be `{status: str, version: str, ...}`; anything
    else is a `SlicerProtocolError`. `ok` is `status == "ok"`."""
    if not isinstance(data, dict):
        raise SlicerProtocolError(f"slicer health returned a non-object body: {data!r}")
    status = data.get("status")
    version = data.get("version")
    if not isinstance(status, str) or not isinstance(version, str):
        raise SlicerProtocolError("slicer health is missing a string status/version")
    return {
        "version": version,
        "ok": status == "ok",
        "busy": bool(data.get("busy", False)),
        "max_concurrency": data.get("max_concurrency"),
        "running": data.get("running"),
        "job_timeout_s": data.get("job_timeout_s"),
        "mem_limit": data.get("mem_limit"),
        "bambu_version": data.get("bambu_version"),
    }


def _error_detail(response: httpx.Response) -> str:
    """`error: message` from the sidecar's own JSON error body, truncated.
    Non-JSON bodies (a proxy's HTML, a stack trace) are never echoed."""
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    parts = [str(data[k]) for k in ("error", "message") if isinstance(data.get(k), str)]
    return ": ".join(parts)[:300]


_MAX_LOG_TAIL = 8192


def _error_log_tail(response: httpx.Response) -> str | None:
    try:
        data = response.json()
    except ValueError:
        return None
    tail = data.get("log_tail") if isinstance(data, dict) else None
    return tail[-_MAX_LOG_TAIL:] if isinstance(tail, str) and tail else None


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

    def __init__(
        self,
        base_url: str,
        *,
        http: httpx.Client | None = None,
        slice_timeout_s: float = _DEFAULT_SLICE_TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT)
        self._slice_timeout_s = slice_timeout_s

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Could not reach the sidecar at all -- doesn't consume a
            # retry attempt (`app.tasks.slicing`'s module docstring).
            raise SlicerUnavailable(f"could not reach slicer at {path!r}: {exc}") from exc
        except httpx.TransportError as exc:
            # Reached it, but the exchange itself failed (read timeout,
            # protocol error, etc.) -- a real slicer-side failure that DOES
            # consume an attempt, unlike a plain connection failure.
            raise SlicerError(f"slicer request to {path!r} failed: {exc}") from exc
        if response.status_code >= 400:
            # M5: the sidecar's error body can be arbitrarily large or
            # contain anything -- log it (server-side only) and never put
            # it in the exception message, which can surface all the way
            # out to a queue entry's `last_error`/an API response.
            logger.warning(
                "slicer returned %s for %r: %s", response.status_code, path, response.text[:2000]
            )
            if response.status_code == 429:
                raise SlicerBusy(f"slicer is busy ({path!r})", 429)
            if response.status_code == 499:
                raise SlicerCancelled("slice cancelled", 499, _error_log_tail(response))
            detail = _error_detail(response)
            suffix = f": {detail}" if detail else ""
            raise SlicerError(
                f"slicer returned {response.status_code} for {path!r}{suffix}",
                response.status_code,
                _error_log_tail(response),
            )
        return response

    def health(self) -> dict:
        response = self._request("GET", _ENDPOINTS["health"])
        try:
            data = response.json()
        except ValueError as exc:
            raise SlicerError(f"slicer health returned non-JSON body: {exc}") from exc
        return _parse_health(data)

    def cancel(self, job_id: str, *, timeout: float = 5.0) -> bool:
        """Ask the sidecar to kill job `job_id`. False when it isn't running
        (already finished / unknown / sidecar predates the endpoint). Short
        timeout: a cancel must never hold an API request for the default 120s."""
        try:
            self._request("DELETE", f"/jobs/{job_id}", timeout=timeout)
        except SlicerError as exc:
            if exc.status == 404:
                return False
            raise
        return True

    def profiles(self, *, flatten: bool = False) -> list[dict] | None:
        """The sidecar's bundled Bambu presets (`flatten=True` includes each
        full `data` body). `None` only when the sidecar has no such endpoint
        (404) or answers 2xx with something that isn't a list; an unreachable
        / busy / erroring sidecar raises `SlicerError` subclasses."""
        params = {"flatten": "1"} if flatten else None
        try:
            response = self._request("GET", _ENDPOINTS["profiles"], params=params)
        except SlicerError as exc:
            if exc.status == 404:
                return None
            raise
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
        job_id: str | None = None,
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
        response = self._request(
            "POST",
            _ENDPOINTS["slice"],
            timeout=self._slice_timeout_s + _SLICE_TIMEOUT_GRACE_S,
            headers={"X-Job-Id": job_id} if job_id else None,
            **request_kwargs,
        )
        content = response.content
        if len(content) > _MAX_SLICE_RESPONSE_BYTES:
            raise SlicerError(
                f"slicer response exceeds the {_MAX_SLICE_RESPONSE_BYTES} byte limit "
                f"({len(content)} bytes)"
            )
        if not zipfile.is_zipfile(BytesIO(content)):
            raise SlicerError("slicer response body is not a valid .gcode.3mf archive")
        return content


def client_from_config(cfg: ServerConfig) -> SlicerClient | None:
    """`None` when the slicer is disabled or has no URL -- callers treat
    that as "slicer integration off"."""
    if not cfg.slicer_enabled or not cfg.slicer_url:
        return None
    return SlicerClient(cfg.slicer_url, slice_timeout_s=cfg.slice_timeout_s)
