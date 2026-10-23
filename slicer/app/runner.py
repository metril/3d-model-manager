"""Runs the Bambu Studio CLI for one slice job.

Verified against v02.08.02.61: the CLI exits 0 even when it produced nothing
(e.g. no display), so success == the exported `.gcode.3mf` exists and
`result.json`'s `return_code` is 0. No X display is needed; `use_xvfb` is a
fallback (`xvfb-run -a`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import tempfile
from dataclasses import dataclass
from pathlib import Path

LOG_TAIL_BYTES = 4096
OUT_NAME = "out.gcode.3mf"

# Our bed_type vocabulary -> the names `--curr-bed-type` takes.
BED_TYPES = {
    "cool_plate": "Cool Plate",
    "engineering_plate": "Engineering Plate",
    "high_temp_plate": "High Temp Plate",
    "textured_plate": "Textured PEI Plate",
    "textured_pei_plate": "Textured PEI Plate",
    "supertack_plate": "Supertack Plate",
}
_NATIVE_BEDS = set(BED_TYPES.values())


class SliceFailed(Exception):
    def __init__(self, message: str, exit_code: int | None, log_tail: str) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.log_tail = log_tail


class SliceTimeout(SliceFailed):
    pass


class BadRequest(ValueError):
    pass


def bed_type_name(bed_type: str) -> str:
    key = bed_type.strip()
    if key in _NATIVE_BEDS:
        return key
    name = BED_TYPES.get(key.lower().replace(" ", "_"))
    if name is None:
        raise BadRequest(f"unknown bed_type {bed_type!r}")
    return name


def build_argv(
    *,
    cli: str,
    work: Path,
    model: Path,
    settings: list[Path],
    filaments: list[Path],
    bed_type: str,
    auto_orient: bool,
    arrange: bool,
    plate: int | None,
    use_xvfb: bool = False,
) -> list[str]:
    argv = ["xvfb-run", "-a"] if use_xvfb else []
    argv += [cli, "--load-settings", ";".join(str(p) for p in settings)]
    if filaments:
        argv += ["--load-filaments", ";".join(str(p) for p in filaments)]
    argv += ["--curr-bed-type", bed_type_name(bed_type)]
    argv += ["--orient", "1" if auto_orient else "0", "--arrange", "1" if arrange else "0"]
    argv += ["--slice", str(plate if plate else 0)]
    argv += ["--export-3mf", OUT_NAME, "--outputdir", str(work / "out"), str(model)]
    return argv


def _tail(data: bytes) -> str:
    return data[-LOG_TAIL_BYTES:].decode("utf-8", "replace")


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


@dataclass
class Job:
    dir: Path
    model: Path
    settings: list[Path]
    filaments: list[Path]


def new_job_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="slice-"))


def cleanup(job_dir: Path) -> None:
    shutil.rmtree(job_dir, ignore_errors=True)


async def run_slice(
    *,
    cli: str,
    job: Job,
    bed_type: str,
    auto_orient: bool,
    arrange: bool,
    plate: int | None,
    timeout_s: float,
    use_xvfb: bool = False,
) -> bytes:
    """Runs the CLI in its own session; kills the whole process group on
    timeout. Returns the `.gcode.3mf` bytes. Does NOT delete `job.dir`."""
    (job.dir / "out").mkdir(exist_ok=True)
    argv = build_argv(
        cli=cli,
        work=job.dir,
        model=job.model,
        settings=job.settings,
        filaments=job.filaments,
        bed_type=bed_type,
        auto_orient=auto_orient,
        arrange=arrange,
        plate=plate,
        use_xvfb=use_xvfb,
    )
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        start_new_session=True,
        cwd=job.dir,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        _kill_group(proc)
        with contextlib.suppress(Exception):
            await proc.wait()
        raise SliceTimeout(f"slice exceeded {timeout_s:.0f}s", None, "") from None
    except BaseException:
        _kill_group(proc)
        raise
    tail = _tail(out or b"")
    result = job.dir / "out" / OUT_NAME
    if proc.returncode != 0:
        raise SliceFailed(f"bambu-studio exited {proc.returncode}", proc.returncode, tail)
    rc_file = job.dir / "out" / "result.json"
    if rc_file.is_file():
        with contextlib.suppress(OSError, ValueError):
            info = json.loads(rc_file.read_text())
            if info.get("return_code", 0) != 0:
                raise SliceFailed(
                    str(info.get("error_string") or "slice failed"), info["return_code"], tail
                )
    if not result.is_file():
        raise SliceFailed("bambu-studio produced no output", proc.returncode, tail)
    return result.read_bytes()
