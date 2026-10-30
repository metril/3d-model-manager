"""W5 layer timelapse: per-layer capture, assembly, shared-blob retention on purge."""

from __future__ import annotations

import hashlib

import pytest
import redis as redis_lib

from app.config import get_settings
from app.models import PrintArchive, PrintJob
from app.models.enums import ArchiveFetchState, ArchiveResult, BlobFormat, BlobKind
from app.services import camera_state as cs
from app.services import layer_timelapse as lt
from app.services.archive import _ensure_blob, archive_path
from app.services.archive_blobs import delete_archives

pytestmark = pytest.mark.usefixtures("printer_enabled", "data_dir")

S = get_settings
JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"


@pytest.fixture
def r(redis_url):
    return redis_lib.Redis.from_url(redis_url)


def _fresh(r, pid: int, jid: int) -> None:
    lt.clear(r, get_settings(), pid, jid)
    r.delete(cs.frame_key(pid))


def _archive(seed, subtask: str, **kw) -> tuple[PrintJob, PrintArchive]:
    job_id = seed.job(subtask=subtask)
    arc = PrintArchive(
        print_job_id=job_id,
        result=ArchiveResult.COMPLETED,
        fetch_state=ArchiveFetchState.DONE,
        **kw,
    )
    seed.s.add(arc)
    seed.s.commit()
    return seed.s.get(PrintJob, job_id), arc


def test_capture_once_per_layer(r):
    pid, jid = 901, 1
    _fresh(r, pid, jid)
    cs.put_frame(r, pid, JPEG)
    assert lt.capture(r, S(), pid, jid, 1) is True
    assert lt.capture(r, S(), pid, jid, 1) is False  # same layer, repeated reports
    assert lt.capture(r, S(), pid, jid, 2) is True
    assert lt.capture(r, S(), pid, jid, 1) is False  # never goes backwards
    assert lt.capture(r, S(), pid, jid, None) is False
    assert lt.frame_count(S(), pid, jid) == 2
    d = lt.frames_dir(S(), pid, jid)
    assert sorted(f.name for f in d.iterdir()) == ["000001.jpg", "000002.jpg"]
    assert (d / "000001.jpg").read_bytes() == JPEG
    assert not [k for k in r.keys(f"printer:{pid}:layertl:*") if not k.endswith(b":last")]
    _fresh(r, pid, jid)


def test_capture_without_frame_retries_same_layer(r):
    pid, jid = 902, 1
    _fresh(r, pid, jid)
    assert lt.capture(r, S(), pid, jid, 3) is False
    cs.put_frame(r, pid, JPEG)
    assert lt.capture(r, S(), pid, jid, 3) is True
    _fresh(r, pid, jid)


def test_enabled_flag():
    assert lt.enabled({"layer_timelapse": True})
    assert not lt.enabled({}) and not lt.enabled(None)


def test_assemble_stores_timelapse_and_clears(seed, r, monkeypatch):
    job, arc = _archive(seed, "LT")
    _fresh(r, job.printer_id, job.id)
    cs.put_frame(r, job.printer_id, JPEG)
    for layer in (1, 2, 3):
        assert lt.capture(r, S(), job.printer_id, job.id, layer)
    monkeypatch.setattr(lt, "stitch", lambda src, n, dest, **k: dest.write_bytes(b"MP4") or True)

    assert lt.assemble_for(seed.s, get_settings(), r, job.id) is True
    seed.s.refresh(arc)
    assert arc.timelapse_blob == hashlib.sha256(b"MP4").hexdigest()
    assert (arc.timelapse_frames, arc.timelapse_layers) == (3, 3)
    # frames stay until the task clears them; stitch read them from disk
    lt.clear(r, get_settings(), job.printer_id, job.id)
    assert lt.frame_count(S(), job.printer_id, job.id) == 0
    assert not lt.frames_dir(get_settings(), job.printer_id, job.id).exists()


def test_assemble_failure_leaves_archive(seed, r, monkeypatch):
    job, arc = _archive(seed, "LT2")
    _fresh(r, job.printer_id, job.id)
    cs.put_frame(r, job.printer_id, JPEG)
    lt.capture(r, S(), job.printer_id, job.id, 1)
    monkeypatch.setattr(lt, "stitch", lambda *a, **k: False)
    assert lt.assemble_for(seed.s, get_settings(), r, job.id) is False
    seed.s.refresh(arc)
    assert arc.timelapse_blob is None
    _fresh(r, job.printer_id, job.id)


def test_purge_keeps_blob_shared_with_other_archive(seed):
    """Two archives (e.g. plates of one 3MF) holding the same timelapse blob:
    deleting one must not release the file the other still uses."""
    s, settings = seed.s, get_settings()
    sha = "ab" * 32
    _ensure_blob(s, sha, 3, BlobKind.VIDEO, BlobFormat.MP4)
    path = archive_path(settings, sha, "mp4")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MP4")
    ids = [_archive(seed, f"P{n}", timelapse_blob=sha)[1].id for n in (1, 2)]
    delete_archives(s, settings, [ids[0]])
    assert path.exists()
    delete_archives(s, settings, [ids[1]])
    assert not path.exists()


def test_layer_timelapse_option_must_be_bool():
    from app.printers.camera_select import normalize_options

    assert normalize_options(get_settings(), {"layer_timelapse": True}, None) == {
        "layer_timelapse": True
    }
    with pytest.raises(ValueError):
        normalize_options(get_settings(), {"layer_timelapse": "yes"}, None)
