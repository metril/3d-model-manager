"""``app.services.slicer_naming`` (Round 8 Task 4): Bambu Studio export
filename -> model name. Pure/stdlib-only -- no fixtures needed."""

from __future__ import annotations

import pytest

from app.services.slicer_naming import _safe_basename, model_name_from_filename, strip_known_ext


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Part 5_PLA_6h15m.gcode", "Part 5"),
        ("Benchy.gcode.3mf", "Benchy"),
        ("Thing_PETG-CF_12h3m.gcode", "Thing"),
        ("Widget.3mf", "Widget"),
        ("X_plate_2.gcode.3mf", "X"),
        ("Orca Cube_PLA+_1h20m30s.gcode.3mf", "Orca Cube"),  # OrcaSlicer export names
        ("Bracket_ABS-GF_45m.gcode", "Bracket"),
        ("Foo_PLA.gcode", "Foo_PLA"),  # material without duration: untouched
        ("Model.stl", "Model"),
        ("sub/dir/Benchy.gcode.3mf", "Benchy"),
        ("..\\evil.gcode", "evil"),
    ],
)
def test_model_name_from_filename_required_table(filename: str, expected: str) -> None:
    assert model_name_from_filename(filename) == expected


class TestSafeBasename:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("Benchy.gcode.3mf", "Benchy.gcode.3mf"),
            ("sub/dir/Benchy.gcode.3mf", "Benchy.gcode.3mf"),
            ("sub\\dir\\Benchy.gcode.3mf", "Benchy.gcode.3mf"),
            ("../../etc/x.gcode", "x.gcode"),
            ("..\\evil.gcode", "evil.gcode"),
        ],
    )
    def test_takes_basename_across_both_separators(self, filename: str, expected: str) -> None:
        assert _safe_basename(filename) == expected

    @pytest.mark.parametrize("filename", ["", ".", "..", "sub/", "sub/.", "sub/.."])
    def test_rejects_empty_or_dot_names(self, filename: str) -> None:
        with pytest.raises(ValueError):
            _safe_basename(filename)


class TestStripKnownExt:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Benchy.gcode.3mf", "Benchy"),
            ("Benchy.GCODE.3MF", "Benchy"),  # case-insensitive
            ("Part.gcode", "Part"),
            ("Widget.3mf", "Widget"),
            ("Model.stl", "Model"),
            ("Model.obj", "Model"),
            ("Part.step", "Part"),
            ("Part.stp", "Part"),
            ("Part.iges", "Part"),
            ("Part.igs", "Part"),
        ],
    )
    def test_known_extensions_stripped_longest_first(self, name: str, expected: str) -> None:
        assert strip_known_ext(name) == expected

    def test_unknown_extension_strips_last_suffix(self) -> None:
        assert strip_known_ext("notes.txt") == "notes"

    def test_no_dot_returns_unchanged(self) -> None:
        assert strip_known_ext("Model") == "Model"


def test_model_name_from_filename_empty_after_stripping_metadata_only_name() -> None:
    """A filename that's ENTIRELY a `_plate_N` marker with no project name at
    all strips down to an empty string -- callers are documented to fall
    back to a generic placeholder in that case (this module itself never
    injects one, it just returns whatever's left)."""
    assert model_name_from_filename("plate_1.gcode.3mf") != ""  # "plate_1" itself is the stem
    assert model_name_from_filename("_plate_1.gcode.3mf") == ""
