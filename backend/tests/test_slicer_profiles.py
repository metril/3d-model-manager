"""Tests for `app.services.slicer_profiles`: Bambu Studio `BBL` bundle
import, `inherits` resolution, printer_model filtering, and upsert-on-
(kind, name).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.models import SlicerProfile
from app.models.enums import ProfileKind, ProfileSource
from app.services.slicer_profiles import (
    ParsedProfile,
    ProfileImportError,
    filter_for_models,
    parse_bundle,
    resolve,
    upsert_profiles,
)
from tests.slicer_fixtures import write_bundle, write_bundle_zip

# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


def test_resolve_inherits_chain():
    by_name = {
        "grandparent": {"name": "grandparent", "a": 1, "b": 1},
        "parent": {"name": "parent", "inherits": "grandparent", "b": 2, "c": 2},
        "child": {"name": "child", "inherits": "parent", "c": 3},
    }
    merged = resolve("child", by_name)
    assert merged == {"name": "child", "a": 1, "b": 2, "c": 3}
    assert "inherits" not in merged


def test_resolve_cycle_raises():
    by_name = {
        "a": {"name": "a", "inherits": "b"},
        "b": {"name": "b", "inherits": "a"},
    }
    with pytest.raises(ProfileImportError):
        resolve("a", by_name)


def test_resolve_missing_parent_raises():
    by_name = {"child": {"name": "child", "inherits": "ghost"}}
    with pytest.raises(ProfileImportError):
        resolve("child", by_name)


# ---------------------------------------------------------------------------
# parse_bundle()
# ---------------------------------------------------------------------------


def test_parse_bundle_instantiable_only(tmp_path):
    root = write_bundle(tmp_path / "bundle")
    parsed = parse_bundle(root)
    names = {p.name for p in parsed}
    # Abstract (instantiation: false) presets never appear.
    assert "fdm_bbl_3dp_001_common" not in names
    assert "fdm_filament_bbl_common" not in names
    assert "fdm_filament_pla" not in names
    # Instantiated leaves do, with `inherits` resolved out of their data.
    assert "Bambu Lab A1 mini 0.4 nozzle" in names
    assert "Bambu PLA Basic @BBL A1M" in names
    machine = next(p for p in parsed if p.name == "Bambu Lab A1 mini 0.4 nozzle")
    assert machine.kind == ProfileKind.PRINTER
    assert machine.printer_model == "Bambu Lab A1 mini"
    assert machine.nozzle_mm == 0.4
    assert "inherits" not in machine.data
    filament = next(p for p in parsed if p.name == "Bambu PLA Basic @BBL A1M")
    assert filament.kind == ProfileKind.FILAMENT
    # Inherited fields from the three-level chain all present.
    assert filament.data["filament_type"] == "PLA"
    assert filament.data["filament_density"] == "1.24"
    assert filament.data["filament_colour"] == "#FFFFFF"
    # Resolved via its sole compatible printer.
    assert filament.printer_model == "Bambu Lab A1 mini"
    assert filament.nozzle_mm == 0.4


def test_parse_bundle_process_printer_model_from_compatible_printers(tmp_path):
    root = write_bundle(tmp_path / "bundle")
    parsed = parse_bundle(root)
    process = next(p for p in parsed if p.name == "0.20mm Standard @BBL A1M")
    assert process.kind == ProfileKind.PROCESS
    assert process.printer_model == "Bambu Lab A1 mini"
    assert process.nozzle_mm == 0.4


def test_printer_model_filter(tmp_path):
    root = write_bundle(tmp_path / "bundle")
    parsed = parse_bundle(root, printer_model="Bambu Lab A1 mini")
    names = {p.name for p in parsed}
    assert "Bambu Lab A1 mini 0.4 nozzle" in names
    assert "Bambu Lab X1 Carbon 0.4 nozzle" not in names
    assert "0.20mm Standard @BBL A1M" in names
    assert "0.20mm Standard @BBL X1C" not in names
    assert "Bambu PLA Basic @BBL A1M" in names
    assert "Bambu PLA Basic @BBL X1C" not in names


def test_parse_bundle_accepts_zip(tmp_path):
    zip_path = write_bundle_zip(tmp_path / "bundle.zip", tmp_path / "tree")
    parsed = parse_bundle(zip_path)
    names = {p.name for p in parsed}
    assert "Bambu Lab A1 mini 0.4 nozzle" in names
    assert "Bambu PLA Basic @BBL A1M" in names


def test_parse_bundle_rejects_zip_path_traversal(tmp_path):
    import zipfile

    root = write_bundle(tmp_path / "bundle")
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in root.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(root))
        zf.writestr("../../etc/evil.json", "{}")
    with pytest.raises(ProfileImportError):
        parse_bundle(zip_path)


# ---------------------------------------------------------------------------
# upsert_profiles()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_bundle_upserts(db_session):
    from app.tasks import base

    root_dir = Path(tempfile.mkdtemp())
    root = write_bundle(root_dir / "bundle")
    parsed = parse_bundle(root, printer_model="Bambu Lab A1 mini")

    with base.sync_session() as session:
        result = upsert_profiles(session, parsed, user_id=None)
    assert result == {"created": len(parsed), "updated": 0, "skipped": 0}

    rows = (await db_session.execute(SlicerProfile.__table__.select())).fetchall()
    assert len(rows) == len(parsed)

    # Re-import: same (kind, name) rows update in place, no duplicates.
    with base.sync_session() as session:
        result2 = upsert_profiles(session, parsed, user_id=None)
    assert result2 == {"created": 0, "updated": len(parsed), "skipped": 0}

    rows2 = (await db_session.execute(SlicerProfile.__table__.select())).fetchall()
    assert len(rows2) == len(parsed)


# ---------------------------------------------------------------------------
# sidecar sync (source=SYNCED) + model filter
# ---------------------------------------------------------------------------


def _pp(name: str, data: dict | None = None) -> ParsedProfile:
    return ParsedProfile(
        kind=ProfileKind.PROCESS,
        name=name,
        printer_model=None,
        nozzle_mm=None,
        data=data or {"name": name, "v": 1},
    )


@pytest.mark.asyncio
async def test_synced_upsert_idempotent_and_keeps_user_rows(db_session):
    from sqlalchemy import select

    from app.tasks import base

    db_session.add(
        SlicerProfile(
            kind=ProfileKind.PROCESS, name="mine", data={"mine": True}, source=ProfileSource.BUILTIN
        )
    )
    await db_session.commit()

    batch = [_pp("a"), _pp("mine", {"name": "mine", "v": 2})]
    with base.sync_session() as session:
        first = upsert_profiles(session, batch, None, ProfileSource.SYNCED)
        second = upsert_profiles(
            session, [_pp("a", {"name": "a", "v": 2}), batch[1]], None, ProfileSource.SYNCED
        )
    assert first == {"created": 1, "updated": 0, "skipped": 1}
    assert second == {"created": 0, "updated": 1, "skipped": 1}

    db_session.expire_all()
    rows = {r.name: r for r in (await db_session.execute(select(SlicerProfile))).scalars()}
    assert rows["a"].source is ProfileSource.SYNCED and rows["a"].data["v"] == 2
    assert rows["mine"].source is ProfileSource.BUILTIN and rows["mine"].data == {"mine": True}


def test_filter_for_models():
    entries = [
        {"kind": "printer", "name": "A1M 0.4", "printer_model": "Bambu Lab A1 mini"},
        {"kind": "printer", "name": "A1 0.4", "printer_model": "Bambu Lab A1"},
        {"kind": "process", "name": "p-a1m", "compatible_printers": ["A1M 0.4"]},
        {"kind": "process", "name": "p-a1", "compatible_printers": ["A1 0.4"]},
        {"kind": "filament", "name": "f-both", "compatible_printers": ["A1 0.4", "A1M 0.4"]},
    ]
    kept = {e["name"] for e in filter_for_models(entries, ["A1 mini", None])}
    assert kept == {"A1M 0.4", "p-a1m", "f-both"}  # "A1" must not match "A1 mini"
    assert filter_for_models(entries, [None, ""]) == entries
