"""Image-to-thumbnail resizing (SPEC pipeline rows 2/4): the one place that
turns an arbitrary source image into the two DB-tracked thumbnail
derivatives every blob's gallery card is built from. ``extract_embedded_thumbs``
(Task 4) is the first caller -- feeding it a 3mf/gcode_3mf's own embedded
preview PNG, since RESEARCH §3/§4 notes assimp can't parse Bambu 3MF at all,
making the slicer's own embedded thumbnail the only source until a mesh
render exists. ``render_thumb`` (Task 6) reuses this unchanged for rasterized
mesh renders and raw png/jpg blobs.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.config import Settings
from app.models.enums import DerivativeKind
from app.services import derivatives

_THUMB_1024_PX = 1024
_THUMB_256_PX = 256


def _load_image(src: Path | bytes) -> Image.Image:
    """Open and fully decode ``src`` up front, translating any Pillow
    decode failure into a plain ``ValueError``.

    ``PIL.UnidentifiedImageError`` (and other Pillow decode failures) are
    ``OSError`` subclasses, which ``app.tasks.pipeline``'s ``run_step``
    would otherwise retry as ``TRANSIENT_ERRORS`` -- wrong for a corrupted
    embedded image, which is a deterministic parse failure exactly like an
    unparseable STL (``app.pipeline.meshload.load_mesh``'s own ``ValueError``
    convention).
    """
    source = io.BytesIO(src) if isinstance(src, bytes) else src
    try:
        with Image.open(source) as image:
            image.load()
            return image.copy()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"cannot read embedded thumbnail image: {exc}") from exc


def _normalize_mode(image: Image.Image) -> Image.Image:
    """Preserve a real alpha channel; otherwise flatten to plain RGB.

    Never manufactures alpha that wasn't there (palette images without a
    "transparency" entry, CMYK, etc. become RGB), but never drops one that
    was (RGBA, or palette *with* a "transparency" entry).
    """
    if image.mode == "RGBA" or (image.mode == "P" and "transparency" in image.info):
        return image.convert("RGBA")
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def _publish_thumb(image: Image.Image, size: int, dest: Path) -> Path:
    """Resize a copy of ``image`` to fit within ``size`` x ``size`` --
    ``Image.thumbnail`` preserves aspect ratio and only ever shrinks, so a
    source already smaller than ``size`` is left at its own resolution --
    and publish it atomically to ``dest`` via ``derivatives.publish_bytes``.
    """
    resized = image.copy()
    resized.thumbnail((size, size), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    resized.save(buf, format="PNG")
    derivatives.publish_bytes(buf.getvalue(), dest)
    return dest


def make_thumbs_from_image(
    src: Path | bytes, settings: Settings, blob_hash: str
) -> tuple[Path, Path]:
    """Resize ``src`` (a file path or already-read bytes) into the
    ``thumb_1024``/``thumb_256`` derivative files and publish both
    atomically. Never upscales past the source's own resolution; alpha is
    preserved rather than flattened onto a background. Returns
    ``(thumb_1024_path, thumb_256_path)`` -- both are ``derivative_path``s
    for ``DerivativeKind.THUMB_1024``/``THUMB_256``, but this function is
    pure file I/O: callers own the ``derivatives`` table bookkeeping.
    """
    image = _normalize_mode(_load_image(src))
    p1024 = derivatives.derivative_path(settings, blob_hash, DerivativeKind.THUMB_1024)
    p256 = derivatives.derivative_path(settings, blob_hash, DerivativeKind.THUMB_256)
    _publish_thumb(image, _THUMB_1024_PX, p1024)
    _publish_thumb(image, _THUMB_256_PX, p256)
    return p1024, p256


def render_pdf_first_page(src: Path, dest: Path, *, size: int = _THUMB_1024_PX) -> Path:
    """Rasterise page 1 of the PDF at ``src`` to a PNG at ``dest`` (long edge
    ~``size`` px). An unreadable/empty PDF is a deterministic ``ValueError``
    (not retried as a transient I/O error)."""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(src))
    except Exception as exc:
        raise ValueError(f"cannot read pdf: {exc}") from exc
    try:
        if len(doc) == 0:
            raise ValueError("pdf has no pages")
        page = doc[0]
        w, h = page.get_size()
        scale = size / max(w, h, 1.0)
        page.render(scale=scale).to_pil().convert("RGB").save(dest, format="PNG")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"cannot render pdf: {exc}") from exc
    finally:
        doc.close()
    return dest
