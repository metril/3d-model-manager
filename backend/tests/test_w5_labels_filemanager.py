"""C12 label content/offset/location labels/number search; C13 folder move + PDF thumb."""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image

from app.pipeline import thumbs
from app.services import spool_labels

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


def _label(**kw) -> spool_labels.LabelData:
    base = dict(
        spool_id=7,
        material="PLA",
        brand="Acme",
        color_name="Red",
        color_hex="#FF0000",
        location=None,
        url="http://x/scan/spool/7",
        weight_g=1000.0,
    )
    return spool_labels.LabelData(**{**base, **kw})


def _first_png(pdf: bytes) -> Image.Image:
    return Image.open(io.BytesIO(spool_labels.pdf_to_pngs(pdf, 1.5, True)[0])).convert("RGB")


def test_label_png_renders_and_fields_change_output() -> None:
    full = spool_labels.render_pdf([_label()], "roomy", "a4")
    bare = spool_labels.render_pdf([_label()], "roomy", "a4", frozenset({"qr"}))
    a, b = _first_png(full), _first_png(bare)
    assert a.size == b.size and a.tobytes() != b.tobytes()
    assert spool_labels.encode_output(full, "png", "l")[1] == "image/png"


def test_sheet_offset_skips_cells() -> None:
    plain = _first_png(spool_labels.render_pdf([_label()], "tight", "a4"))
    shifted = _first_png(spool_labels.render_pdf([_label()], "tight", "a4", None, 3))
    assert plain.tobytes() != shifted.tobytes()
    # starting on the last cell of a page pushes the second label to page 2
    pdf = spool_labels.render_pdf([_label(), _label()], "tight", "a4", None, 29)
    assert len(spool_labels.pdf_to_pngs(pdf, 0.5)) == 2


def test_roll_ignores_offset() -> None:
    a = spool_labels.render_pdf([_label()], "tight", "roll_62mm", None, 0)
    b = spool_labels.render_pdf([_label()], "tight", "roll_62mm", None, 5)
    assert len(spool_labels.pdf_to_pngs(b, 0.5)) == 1 == len(spool_labels.pdf_to_pngs(a, 0.5))


async def test_location_label_qr_and_spool_search_by_number(
    authenticated_client: httpx.AsyncClient, monkeypatch
) -> None:
    c = authenticated_client
    seen: list[str] = []
    orig = spool_labels._qr
    monkeypatch.setattr(spool_labels, "_qr", lambda url: (seen.append(url), orig(url))[1])
    loc = (await c.post("/api/storage-locations", json={"name": "Shelf A"})).json()
    r = await c.post("/api/storage-locations/labels", json={"ids": [loc["id"]], "format": "png"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert seen[0].endswith(f"/scan/location/{loc['id']}")
    assert (await c.post("/api/storage-locations/labels", json={"ids": [9999]})).status_code == 404

    sp = (await c.post("/api/spools", json={"material_type": "PETG"})).json()
    await c.post("/api/spools", json={"material_type": "ABS"})
    hit = await c.get("/api/spools", params={"q": f"#{sp['id']}"})
    assert [s["id"] for s in hit.json()] == [sp["id"]]


def test_pdf_first_page_thumbnail(tmp_path) -> None:
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "d.pdf"
    cv = canvas.Canvas(str(pdf))
    cv.drawString(100, 700, "hello")
    cv.save()
    out = thumbs.render_pdf_first_page(pdf, tmp_path / "o.png", size=300)
    with Image.open(out) as im:
        assert max(im.size) == 300
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(ValueError):
        thumbs.render_pdf_first_page(bad, tmp_path / "b.png")


async def _model_with_files(c: httpx.AsyncClient, name: str, paths: list[str]) -> dict:
    m = (await c.post("/api/models", json={"name": name})).json()
    for i, rel in enumerate(paths):
        r = await c.put(
            "/api/uploads",
            params={
                "model_id": m["id"],
                "revision_id": m["current_revision"]["id"],
                "rel_path": rel,
            },
            content=f"content-{name}-{i}".encode(),
        )
        assert r.status_code == 201, r.text
    return m


async def test_folder_move_rewrites_paths_and_moves_bytes(
    authenticated_client: httpx.AsyncClient,
) -> None:
    c = authenticated_client
    m = await _model_with_files(c, "Mover", ["a/x.stl", "a/deep/y.stl", "b/z.stl"])
    root = f"{m['slug']}/{m['current_revision']['dir_name']}"
    r = await c.post("/api/storage/folders/move", json={"src": f"{root}/a", "dest": f"{root}/b"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "done", r.json()
    tree = (await c.get("/api/storage/tree", params={"path": f"{root}/b/a"})).json()
    assert [f["name"] for f in tree["files"]] == ["x.stl"]
    assert [f["rel_path"] for f in tree["files"]] == ["b/a/x.stl"]
    deep = (await c.get("/api/storage/tree", params={"path": f"{root}/b/a/deep"})).json()
    assert deep["files"][0]["rel_path"] == "b/a/deep/y.stl"
    old = (await c.get("/api/storage/tree", params={"path": f"{root}/a"})).json()
    assert old["files"] == [] and old["dirs"] == []
    dl = await c.get(f"/api/files/{tree['files'][0]['id']}/download")
    assert dl.status_code == 200 and dl.content == b"content-Mover-0"


async def test_folder_move_validation(authenticated_client: httpx.AsyncClient) -> None:
    c = authenticated_client
    m = await _model_with_files(c, "Mover2", ["a/x.stl", "b/a/clash.stl", "b/z.stl"])
    other = await _model_with_files(c, "Other", ["q/x.stl"])
    root = f"{m['slug']}/{m['current_revision']['dir_name']}"
    oroot = f"{other['slug']}/{other['current_revision']['dir_name']}"

    async def move(src: str, dest: str) -> int:
        r = await c.post("/api/storage/folders/move", json={"src": src, "dest": dest})
        return r.status_code

    assert await move(m["slug"], root) == 422  # model dir, not inside a revision
    assert await move(root, root) == 422
    assert await move(f"{root}/a", f"{root}/a/sub") == 422  # into itself
    assert await move(f"{root}/a", oroot) == 422  # across models
    assert await move(f"{root}/a", f"{root}/b") == 409  # name clash
    assert await move(f"{root}/nope", f"{root}/b") == 404
    assert await move(f"{root}/b/a", root) == 409  # clashes with existing a/
