"""`plates3mf.read_embedded_process`: the 3MF's own layer height / profile names."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.models.enums import BlobFormat, BlobKind
from app.models.library import Model, Revision
from app.pipeline import plates3mf


def _zip(cfg: object | None) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        if cfg is not None:
            zf.writestr(
                "Metadata/project_settings.config",
                cfg if isinstance(cfg, str) else json.dumps(cfg),
            )
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


def test_reads_layer_height_and_ids():
    out = plates3mf.read_embedded_process(
        _zip(
            {
                "layer_height": "0.2",
                "print_settings_id": "0.20mm Standard @BBL X1C",
                "printer_settings_id": "Bambu Lab P1S 0.4 nozzle",
            }
        )
    )
    assert out == {
        "layer_height": 0.2,
        "nozzle_mm": None,
        "print_settings_id": "0.20mm Standard @BBL X1C",
        "printer_settings_id": "Bambu Lab P1S 0.4 nozzle",
    }


def test_missing_or_bad_config_is_none_or_partial():
    assert plates3mf.read_embedded_process(_zip(None)) is None
    assert plates3mf.read_embedded_process(_zip("not json")) is None
    assert plates3mf.read_embedded_process(_zip({}))["layer_height"] is None


@pytest.mark.usefixtures("library_root")
async def test_file_lookup_exposes_embedded_settings(authenticated_client, db_session, seed_file):
    model = Model(slug="emb", name="emb")
    db_session.add(model)
    await db_session.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db_session.add(rev)
    await db_session.commit()
    await db_session.refresh(rev)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps({"layer_height": "0.16"}))
    f = await seed_file(
        model, rev, "p.3mf", buf.getvalue(), blob_format=BlobFormat.THREEMF, blob_kind=BlobKind.MESH
    )
    r = await authenticated_client.get(f"/api/files/{f.id}")
    assert r.status_code == 200, r.text
    assert r.json()["embedded"]["layer_height"] == 0.16
