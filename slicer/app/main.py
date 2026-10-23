"""Slicer sidecar HTTP API: /health, /profiles, /slice (multipart contract
defined by backend `slicer_client._build_slice_request`)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import UploadFile

from app import runner
from app.profiles import KINDS, ProfileError, ProfileIndex

BAMBU_DIR = Path(os.environ.get("BAMBU_DIR", "/opt/bambu"))
CLI = os.environ.get("BAMBU_CLI", str(BAMBU_DIR / "bin" / "bambu-studio"))
PROFILES_DIR = Path(os.environ.get("SLICER_PROFILES_DIR", BAMBU_DIR / "resources/profiles/BBL"))
VERSION = os.environ.get("BAMBU_VERSION", "unknown")
MAX_JOBS = int(os.environ.get("SLICER_MAX_JOBS", "1"))
JOB_TIMEOUT_S = float(os.environ.get("SLICER_JOB_TIMEOUT_S", "600"))
MAX_UPLOAD = int(os.environ.get("SLICER_MAX_UPLOAD_MB", "200")) * 1024 * 1024
USE_XVFB = os.environ.get("SLICER_USE_XVFB", "0") == "1"
MODEL_SUFFIXES = {".stl", ".3mf", ".obj", ".step", ".stp", ".amf"}

app = FastAPI(title="tdmm-slicer")
app.state.index = ProfileIndex(PROFILES_DIR)
app.state.sem = asyncio.Semaphore(MAX_JOBS)
app.state.running = 0


def _err(status: int, error: str, message: str, exit_code=None, log_tail="", headers=None):
    return JSONResponse(
        {"error": error, "message": message, "exit_code": exit_code, "log_tail": log_tail},
        status_code=status,
        headers=headers,
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": VERSION,
        "busy": app.state.running >= MAX_JOBS,
        "max_concurrency": MAX_JOBS,
    }


@app.get("/profiles")
async def profiles(flatten: int = 0):
    ix: ProfileIndex = app.state.index
    out = []
    for kind in KINDS:
        out += ix.entries(kind, flatten=bool(flatten))
    return out


async def _read_part(part: UploadFile, limit: int) -> bytes:
    data = bytearray()
    while chunk := await part.read(1 << 20):
        data += chunk
        if len(data) > limit:
            raise OverflowError
    return bytes(data)


def _load_json(raw: bytes, what: str):
    try:
        return json.loads(raw)
    except ValueError:
        raise runner.BadRequest(f"{what} is not valid JSON") from None


@app.post("/slice")
async def slice_(request: Request):
    sem: asyncio.Semaphore = app.state.sem
    if sem.locked():
        return _err(429, "busy", "slicer is busy", headers={"Retry-After": "15"})
    await sem.acquire()
    app.state.running += 1
    job_dir = runner.new_job_dir()
    try:
        try:
            form = await request.form(max_part_size=MAX_UPLOAD)
        except Exception:
            return _err(413, "too_large", f"upload exceeds {MAX_UPLOAD} bytes")
        model = form.get("model")
        if not isinstance(model, UploadFile):
            return _err(400, "bad_request", "missing 'model' file")
        try:
            raw = {}
            for key in ("printer_profile", "process_profile", "filament_profiles"):
                part = form.get(key)
                if not isinstance(part, UploadFile):
                    return _err(400, "bad_request", f"missing {key!r} file")
                raw[key] = _load_json(await _read_part(part, MAX_UPLOAD), key)
            options = _load_json((form.get("options") or "{}").encode(), "options")
            bed_type = str(form.get("bed_type") or "textured_plate")
            model_bytes = await _read_part(model, MAX_UPLOAD)
        except OverflowError:
            return _err(413, "too_large", f"upload exceeds {MAX_UPLOAD} bytes")
        except runner.BadRequest as exc:
            return _err(400, "bad_request", str(exc))

        suffix = Path(model.filename or "").suffix.lower()
        if suffix not in MODEL_SUFFIXES:
            return _err(400, "bad_request", f"unsupported model type {suffix!r}")
        if not isinstance(raw["printer_profile"], dict) or not isinstance(
            raw["process_profile"], dict
        ):
            return _err(400, "bad_request", "printer/process profile must be JSON objects")
        fils = raw["filament_profiles"]
        if isinstance(fils, dict):
            fils = [fils]
        if not isinstance(fils, list) or not all(isinstance(f, dict) for f in fils):
            return _err(400, "bad_request", "filament_profiles must be a JSON list of objects")
        try:
            runner.bed_type_name(bed_type)
            ix: ProfileIndex = app.state.index
            flat = {
                "printer": ix.flatten_dict("printer", raw["printer_profile"]),
                "process": ix.flatten_dict("process", raw["process_profile"]),
            }
            flat_fils = [ix.flatten_dict("filament", f) for f in fils]
        except (ProfileError, runner.BadRequest) as exc:
            return _err(400, "bad_profile", str(exc))

        job_model = job_dir / f"model{suffix}"
        job_model.write_bytes(model_bytes)
        settings = []
        for i, (kind, data) in enumerate(flat.items()):
            path = job_dir / f"{kind}.json"
            path.write_text(json.dumps(data))
            settings.append(path)
        filaments = []
        for i, data in enumerate(flat_fils):
            path = job_dir / f"filament{i}.json"
            path.write_text(json.dumps(data))
            filaments.append(path)

        plate = options.get("plate") if isinstance(options, dict) else None
        try:
            out = await runner.run_slice(
                cli=CLI,
                job=runner.Job(job_dir, job_model, settings, filaments),
                bed_type=bed_type,
                auto_orient=bool(options.get("auto_orient")),
                arrange=bool(options.get("arrange")),
                plate=int(plate) if plate else None,
                timeout_s=JOB_TIMEOUT_S,
                use_xvfb=USE_XVFB,
            )
        except runner.SliceTimeout as exc:
            return _err(504, "timeout", exc.message, None, exc.log_tail)
        except runner.SliceFailed as exc:
            return _err(422, "slice_failed", exc.message, exc.exit_code, exc.log_tail)
        return Response(out, media_type="application/octet-stream")
    finally:
        runner.cleanup(job_dir)
        app.state.running -= 1
        sem.release()
