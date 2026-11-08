from types import SimpleNamespace

from app.services.plates import resolve_plate, sliced_plate_indices


def test_prefers_plates_with_gcode():
    meta = SimpleNamespace(
        raw={"plates": [{"index": 1}, {"index": 3, "gcode_file": "Metadata/plate_3.gcode"}]}
    )
    assert sliced_plate_indices(meta) == [3]
    assert resolve_plate(meta, None) == 3
    assert resolve_plate(meta, 1) == 1
    assert resolve_plate(None, None, 1) == 1
