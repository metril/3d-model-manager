"""Filament colouring of Bambu/Orca project 3MFs (parts, modifiers, colour changes)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import trimesh

from app.models.enums import BlobFormat
from app.pipeline import convert, plates3mf, threemf_colour
from tests.threemf_builder import make_coloured_project_3mf, make_project_3mf

RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)


def _setup(tmp_path: Path):
    path = tmp_path / "c.3mf"
    path.write_bytes(make_coloured_project_3mf())
    with zipfile.ZipFile(path) as zf:
        plates = plates3mf.parse_unsliced_plates(zf)
        bed = plates3mf.read_bed(zf)
    return path, plates, bed


def _by_colour(mesh: trimesh.Trimesh) -> dict[tuple, np.ndarray]:
    fc = np.asarray(mesh.visual.face_colors)
    return {
        tuple(int(x) for x in c): mesh.triangles_center[(fc == c).all(axis=1)]
        for c in np.unique(fc, axis=0)
    }


def test_read_colour_info(tmp_path: Path) -> None:
    path, _, _ = _setup(tmp_path)
    with zipfile.ZipFile(path) as zf:
        info = threemf_colour.read_colour_info(zf)
    assert info is not None and info.is_multicolour
    assert [(e.z, e.rgba) for e in info.events[2]] == [(10.0, BLUE)]
    assert info.part_rgba(1, 0) == RED
    assert info.part_rgba(1, 1) == BLUE
    assert info.part_rgba(1, 2) is None  # modifier


def test_plain_project_not_read_as_coloured(tmp_path: Path) -> None:
    p = tmp_path / "plain.3mf"
    p.write_bytes(make_project_3mf(2))
    with zipfile.ZipFile(p) as zf:
        assert threemf_colour.read_colour_info(zf) is None
    assert plates3mf.whole_file_mesh(p) is None


def test_plate_parts_coloured_and_modifier_excluded(tmp_path: Path) -> None:
    path, plates, bed = _setup(tmp_path)
    meshes = plates3mf.plate_meshes(path, plates, bed)
    m1 = meshes[1]
    assert len(m1.faces) == 24  # modifier cube dropped
    cols = _by_colour(m1)
    assert set(cols) == {RED, BLUE}
    assert cols[RED][:, 0].max() < cols[BLUE][:, 0].min()  # part A left of part B
    assert m1.bounds[1][0] - m1.bounds[0][0] < 50  # modifier (x +50) not in extents


def test_colour_change_splits_at_z(tmp_path: Path) -> None:
    path, plates, bed = _setup(tmp_path)
    m2 = plates3mf.plate_meshes(path, plates, bed)[2]
    cols = _by_colour(m2)
    assert set(cols) == {GREEN, BLUE}
    assert cols[GREEN][:, 2].max() <= 10 < cols[BLUE][:, 2].min()
    # plate 1 has no change entry: unaffected by plate 2's change
    assert BLUE in _by_colour(plates3mf.plate_meshes(path, plates, bed)[1])


def test_colours_survive_glb_export(tmp_path: Path) -> None:
    path, plates, bed = _setup(tmp_path)
    m2 = plates3mf.plate_meshes(path, plates, bed)[2]
    out = tmp_path / "p2.glb"
    convert.to_y_up(m2).export(out, file_type="glb")
    g = trimesh.load(out, force="mesh")
    assert g.visual.kind == "vertex"
    got = {tuple(int(x) for x in c) for c in np.unique(g.visual.vertex_colors, axis=0)}
    assert got == {GREEN, BLUE}
    assert abs(g.bounds[1][1] - 20) < 1e-6  # Y-up


def test_whole_file_glb_coloured(tmp_path: Path) -> None:
    path, _, _ = _setup(tmp_path)
    dst = tmp_path / "whole.glb"
    convert.convert_to_glb_file(path, BlobFormat.THREEMF, dst)
    g = trimesh.load(dst, force="mesh")
    got = {tuple(int(x) for x in c) for c in np.unique(g.visual.vertex_colors, axis=0)}
    assert got == {RED, GREEN, BLUE}
    assert len(g.faces) >= 36  # 3 display cubes (plate 2's split at its change), modifier excluded


def test_bake_does_not_blend_shared_vertices() -> None:
    box = trimesh.creation.box((1, 1, 1))
    fc = np.tile(np.array(RED, dtype=np.uint8), (12, 1))
    fc[:6] = BLUE
    box.visual = trimesh.visual.ColorVisuals(box, face_colors=fc)
    baked = threemf_colour.bake_vertex_colours(box)
    assert {tuple(c) for c in np.unique(baked.visual.vertex_colors, axis=0)} == {RED, BLUE}
    assert len(baked.faces) == 12


def test_plate_filaments_and_colour_changes(tmp_path: Path) -> None:
    path, plates, _ = _setup(tmp_path)
    with zipfile.ZipFile(path) as zf:
        got = plates3mf.plate_filament_info(zf, plates)
    # the modifier part (extruder 2) prints with its own extruder, so it is listed
    assert got[1] == {
        "filaments": [
            {"slot": 1, "colour": "#FF0000"},
            {"slot": 2, "colour": "#00FF00"},
            {"slot": 3, "colour": "#0000FF"},
        ],
        "colour_changes": [],
        "pauses": [],
    }
    assert got[2]["filaments"] == [{"slot": 2, "colour": "#00FF00"}]
    assert got[2]["colour_changes"] == [
        {"z": 10.0, "slot": 3, "colour": "#0000FF", "kind": "colour"}
    ]


def test_plain_project_has_no_filament_data(tmp_path: Path) -> None:
    p = tmp_path / "plain.3mf"
    p.write_bytes(make_project_3mf(2))
    with zipfile.ZipFile(p) as zf:
        got = plates3mf.plate_filament_info(zf, plates3mf.parse_unsliced_plates(zf))
    assert got[1] == {"filaments": [], "colour_changes": [], "pauses": []}


def test_read_3mf_profile_meta(tmp_path: Path) -> None:
    p = tmp_path / "m.3mf"
    p.write_bytes(make_coloured_project_3mf(profile_meta=True))
    with zipfile.ZipFile(p) as zf:
        assert plates3mf.read_3mf_profile_meta(zf) == {
            "profile_id": 777,
            "title": "Fast profile",
            "design_id": "555",
            "design_title": "Benchy",
            "description": "0.2 layers",
            "author": "alice",
            "origin": "3mf",
        }
    p.write_bytes(make_coloured_project_3mf())
    with zipfile.ZipFile(p) as zf:
        assert plates3mf.read_3mf_profile_meta(zf) is None


def test_clean_meta_text() -> None:
    assert plates3mf.clean_meta_text("&lt;p&gt;no ams.&lt;/p&gt;") == "no ams."
    assert (
        plates3mf.clean_meta_text(
            "&amp;lt;p&amp;gt;a  &amp;amp; b&amp;lt;br/&amp;gt;c&amp;lt;/p&amp;gt;"
        )
        == "a & b\nc"
    )
    assert plates3mf.clean_meta_text("  ") is None
    # only real tag shapes are stripped; a bare "<" survives
    assert plates3mf.clean_meta_text("needs 5 < 10 and x<=3 <3") == "needs 5 < 10 and x<=3 <3"
    assert plates3mf.clean_meta_text("a<b>bold</b>") == "a bold"
    # paragraphs survive
    assert plates3mf.clean_meta_text("&lt;p&gt;one&lt;/p&gt;&lt;p&gt;two  2&lt;/p&gt;") == (
        "one\n\ntwo 2"
    )


def _plate_cols(tmp_path: Path, plate: int = 2, **kw) -> dict[tuple, np.ndarray]:
    path = tmp_path / "x.3mf"
    path.write_bytes(make_coloured_project_3mf(**kw))
    with zipfile.ZipFile(path) as zf:
        plates = plates3mf.parse_unsliced_plates(zf)
        bed = plates3mf.read_bed(zf)
    return _by_colour(plates3mf.plate_meshes(path, plates, bed)[plate])


def test_pause_does_not_repaint_and_is_listed(tmp_path: Path) -> None:
    layers = '<layer top_z="10" type="1" extruder="3" color="" extra="" gcode="M400 U1"/>'
    cols = _plate_cols(tmp_path, layers=layers)
    assert set(cols) == {GREEN}  # single colour all the way up
    p = tmp_path / "x.3mf"
    with zipfile.ZipFile(p) as zf:
        info = threemf_colour.read_colour_info(zf)
        got = plates3mf.plate_filament_info(zf, plates3mf.parse_unsliced_plates(zf))
    assert info is not None and not info.events[2]
    assert got[2]["colour_changes"] == []
    assert got[2]["pauses"] == [{"z": 10.0}]


def test_colour_change_with_explicit_color(tmp_path: Path) -> None:
    layers = '<layer top_z="10" type="0" extruder="1" color="#FFFF00" extra="" gcode="M600"/>'
    cols = _plate_cols(tmp_path, layers=layers)
    assert set(cols) == {GREEN, (255, 255, 0, 255)}


def test_colour_change_without_color_uses_extruder(tmp_path: Path) -> None:
    layers = '<layer top_z="10" type="0" extruder="1" color="" extra="" gcode="M600"/>'
    assert set(_plate_cols(tmp_path, layers=layers)) == {GREEN, RED}


def test_multi_extruder_only_repaints_that_extruders_parts(tmp_path: Path) -> None:
    # plate 1 has parts on extruders 1 and 3; change on extruder 3 -> only B repaints
    layers = '<layer top_z="10" type="0" extruder="3" color="#FFFF00" extra="" gcode="M600"/>'
    cols = _plate_cols(tmp_path, plate=1, plate1_layers=layers, plate1_mode="MultiExtruder")
    yellow = (255, 255, 0, 255)
    assert set(cols) == {RED, BLUE, yellow}
    assert cols[RED][:, 2].max() > 10  # extruder 1 part untouched up to the top
    assert cols[BLUE][:, 2].max() <= 10 < cols[yellow][:, 2].min()
    # a colour change is aimed at one extruder in MultiAsSingle too
    cols = _plate_cols(tmp_path, plate=1, plate1_layers=layers, plate1_mode="MultiAsSingle")
    assert cols[RED][:, 2].max() > 10 and cols[BLUE][:, 2].max() <= 10 < cols[yellow][:, 2].min()
    # SingleExtruder repaints everything
    cols = _plate_cols(tmp_path, plate=1, plate1_layers=layers, plate1_mode="SingleExtruder")
    assert cols[RED][:, 2].max() <= 10 and cols[BLUE][:, 2].max() <= 10


def test_tool_change_only_in_multi_as_single(tmp_path: Path) -> None:
    layers = '<layer top_z="10" type="2" extruder="1" color="" extra="" gcode="T1"/>'
    assert set(_plate_cols(tmp_path, layers=layers, mode="MultiAsSingle")) == {GREEN, RED}
    assert set(_plate_cols(tmp_path, layers=layers, mode="SingleExtruder")) == {GREEN}
    assert set(_plate_cols(tmp_path, layers=layers, mode="MultiExtruder")) == {GREEN}


def test_colour_change_aimed_at_other_extruder_is_dead_in_multi_as_single(tmp_path: Path) -> None:
    # plate 2's only part is on extruder 2; the change targets extruder 3
    layers = '<layer top_z="10" type="0" extruder="3" color="" extra="" gcode="M600"/>'
    assert set(_plate_cols(tmp_path, layers=layers, mode="MultiAsSingle")) == {GREEN}
    p = tmp_path / "x.3mf"
    with zipfile.ZipFile(p) as zf:
        info = threemf_colour.read_colour_info(zf)
        got = plates3mf.plate_filament_info(zf, plates3mf.parse_unsliced_plates(zf))
    assert info is not None and info.painting_changes(2, 2) == []
    assert got[2]["colour_changes"] == []  # never applied, so not listed
    # SingleExtruder repaints regardless of the target
    assert set(_plate_cols(tmp_path, layers=layers, mode="SingleExtruder")) == {GREEN, BLUE}


def test_dead_events_do_not_make_a_file_multicolour(tmp_path: Path) -> None:
    # one extruder everywhere, only a tool change that MultiExtruder never applies
    path, _, _ = _setup(tmp_path)
    with zipfile.ZipFile(path) as zf:
        info = threemf_colour.read_colour_info(zf)
    assert info is not None
    info.objects = {2: info.objects[2]}
    info.events = {2: [threemf_colour.LayerEvent(10.0, BLUE, 3, threemf_colour.TOOL_CHANGE)]}
    info.modes = {2: "MultiExtruder"}
    assert not info.is_multicolour
    info.modes = {2: "MultiAsSingle"}
    assert info.is_multicolour


def test_tool_change_lists_target_filament(tmp_path: Path) -> None:
    layers = '<layer top_z="10" type="2" extruder="3" color="" extra="" gcode="T2"/>'
    (tmp_path / "x.3mf").write_bytes(make_coloured_project_3mf(layers=layers, mode="MultiAsSingle"))
    with zipfile.ZipFile(tmp_path / "x.3mf") as zf:
        got = plates3mf.plate_filament_info(zf, plates3mf.parse_unsliced_plates(zf))
    assert [f["slot"] for f in got[2]["filaments"]] == [2, 3]
    assert got[2]["colour_changes"][0]["kind"] == "tool"


def test_untyped_layers_fall_back_to_gcode(tmp_path: Path) -> None:
    def kinds(gcode: str) -> list:
        layers = f'<layer top_z="10" extruder="1" color="#FFFF00" extra="" gcode="{gcode}"/>'
        (tmp_path / "x.3mf").write_bytes(make_coloured_project_3mf(layers=layers))
        with zipfile.ZipFile(tmp_path / "x.3mf") as zf:
            info = threemf_colour.read_colour_info(zf)
        assert info is not None
        return [e.kind for e in info.events[2]], info.pauses[2]

    assert kinds("M600") == ([threemf_colour.COLOUR_CHANGE], [])
    assert kinds("M601") == ([], [10.0])
    assert kinds("M400 U1") == ([], [10.0])
    assert kinds("tool_change")[0] == [threemf_colour.TOOL_CHANGE]
    assert kinds("G92 E0") == ([], [])


def test_faces_straddling_a_change_are_split(tmp_path: Path) -> None:
    layers = '<layer top_z="7" type="0" extruder="2" color="#FFFF00" extra="" gcode="M600"/>'
    cols = _plate_cols(tmp_path, layers=layers)
    yellow = (255, 255, 0, 255)
    assert cols[GREEN][:, 2].max() <= 7 < cols[yellow][:, 2].min()
    mesh = plates3mf.plate_meshes(
        tmp_path / "x.3mf",
        plates3mf.parse_unsliced_plates(zipfile.ZipFile(tmp_path / "x.3mf")),
    )[2]
    tz = mesh.vertices[mesh.faces][:, :, 2]
    assert not ((tz.min(axis=1) < 7 - 1e-6) & (tz.max(axis=1) > 7 + 1e-6)).any()
    assert abs(mesh.area - 6 * 400) < 1e-6  # splitting preserves the surface


def test_duplicate_plater_id_renumbers_by_position(tmp_path: Path) -> None:
    src = make_coloured_project_3mf()
    out = tmp_path / "dup.3mf"
    with zipfile.ZipFile(io.BytesIO(src)) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == threemf_colour.MODEL_SETTINGS:
                data = data.replace(b'key="plater_id" value="2"', b'key="plater_id" value="1"')
            zout.writestr(item.filename, data)
    with zipfile.ZipFile(out) as zf:
        plates = plates3mf.parse_unsliced_plates(zf)
        info = threemf_colour.read_colour_info(zf)
    assert [(p["index"], p["object_ids"]) for p in plates] == [(1, [1]), (2, [2])]
    assert info is not None and info.plate_objects == {1: [1], 2: [2]}


def test_item_claimed_twice_and_bad_instance_are_skipped() -> None:
    far = (np.full(3, 1e6), np.full(3, 1e6 + 1))  # on no grid cell: dropped when unclaimed
    bounds = [far, far]
    plates = [
        {"index": 1, "object_ids": [1, 1], "instance_ids": [0, 0]},  # same item twice
        {"index": 2, "object_ids": [2], "instance_ids": [5]},  # no such instance
    ]
    got = plates3mf._assign_items(bounds, [1, 2], plates, (180.0, 180.0))
    assert got[1] == [0]
    assert got[2] == []  # not silently pulled in via slots[0]


def test_single_colour_file_still_drops_modifier_parts(tmp_path: Path) -> None:
    src = make_coloured_project_3mf()
    out = tmp_path / "mono.3mf"
    with zipfile.ZipFile(io.BytesIO(src)) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == threemf_colour.PROJECT_SETTINGS:
                data = data.replace(b'"#FF0000", "#00FF00", "#0000FF"', b'"#FF0000"')
                data = data.replace(b'["#FF0000","#00FF00","#0000FF"]', b'["#FF0000"]')
            if item.filename == threemf_colour.GCODE_PER_LAYER:
                continue
            if item.filename == threemf_colour.MODEL_SETTINGS:
                data = data.replace(b'value="2"/></part>', b'value="1"/></part>')
                data = data.replace(b'value="3"/></part>', b'value="1"/></part>')
            zout.writestr(item.filename, data)
    with zipfile.ZipFile(out) as zf:
        info = threemf_colour.read_colour_info(zf)
    assert info is not None and not info.is_multicolour
    dst = tmp_path / "mono.glb"
    convert.convert_to_glb_file(out, BlobFormat.THREEMF, dst)
    g = trimesh.load(dst, force="mesh")
    assert len(g.faces) == 36  # modifier cube dropped even though nothing is painted
    assert g.visual.kind != "vertex" or len(np.unique(g.visual.vertex_colors, axis=0)) <= 1


def test_decode_paint_state() -> None:
    d = threemf_colour.decode_paint_state
    assert d("") == 0 and d("0") == 0 and d("4") == 1 and d("8") == 2
    assert d("0C") == 3 and d("1C") == 4 and d("zz") == 0
    # extended states: runs of 0xF nibbles add 15 each (stream is stored back to front)
    assert d("EC") == 17 and d("0FC") == 18 and d("1FC") == 19 and d("0FFC") == 33
    # split in two (1 split side): leaves state 1 and state 2 -> tie goes to the first
    assert d("841") == 1
    # split in two where the second child is split again into leaves 2, 2: majority is 2
    assert d("88" + "1" + "4" + "1") != 0


def _paint_plate2(tmp_path: Path, paint: dict[int, str], **kw):
    path = tmp_path / "paint.3mf"
    path.write_bytes(make_coloured_project_3mf(paint={21: paint}, **kw))
    with zipfile.ZipFile(path) as zf:
        plates = plates3mf.parse_unsliced_plates(zf)
        bed = plates3mf.read_bed(zf)
        info = threemf_colour.read_colour_info(zf)
        fil = plates3mf.plate_filament_info(zf, plates)
    return path, plates, bed, info, fil


def test_paint_color_paints_triangles_and_counts_extruders(tmp_path: Path) -> None:
    # cube triangles 0-3 are the bottom/top caps; paint them extruder 1 (red) and 3 (blue)
    path, plates, bed, info, fil = _paint_plate2(
        tmp_path, {0: "4", 1: "4", 2: "0C", 3: "0C"}, layers=""
    )
    assert info is not None and info.paint[(2, 0)].tolist()[:4] == [1, 1, 3, 3]
    assert info.painted_extruders(2) == {1, 3}
    assert [f["slot"] for f in fil[2]["filaments"]] == [1, 2, 3]
    cols = _by_colour(plates3mf.plate_meshes(path, plates, bed)[2])
    assert set(cols) == {RED, GREEN, BLUE}
    assert len(cols[RED]) == 2 and len(cols[BLUE]) == 2 and len(cols[GREEN]) == 8


def test_paint_color_gets_colour_changes_of_its_own_extruder(tmp_path: Path) -> None:
    layers = '<layer top_z="10" type="0" extruder="1" color="#FFFF00" extra="" gcode="M600"/>'
    path, plates, bed, _, _ = _paint_plate2(
        tmp_path, {i: "4" for i in range(12)}, layers=layers, mode="MultiExtruder"
    )
    cols = _by_colour(plates3mf.plate_meshes(path, plates, bed)[2])
    # every face is painted extruder 1, so the extruder-1 change repaints the upper half
    assert set(cols) == {RED, (255, 255, 0, 255)}


def test_paint_color_scan_is_skipped_without_attribute(tmp_path: Path) -> None:
    path, _, _ = _setup(tmp_path)
    with zipfile.ZipFile(path) as zf:
        assert not threemf_colour.has_paint(zf)
        assert threemf_colour.read_paint(zf) == {}
