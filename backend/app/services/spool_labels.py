"""Spool label rendering (T5): reportlab PDF with a QR back to the spool,
PNG pages rasterised with pypdfium2."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

import qrcode
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

# template -> (label height mm, sheet label width mm)
_TEMPLATES = {"tight": (25.0, 50.0), "roomy": (40.0, 90.0)}
ROLL_WIDTH_MM = 62.0
_MARGIN = 10 * mm
_GAP = 2 * mm
_BORDER = Color(0.75, 0.75, 0.75)


@dataclass(frozen=True)
class LabelData:
    spool_id: int
    material: str
    brand: str | None
    color_name: str | None
    color_hex: str | None
    location: str | None
    url: str
    weight_g: float | None = None
    kind: str = "spool"  # "spool" | "location"
    title: str | None = None  # location labels: the location name


LABEL_FIELDS = ("name", "material", "swatch", "weight", "qr", "number")


def _qr(url: str) -> ImageReader:
    q = qrcode.QRCode(border=1, box_size=8, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(url)
    q.make(fit=True)
    return ImageReader(q.make_image(fill_color="black", back_color="white").get_image())


def _fit(c: rl_canvas.Canvas, text: str, font: str, size: float, max_w: float) -> float:
    while size > 4 and c.stringWidth(text, font, size) > max_w:
        size -= 0.5
    return size


def _draw_label(
    c: rl_canvas.Canvas,
    d: LabelData,
    x: float,
    y: float,
    w: float,
    h: float,
    template: str,
    fields: frozenset[str] = frozenset(LABEL_FIELDS),
) -> None:
    """Draw one label with its lower-left corner at (x, y)."""
    c.setStrokeColor(_BORDER)
    c.setLineWidth(0.3)
    c.rect(x, y, w, h)
    pad = 2 * mm
    sw = 0.0
    if "swatch" in fields and d.kind == "spool":
        sw = 4 * mm
        try:
            c.setFillColor(HexColor(d.color_hex or "#CCCCCC"))
        except (ValueError, TypeError):
            c.setFillColor(HexColor("#CCCCCC"))
        c.rect(x, y, sw, h, stroke=0, fill=1)
    qr_size = 0.0
    if "qr" in fields:
        qr_size = min(h - 2 * pad, w * 0.4)
        c.drawImage(_qr(d.url), x + w - qr_size - pad, y + (h - qr_size) / 2, qr_size, qr_size)
    tx = x + sw + pad
    tw = w - sw - qr_size - (3 if qr_size else 2) * pad
    c.setFillColor(black)
    cy = y + h - pad
    big = 20 if template == "roomy" else 14
    head = (
        d.title or "" if d.kind == "location" else (f"#{d.spool_id}" if "number" in fields else "")
    )
    if d.kind == "location" and "number" in fields:
        head = f"{head} (#{d.spool_id})" if head else f"#{d.spool_id}"
    if head:
        size = _fit(c, head, "Helvetica-Bold", big, tw)
        c.setFont("Helvetica-Bold", size)
        cy -= size
        c.drawString(tx, cy, head)
    lines: list[str] = []
    if d.kind == "location":
        if d.location:
            lines.append(d.location)
    else:
        if "material" in fields:
            lines.append(" ".join(p for p in (d.brand, d.material) if p))
        if "name" in fields and d.color_name:
            lines.append(d.color_name)
        if template == "roomy":
            if "name" in fields and d.color_hex:
                lines.append(d.color_hex.upper())
            if "weight" in fields and d.weight_g is not None:
                lines.append(f"{d.weight_g:g} g")
            if d.location:
                lines.append(d.location)
        elif "weight" in fields and d.weight_g is not None:
            lines.append(f"{d.weight_g:g} g")
    fs = 8 if template == "roomy" else 6.5
    for line in lines:
        if not line:
            continue
        cy -= fs + 2
        if cy < y + 1:
            break
        s = _fit(c, line, "Helvetica", fs, tw)
        c.setFont("Helvetica", s)
        c.drawString(tx, cy, line)
    c.setFillColor(white)


def render_pdf(
    items: list[LabelData],
    template: str,
    page: str,
    fields: frozenset[str] | None = None,
    offset: int = 0,
) -> bytes:
    """``fields`` picks which content blocks print; ``offset`` skips that many
    leading cells on a sheet page (a partly used Avery sheet). Roll pages
    ignore the offset."""
    fields = frozenset(LABEL_FIELDS) if fields is None else fields
    lh, sheet_w = _TEMPLATES[template]
    buf = io.BytesIO()
    if page == "roll_62mm":
        w, h = ROLL_WIDTH_MM * mm, lh * mm
        c = rl_canvas.Canvas(buf, pagesize=(w, h))
        for d in items:
            _draw_label(c, d, 0, 0, w, h, template, fields)
            c.showPage()
        c.save()
        return buf.getvalue()
    pw, ph = A4 if page == "a4" else letter
    lw, lhh = sheet_w * mm, lh * mm
    cols = max(1, int((pw - 2 * _MARGIN + _GAP) // (lw + _GAP)))
    rows = max(1, int((ph - 2 * _MARGIN + _GAP) // (lhh + _GAP)))
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))
    per_page = cols * rows
    offset = max(0, offset) % per_page  # whole skipped pages are meaningless
    for n, d in enumerate(items):
        i = n + offset
        slot = i % per_page
        if i and slot == 0:
            c.showPage()
        col, row = slot % cols, slot // cols
        x = _MARGIN + col * (lw + _GAP)
        y = ph - _MARGIN - (row + 1) * lhh - row * _GAP
        _draw_label(c, d, x, y, lw, lhh, template, fields)
    c.showPage()
    c.save()
    return buf.getvalue()


def pdf_to_pngs(pdf: bytes, scale: float = 4.0, first_only: bool = False) -> list[bytes]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    out: list[bytes] = []
    try:
        for i in range(len(doc)):
            img = doc[i].render(scale=scale).to_pil()
            b = io.BytesIO()
            img.save(b, format="PNG")
            out.append(b.getvalue())
            if first_only:
                break
    finally:
        doc.close()
    return out


def zip_pngs(pngs: list[bytes]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_STORED) as z:
        for i, p in enumerate(pngs, 1):
            z.writestr(f"labels-{i:02d}.png", p)
    return b.getvalue()


def encode_output(pdf: bytes, fmt: str, stem: str) -> tuple[bytes, str, str]:
    """(body, media type, filename) for a rendered label PDF in ``fmt``."""
    if fmt == "pdf":
        return pdf, "application/pdf", f"{stem}.pdf"
    pngs = pdf_to_pngs(pdf)
    if len(pngs) == 1:
        return pngs[0], "image/png", f"{stem}.png"
    return zip_pngs(pngs), "application/zip", f"{stem}.zip"
