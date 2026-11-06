"""PR-D leftovers: lenient plate schema, sliced+project plate merge, object assets lock."""

from __future__ import annotations

from types import SimpleNamespace

from app.config import get_settings
from app.schemas.library import PlateOut
from app.services import library as library_service
from app.services import object_assets
from app.services.costing import CostRates


def test_plate_out_skips_malformed_rows() -> None:
    raw = {
        "index": 1,
        "colour_changes": [
            {"z": 1.0, "colour": "#FF0000", "kind": "tool"},
            {"z": "nope"},  # malformed: skipped
            "junk",
        ],
        "pauses": [{"z": 2.0}, {}, None],
        "filaments": [{"slot": 1, "colour": "#00FF00"}, 7],
    }
    out = PlateOut.from_raw(raw, thumbnail_available=False, rates=CostRates(0.0, 0.0))
    assert [(c.z, c.kind) for c in out.colour_changes] == [(1.0, "tool")]
    assert [p.z for p in out.pauses] == [2.0]
    assert [f.slot for f in out.filaments] == [1]


def test_raw_plates_merges_project_changes_into_sliced_plates() -> None:
    meta = SimpleNamespace(
        raw={"plates": [{"index": 1, "weight_g": 3.0}, {"index": 2}]},
        plates_json=[
            {"index": 1, "colour_changes": [{"z": 5, "colour": "#FFFFFF"}], "pauses": [{"z": 3}]},
            {"index": 3, "pauses": [{"z": 9}]},
        ],
    )
    got = library_service._raw_plates(meta)
    assert got[0]["weight_g"] == 3.0 and got[0]["pauses"] == [{"z": 3}]
    assert got[0]["colour_changes"] == [{"z": 5, "colour": "#FFFFFF"}]
    assert "pauses" not in got[1]
    assert library_service._raw_plates(SimpleNamespace(raw=None, plates_json=[{"index": 4}])) == [
        {"index": 4}
    ]


def test_ensure_objects_clears_old_version_output(tmp_path, monkeypatch) -> None:
    settings = get_settings()
    h = "e" * 64
    from app.services import derivatives

    stale = derivatives.object_glb_path(settings, h, 9)
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"old")
    derivatives.objects_json_path(settings, h).write_text('{"v": 1, "objects": []}')
    monkeypatch.setattr(object_assets.plates3mf, "object_meshes", lambda _src: [])
    assert object_assets.ensure_objects(settings, h, tmp_path / "x.3mf") == []
    assert not stale.exists()
    assert object_assets.read_cached(settings, h) == []
