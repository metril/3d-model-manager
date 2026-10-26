"""Archive source attachments: upload / download / replace / delete and the
auto-link from a library model (trash respected)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import get_settings
from app.models import File, Model
from app.models.enums import BlobFormat, BlobKind
from app.models.library import Blob, Revision
from app.models.printing import PrintArchive
from app.services import archive_sources
from app.services.archive import archive_path
from app.tasks.base import sync_session
from tests.test_archive_extras import _arc, _printer

pytestmark = pytest.mark.usefixtures("library_root")

DATA = b"PK-fake-3mf-source"


async def test_source_and_f3d_lifecycle(authenticated_client, viewer_client, db_session):
    a = await _arc(db_session, await _printer(db_session))
    c = authenticated_client
    url = f"/api/archives/{a.id}"
    assert (await c.get(f"{url}/source")).status_code == 404

    r = await c.put(f"{url}/source", files={"file": ("proj.3mf", DATA, "application/zip")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["has_source"] and out["source_name"] == "proj.3mf" and not out["has_f3d"]
    got = await c.get(f"{url}/source")
    assert got.status_code == 200 and got.content == DATA
    assert "proj.3mf" in got.headers["content-disposition"]

    r = await c.put(f"{url}/f3d", files={"file": ("part.f3d", b"fusion-bytes", "x/y")})
    assert r.json()["has_f3d"] and r.json()["f3d_name"] == "part.f3d"
    assert (await c.get(f"{url}/f3d")).content == b"fusion-bytes"

    # replace: old blob file is released
    old = (await c.get(url)).json()
    assert old["has_source"]
    r = await c.put(f"{url}/source", files={"file": ("v2.3mf", b"new-bytes", "x/y")})
    assert r.json()["source_name"] == "v2.3mf"
    assert (await c.get(f"{url}/source")).content == b"new-bytes"

    assert (
        await viewer_client.put(f"{url}/source", files={"file": ("x.3mf", b"x", "x/y")})
    ).status_code in (403, 404)

    r = await c.delete(f"{url}/source")
    assert r.status_code == 200 and not r.json()["has_source"]
    assert (await c.get(f"{url}/source")).status_code == 404
    assert (await c.get(f"{url}/f3d")).status_code == 200
    r = await c.put(f"{url}/f3d", files={"file": ("e.f3d", b"", "x/y")})
    assert r.status_code == 422


def _library_model(s, name: str, trashed: bool) -> tuple[int, int]:
    m = Model(slug=name, name=name, deleted_at=datetime.now(UTC) if trashed else None)
    s.add(m)
    s.flush()
    rev = Revision(model_id=m.id, number=1, dir_name="rev-001")
    s.add(rev)
    s.flush()
    m.current_revision_id = rev.id
    for i, (rel, fmt) in enumerate(
        [(f"{name}.3mf", BlobFormat.THREEMF), (f"{name}.f3d", BlobFormat.OTHER)]
    ):
        h = f"{name[0]}{i}".ljust(64, "0")
        s.add(Blob(hash=h, size=9, kind=BlobKind.CAD, format=fmt))
        s.flush()
        s.add(File(revision_id=rev.id, blob_hash=h, rel_path=rel, storage_path=rel))
    s.commit()
    return m.id, rev.id


async def test_link_from_model_and_trash(authenticated_client, db_session, monkeypatch):
    monkeypatch.setattr(
        archive_sources, "_read_library_file", lambda s, st, f: f"data:{f.rel_path}".encode()
    )
    p = await _printer(db_session)
    live = await _arc(db_session, p)
    trashed = await _arc(db_session, p)
    with sync_session() as s:
        m1, r1 = _library_model(s, "alpha", trashed=False)
        m2, r2 = _library_model(s, "beta", trashed=True)
        for aid, mid, rid in ((live.id, m1, r1), (trashed.id, m2, r2)):
            arc = s.get(PrintArchive, aid)
            arc.model_id, arc.revision_id = mid, rid
        s.commit()

    c = authenticated_client
    r = await c.post(f"/api/archives/{live.id}/sources/link-model")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["has_source"] and out["source_linked"] and out["has_f3d"]
    assert (await c.get(f"/api/archives/{live.id}/source")).content == b"data:alpha.3mf"
    assert (await c.get(f"/api/archives/{live.id}/f3d")).content == b"data:alpha.f3d"
    assert archive_path(get_settings(), out_hash(live.id), "3mf").exists()

    # a trashed model's files are never linked
    r = await c.post(f"/api/archives/{trashed.id}/sources/link-model")
    assert r.status_code == 404


def out_hash(archive_id: int) -> str:
    with sync_session() as s:
        return s.get(PrintArchive, archive_id).source_blob
