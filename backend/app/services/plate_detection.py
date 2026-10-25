"""Empty-build-plate detection (ported from Bambuddy's ``plate_detection``,
re-expressed on numpy + Pillow so no OpenCV dependency is needed).

Calibration = up to ``MAX_REFERENCES`` JPEG frames of the empty plate per
(printer, plate type). A check compares the current frame's heavily blurred,
brightness-normalised ROI against every reference and keeps the best
(lowest) mean difference, as a percentage. ``score < threshold`` = empty.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

MAX_REFERENCES = 5
# (x, y, w, h) as fractions of the frame; centre-lower part of the camera view.
DEFAULT_ROI = (0.15, 0.35, 0.70, 0.55)
DEFAULT_THRESHOLD = 1.0
MIN_THRESHOLD = 0.05
MAX_THRESHOLD = 50.0
PLATE_NOT_EMPTY = "plate not empty"
_WORK_WIDTH = 320
_BLUR_RADIUS = 3.0


@dataclass(frozen=True)
class PlateCheckConfig:
    enabled: bool
    threshold: float
    plate_type: str


def config_from_options(options: dict | None) -> PlateCheckConfig:
    raw = (options or {}).get("plate_check")
    raw = raw if isinstance(raw, dict) else {}
    try:
        thr = float(raw.get("threshold", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        thr = DEFAULT_THRESHOLD
    thr = min(MAX_THRESHOLD, max(MIN_THRESHOLD, thr))
    return PlateCheckConfig(
        enabled=bool(raw.get("enabled")),
        threshold=thr,
        plate_type=str(raw.get("plate_type") or "")[:64],
    )


def validate_options(new: dict) -> None:
    """Raises ``ValueError`` for a malformed ``options.plate_check``."""
    pc = new.get("plate_check")
    if pc is None:
        return
    if not isinstance(pc, dict):
        raise ValueError("plate_check must be an object")
    thr = pc.get("threshold")
    if thr is not None and (
        isinstance(thr, bool)
        or not isinstance(thr, int | float)
        or not MIN_THRESHOLD <= thr <= MAX_THRESHOLD
    ):
        raise ValueError(f"plate_check.threshold must be {MIN_THRESHOLD}..{MAX_THRESHOLD}")
    pt = pc.get("plate_type")
    if pt is not None and (not isinstance(pt, str) or len(pt) > 64):
        raise ValueError("plate_check.plate_type must be a string up to 64 chars")


@dataclass
class PlateResult:
    empty: bool
    score: float  # best mean difference vs a reference, in percent
    threshold: float
    best_ref: int  # index into the supplied reference list, -1 if none usable
    needs_calibration: bool = False
    message: str = ""
    preview: bytes | None = None  # annotated JPEG

    def preview_data_url(self) -> str | None:
        if self.preview is None:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(self.preview).decode()


def _decode(jpeg: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(jpeg))
    img.load()
    return img.convert("RGB")


def _box(
    size: tuple[int, int], roi: tuple[float, float, float, float]
) -> tuple[int, int, int, int]:
    w, h = size
    x, y = int(w * roi[0]), int(h * roi[1])
    return x, y, x + max(1, int(w * roi[2])), y + max(1, int(h * roi[3]))


def _prep(img: Image.Image, box: tuple[int, int, int, int]) -> np.ndarray:
    g = img.crop(box).convert("L")
    scale = _WORK_WIDTH / g.width
    g = g.resize((_WORK_WIDTH, max(1, int(g.height * scale))), Image.BILINEAR)
    a = np.asarray(g.filter(ImageFilter.GaussianBlur(_BLUR_RADIUS)), dtype=np.float32)
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-3:
        return np.zeros_like(a)
    return (a - lo) * (255.0 / (hi - lo))


def analyze(
    frame_jpeg: bytes,
    references: list[bytes],
    threshold: float = DEFAULT_THRESHOLD,
    *,
    roi: tuple[float, float, float, float] = DEFAULT_ROI,
    with_preview: bool = False,
) -> PlateResult:
    if not references:
        return PlateResult(
            True, 0.0, threshold, -1, True, "No calibration - capture empty-plate references first"
        )
    cur = _decode(frame_jpeg)
    box = _box(cur.size, roi)
    cur_p = _prep(cur, box)
    best, best_idx, best_diff = float("inf"), -1, None
    for i, ref_jpeg in enumerate(references):
        try:
            ref = _decode(ref_jpeg)
        except Exception:  # noqa: BLE001 - a corrupt reference is skipped
            continue
        if ref.size != cur.size:
            ref = ref.resize(cur.size)
        diff = np.abs(cur_p - _prep(ref, box))
        pct = float(diff.mean()) / 255.0 * 100.0
        if pct < best:
            best, best_idx, best_diff = pct, i, diff
    if best_idx < 0 or best_diff is None:
        return PlateResult(True, 0.0, threshold, -1, True, "No usable references - recalibrate")
    empty = best < threshold
    msg = (
        f"Plate appears empty ({best:.2f}% difference)"
        if empty
        else f"Objects detected on plate ({best:.2f}% difference)"
    )
    preview = (
        _preview(cur, box, best_diff, empty, best, best_idx, len(references))
        if with_preview
        else None
    )
    return PlateResult(empty, round(best, 3), threshold, best_idx, False, msg, preview)


def _preview(
    cur: Image.Image,
    box: tuple[int, int, int, int],
    diff: np.ndarray,
    empty: bool,
    score: float,
    idx: int,
    n: int,
) -> bytes:
    out = cur.copy()
    bw, bh = box[2] - box[0], box[3] - box[1]
    amp = np.minimum(diff * 3.0, 255.0).astype(np.uint8)
    heat = Image.fromarray(amp).resize((bw, bh), Image.BILINEAR)
    red = Image.merge("RGB", (heat, Image.new("L", heat.size, 0), Image.new("L", heat.size, 0)))
    region = Image.blend(out.crop(box), red, 0.5)
    out.paste(region, box[:2])
    d = ImageDraw.Draw(out)
    color = (0, 200, 0) if empty else (230, 30, 30)
    d.rectangle(box, outline=(0, 255, 0), width=2)
    d.text((10, 10), "EMPTY" if empty else "OBJECTS DETECTED", fill=color)
    d.text((10, 26), f"Diff {score:.2f}% (ref {idx + 1}/{n})", fill=color)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def normalize_jpeg(data: bytes) -> bytes:
    """Validate + re-encode a captured frame as a reference JPEG."""
    img = _decode(data)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def thumbnail(jpeg: bytes, max_size: int = 160) -> bytes:
    img = _decode(jpeg)
    img.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()
