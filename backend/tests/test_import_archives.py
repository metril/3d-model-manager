"""``app.importers.archives.process_staged_zips`` (feat/import-fidelity T1):
MakerWorld's per-print-profile ``.zip`` downloads are actually mislabeled 3MF
containers -- sniffed and renamed in place, never extracted. A genuine zip
(Thingiverse's loose-file ``ZipFile.zip``) is extracted member-by-member
instead, with the original archive discarded. Pure function over a staged-
file list + the settings handle -- no DB/Celery involved, so every test here
builds ``StagedFile``s directly against a real spool file on disk.
"""

from __future__ import annotations

import io
import uuid
import warnings
import zipfile

import pytest
from blake3 import blake3

from app.config import get_settings
from app.importers import archives
from app.importers.download import StagedFile
from app.models.enums import BlobFormat, BlobKind
from app.services import layout, spool
from tests import corpus as corpus_module

pytestmark = pytest.mark.usefixtures("data_dir")


def _stage_bytes(settings, rel_path: str, content: bytes) -> StagedFile:
    """Test-only twin of ``download.stream_remote_to_spool`` that skips the
    network entirely -- writes ``content`` straight to a real spool file so
    ``zipfile.ZipFile`` (which needs a real path/file object, not raw bytes
    handed in-process) can open it exactly like the real import flow would.
    """
    spool.ensure_spool_dir(settings)
    token = uuid.uuid4()
    path = spool.spool_path(settings, token)
    path.write_bytes(content)
    kind, format_ = layout.infer_blob_kind_format(rel_path)
    return StagedFile(
        token=token,
        spool_path=path,
        blob_hash=blake3(content).hexdigest(),
        size=len(content),
        rel_path=rel_path,
        kind=kind,
        format_=format_,
    )


def _build_zip(members: dict[str, bytes], *, extra_names: list[str] | None = None) -> bytes:
    """A hand-rolled zip, optionally including raw ``extra_names`` entries
    (unsanitized -- lets a test smuggle in a directory-traversal or
    ``__MACOSX`` member name that ``zipfile.ZipFile.writestr`` will happily
    write even though it would never be produced by a well-behaved zip tool).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
        for name in extra_names or []:
            zf.writestr(name, b"unsafe payload")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 3MF sniff: a `.zip` that's actually a 3MF container is renamed, not
# extracted.
# ---------------------------------------------------------------------------


def test_sniff_renames_mislabeled_3mf_in_place() -> None:
    settings = get_settings()
    content = corpus_module.box_3mf_generic()  # has 3D/3dmodel.model
    sf = _stage_bytes(settings, "Cool Profile-98765.zip", content)

    result = archives.process_staged_zips(settings, [sf])

    assert len(result) == 1
    renamed = result[0]
    assert renamed.rel_path == "Cool Profile-98765.3mf"
    assert renamed.format_ == BlobFormat.THREEMF
    assert renamed.kind == BlobKind.MESH
    # Same bytes, same spool file, same hash -- a rename, not a re-stage.
    assert renamed.blob_hash == sf.blob_hash
    assert renamed.spool_path == sf.spool_path
    assert renamed.token == sf.token
    assert renamed.spool_path.exists()


def test_sniff_is_case_insensitive_on_the_zip_suffix() -> None:
    settings = get_settings()
    content = corpus_module.box_3mf_generic()
    sf = _stage_bytes(settings, "Profile-1.ZIP", content)

    result = archives.process_staged_zips(settings, [sf])

    assert len(result) == 1
    # The trailing ".ZIP" is stripped case-insensitively; the replacement
    # suffix is always the literal, lowercase ".3mf".
    assert result[0].rel_path == "Profile-1.3mf"
    assert result[0].format_ == BlobFormat.THREEMF


# ---------------------------------------------------------------------------
# Extraction: a genuine zip is exploded into its members, nested dirs
# preserved, unsafe members quietly skipped, original archive discarded.
# ---------------------------------------------------------------------------


def test_extraction_stages_members_and_drops_the_original_archive() -> None:
    settings = get_settings()
    stl_bytes = corpus_module.box_stl()
    zip_bytes = _build_zip(
        {
            "sub/": b"",  # explicit directory entry -- must be skipped, not staged
            "readme.txt": b"hello world",
            "sub/nested/part.stl": stl_bytes,
        },
        extra_names=["__MACOSX/._readme.txt", "../evil.txt"],
    )
    sf = _stage_bytes(settings, "ZipFile.zip", zip_bytes)
    original_spool_path = sf.spool_path

    result = archives.process_staged_zips(settings, [sf])

    by_rel_path = {r.rel_path: r for r in result}
    assert set(by_rel_path) == {"ZipFile/readme.txt", "ZipFile/sub/nested/part.stl"}
    assert by_rel_path["ZipFile/readme.txt"].spool_path.read_bytes() == b"hello world"
    assert by_rel_path["ZipFile/sub/nested/part.stl"].spool_path.read_bytes() == stl_bytes
    assert by_rel_path["ZipFile/sub/nested/part.stl"].format_ == BlobFormat.STL
    assert by_rel_path["ZipFile/sub/nested/part.stl"].kind == BlobKind.MESH

    # Original archive is gone -- no result entry named "ZipFile.zip", and
    # its spool file was cleaned up (extraction replaced it).
    assert "ZipFile.zip" not in by_rel_path
    assert not original_spool_path.exists()

    # Each extracted member is its OWN staged file (own token, own hash).
    for member in result:
        assert member.spool_path.exists()
        assert member.blob_hash == blake3(member.spool_path.read_bytes()).hexdigest()


def test_extraction_skipped_members_leave_no_spool_files() -> None:
    """The traversal/`__MACOSX` members from the test above must never reach
    spool at all -- not staged-then-discarded, just never staged.
    """
    settings = get_settings()
    zip_bytes = _build_zip(
        {"keep.txt": b"kept"},
        extra_names=["__MACOSX/._keep.txt", "../evil.txt", "nested/../../also_evil.txt"],
    )
    sf = _stage_bytes(settings, "loose.zip", zip_bytes)

    result = archives.process_staged_zips(settings, [sf])

    assert [r.rel_path for r in result] == ["loose/keep.txt"]
    # Nothing beyond the one legitimate member (plus its own spool file) was
    # ever written under the spool directory.
    spooled = list(spool.spool_dir(settings).glob("*"))
    assert len(spooled) == 1


# ---------------------------------------------------------------------------
# Caps: a weird archive is never extracted -- kept as a single opaque zip.
# ---------------------------------------------------------------------------


def test_over_member_cap_zip_is_kept_unextracted() -> None:
    settings = get_settings()
    members = {f"file_{i}.txt": b"x" for i in range(archives.MAX_ZIP_MEMBERS + 1)}
    zip_bytes = _build_zip(members)
    sf = _stage_bytes(settings, "huge.zip", zip_bytes)

    result = archives.process_staged_zips(settings, [sf])

    assert result == [sf]
    assert sf.spool_path.exists()


def test_over_size_cap_zip_is_kept_unextracted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same fallback for the uncompressed-size cap -- exercised with the cap
    monkeypatched down rather than actually staging 2 GiB of test data.
    """
    monkeypatch.setattr(archives, "MAX_ZIP_UNCOMPRESSED_BYTES", 10)
    settings = get_settings()
    zip_bytes = _build_zip({"big.txt": b"x" * 1000})
    sf = _stage_bytes(settings, "toobig.zip", zip_bytes)

    result = archives.process_staged_zips(settings, [sf])

    assert result == [sf]
    assert sf.spool_path.exists()


def test_corrupt_zip_is_kept_unextracted() -> None:
    settings = get_settings()
    sf = _stage_bytes(settings, "broken.zip", b"this is not a zip file at all")

    result = archives.process_staged_zips(settings, [sf])

    assert result == [sf]
    assert sf.spool_path.exists()


# ---------------------------------------------------------------------------
# One level only: an extracted member that's itself a zip stays a file
# (unless it's ALSO a disguised 3MF, which still gets sniffed/renamed).
# ---------------------------------------------------------------------------


def test_inner_zip_member_stays_a_file_and_inner_3mf_is_still_renamed() -> None:
    settings = get_settings()
    inner_plain_zip = _build_zip({"a.txt": b"plain inner zip, not a 3mf"})
    inner_3mf_zip = corpus_module.box_3mf_generic()
    outer_zip = _build_zip(
        {
            "notes.txt": b"top level file",
            "inner_plain.zip": inner_plain_zip,
            "inner_3mf.zip": inner_3mf_zip,
        }
    )
    sf = _stage_bytes(settings, "Bundle.zip", outer_zip)

    result = archives.process_staged_zips(settings, [sf])
    by_rel_path = {r.rel_path: r for r in result}

    assert set(by_rel_path) == {
        "Bundle/notes.txt",
        "Bundle/inner_plain.zip",  # stays a file -- one level only
        "Bundle/inner_3mf.3mf",  # sniffed even at depth 1, renamed not extracted
    }
    assert by_rel_path["Bundle/inner_plain.zip"].format_ == BlobFormat.OTHER
    assert by_rel_path["Bundle/inner_3mf.3mf"].format_ == BlobFormat.THREEMF
    assert by_rel_path["Bundle/inner_3mf.3mf"].kind == BlobKind.MESH
    # The plain inner zip is untouched, still a real openable zip on disk.
    with zipfile.ZipFile(by_rel_path["Bundle/inner_plain.zip"].spool_path) as zf:
        assert zf.namelist() == ["a.txt"]


# ---------------------------------------------------------------------------
# Non-zip passthrough + multi-entry ordering.
# ---------------------------------------------------------------------------


def test_non_zip_entries_pass_through_untouched() -> None:
    settings = get_settings()
    stl_sf = _stage_bytes(settings, "part.stl", corpus_module.box_stl())

    result = archives.process_staged_zips(settings, [stl_sf])

    assert result == [stl_sf]


def test_mixed_list_preserves_relative_order_of_non_zip_entries() -> None:
    settings = get_settings()
    a = _stage_bytes(settings, "a.stl", corpus_module.box_stl())
    zip_sf = _stage_bytes(settings, "b.zip", _build_zip({"c.txt": b"c", "d.txt": b"d"}))
    e = _stage_bytes(settings, "e.obj", corpus_module.box_obj())

    result = archives.process_staged_zips(settings, [a, zip_sf, e])

    rel_paths = [r.rel_path for r in result]
    assert rel_paths == ["a.stl", "b/c.txt", "b/d.txt", "e.obj"]


# ---------------------------------------------------------------------------
# F1 (post-review fix): duplicate zip member names are RENAMED, never
# skipped or allowed to collide -- ``File`` has a UNIQUE("revision_id",
# "rel_path") constraint, so two members staging to the identical rel_path
# would IntegrityError the whole import (exactly the "hostile archive fails
# the import" outcome this module's docstring promises never happens).
# Content can differ between duplicates, so both must survive.
#
# Note: ``_sanitize_member_name`` returns a safe member's name VERBATIM (it
# only validates, never normalizes/rewrites it), so two DIFFERENT raw names
# can never sanitize to the same string -- the only way to collide is a
# genuine duplicate member name already in the archive. These tests cover
# that case only.
# ---------------------------------------------------------------------------


def _build_zip_from_pairs(pairs: list[tuple[str, bytes]]) -> bytes:
    """Like ``_build_zip``, but takes an ORDERED LIST instead of a dict so a
    test can smuggle in a genuine duplicate member name (a dict's keys can't
    repeat; ``zipfile.ZipFile.writestr`` doesn't care, though it does warn --
    suppressed here since it's the exact scenario under test, not test
    pollution).
    """
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in pairs:
                zf.writestr(name, content)
    return buf.getvalue()


def test_duplicate_member_name_is_renamed_not_skipped_and_both_survive() -> None:
    zip_bytes = _build_zip_from_pairs(
        [("part.stl", b"first copy"), ("part.stl", b"second copy, different bytes")]
    )
    settings = get_settings()
    sf = _stage_bytes(settings, "Dupe.zip", zip_bytes)

    result = archives.process_staged_zips(settings, [sf])

    by_rel_path = {r.rel_path: r for r in result}
    assert set(by_rel_path) == {"Dupe/part.stl", "Dupe/part (2).stl"}
    assert by_rel_path["Dupe/part.stl"].spool_path.read_bytes() == b"first copy"
    assert (
        by_rel_path["Dupe/part (2).stl"].spool_path.read_bytes() == b"second copy, different bytes"
    )
    # Two distinct staged files -- own token, own spool path -- not a rename
    # of one in place.
    assert by_rel_path["Dupe/part.stl"].token != by_rel_path["Dupe/part (2).stl"].token
    assert by_rel_path["Dupe/part.stl"].spool_path != by_rel_path["Dupe/part (2).stl"].spool_path


def test_three_way_duplicate_member_name_gets_sequential_suffixes() -> None:
    zip_bytes = _build_zip_from_pairs(
        [("readme.txt", b"a"), ("readme.txt", b"b"), ("readme.txt", b"c")]
    )
    settings = get_settings()
    sf = _stage_bytes(settings, "Triple.zip", zip_bytes)

    result = archives.process_staged_zips(settings, [sf])

    by_rel_path = {r.rel_path: r for r in result}
    assert set(by_rel_path) == {
        "Triple/readme.txt",
        "Triple/readme (2).txt",
        "Triple/readme (3).txt",
    }
    assert by_rel_path["Triple/readme.txt"].spool_path.read_bytes() == b"a"
    assert by_rel_path["Triple/readme (2).txt"].spool_path.read_bytes() == b"b"
    assert by_rel_path["Triple/readme (3).txt"].spool_path.read_bytes() == b"c"


def test_duplicate_member_name_in_a_subdirectory_keeps_the_directory_prefix() -> None:
    zip_bytes = _build_zip_from_pairs([("sub/dir/cover.png", b"a"), ("sub/dir/cover.png", b"b")])
    settings = get_settings()
    sf = _stage_bytes(settings, "Nested.zip", zip_bytes)

    result = archives.process_staged_zips(settings, [sf])

    rel_paths = {r.rel_path for r in result}
    assert rel_paths == {"Nested/sub/dir/cover.png", "Nested/sub/dir/cover (2).png"}


def test_extensionless_duplicate_member_name_is_renamed_too() -> None:
    zip_bytes = _build_zip_from_pairs([("README", b"a"), ("README", b"b")])
    settings = get_settings()
    sf = _stage_bytes(settings, "NoExt.zip", zip_bytes)

    result = archives.process_staged_zips(settings, [sf])

    rel_paths = {r.rel_path for r in result}
    assert rel_paths == {"NoExt/README", "NoExt/README (2)"}


def test_unexpected_extraction_error_keeps_zip_and_cleans_staged_members(monkeypatch) -> None:
    """Encrypted / unsupported-compression / zlib errors aren't BadZipFile or
    OSError; any extraction failure must fall back to the original zip."""
    settings = get_settings()
    zip_bytes = _build_zip({"a.txt": b"one", "b.txt": b"two"})
    sf = _stage_bytes(settings, "Pack.zip", zip_bytes)
    real = archives.download.stage_zip_member
    staged: list[StagedFile] = []

    def flaky(settings_, zf, info, rel):
        if staged:
            raise NotImplementedError("compression type 99 is not supported")
        member = real(settings_, zf, info, rel)
        staged.append(member)
        return member

    monkeypatch.setattr(archives.download, "stage_zip_member", flaky)

    result = archives.process_staged_zips(settings, [sf])

    assert result == [sf]
    assert sf.spool_path.exists()
    assert staged and not staged[0].spool_path.exists()
