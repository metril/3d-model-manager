# ruff: noqa: E501
"""Hand-built Bambu/Orca-style project 3MF zips for plate tests (Production
Extension layout: root model -> components with ``p:path`` -> per-object
sub-models; plates mapped in ``Metadata/model_settings.config``)."""

from __future__ import annotations

import io
import json
import math
import zipfile

_NS = (
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'
)
_PNG = b"\x89PNG\r\n\x1a\n-plate-thumb-"


def _cube(size: float) -> str:
    s = size
    verts = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0), (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    tris = [
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
        (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    ]  # fmt: skip
    v = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in verts)
    t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
    return f"<mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh>"


def grid_origin(i: int, n: int, bed: float) -> tuple[float, float]:
    cols = math.ceil(math.sqrt(n))
    row, col = divmod(i, cols)
    return col * bed * 1.2, -row * bed * 1.2


def make_project_3mf(
    n_plates: int = 3,
    *,
    bed: float | None = 180.0,
    cube: float = 20.0,
    on_grid: bool = True,
    names: list[str] | None = None,
    extra_unassigned: bool = False,
    extra_cell: int | None = 0,
    thumbs: bool = True,
) -> bytes:
    """``n_plates`` plates, one ``cube``-mm cube each. ``on_grid`` places each
    cube mid-bed on its Bambu virtual-grid plate; otherwise at a far-off spot
    (to exercise the bbox-centre fallback). ``extra_unassigned`` adds an
    object in the build that no plate lists."""
    n_obj = n_plates + (1 if extra_unassigned else 0)
    bed_for_layout = bed or 256.0
    resources = []
    items = []
    sub_rels = []
    files: dict[str, str] = {}
    for k in range(n_obj):
        oid = k + 1
        sub_id = 100 + oid
        path = f"/3D/Objects/object_{oid}.model"
        files[path.lstrip("/")] = (
            f'<?xml version="1.0"?><model unit="millimeter" {_NS}><resources>'
            f'<object id="{sub_id}" type="model">{_cube(cube)}</object></resources><build/></model>'
        )
        sub_rels.append(
            f'<Relationship Target="{path}" Id="rel{oid}" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        )
        resources.append(
            f'<object id="{oid}" type="model"><components>'
            f'<component p:path="{path}" objectid="{sub_id}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
            "</components></object>"
        )
        if on_grid and k < n_plates:
            ox, oy = grid_origin(k, n_plates, bed_for_layout)
            tx, ty = ox + bed_for_layout / 2 - cube / 2, oy + bed_for_layout / 2 - cube / 2
        elif k >= n_plates and extra_cell is not None:
            ox, oy = grid_origin(extra_cell, n_plates, bed_for_layout)
            tx, ty = ox + bed_for_layout / 2 - cube / 2, oy + bed_for_layout / 2 - cube / 2
        else:
            tx, ty = 1000.0 + 50 * k, -900.0
        items.append(f'<item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 {tx} {ty} 0"/>')

    root = (
        f'<?xml version="1.0"?><model unit="millimeter" {_NS}><resources>{"".join(resources)}'
        f"</resources><build>{''.join(items)}</build></model>"
    )
    plates_xml = ""
    for k in range(n_plates):
        name = (names[k] if names else "") or ""
        plates_xml += (
            f'<plate><metadata key="plater_id" value="{k + 1}"/>'
            f'<metadata key="plater_name" value="{name}"/>'
            f'<model_instance><metadata key="object_id" value="{k + 1}"/>'
            '<metadata key="instance_id" value="0"/></model_instance></plate>'
        )
    model_settings = f"<config>{plates_xml}</config>"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
            '<Default Extension="png" ContentType="image/png"/></Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>',
        )
        zf.writestr(
            "3D/_rels/3dmodel.model.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(sub_rels)
            + "</Relationships>",
        )
        zf.writestr("3D/3dmodel.model", root)
        for name, body in files.items():
            zf.writestr(name, body)
        zf.writestr("Metadata/model_settings.config", model_settings)
        if bed is not None:
            area = ["0x0", f"{bed}x0", f"{bed}x{bed}", f"0x{bed}"]
            zf.writestr("Metadata/project_settings.config", json.dumps({"printable_area": area}))
        if thumbs:
            for k in range(n_plates):
                zf.writestr(f"Metadata/plate_{k + 1}.png", _PNG + bytes([k]))
    return buf.getvalue()


def make_coloured_project_3mf(*, profile_meta: bool = False) -> bytes:
    """Two plates (bed 180). Plate 1: object 1 = parts [A extruder 1, B extruder
    3, M modifier extruder 2] (20 mm cubes in a row). Plate 2: object 2 = one
    20 mm cube on extruder 2 with a colour change to extruder 3 at top_z=10."""
    bed = 180.0
    sub = {
        "3D/Objects/object_1.model": [(11, 0.0), (12, 25.0), (13, 50.0)],
        "3D/Objects/object_2.model": [(21, 0.0)],
    }
    files: dict[str, str] = {}
    rels = []
    for n, (path, parts) in enumerate(sub.items(), start=1):
        objs = "".join(
            f'<object id="{oid}" type="model">{_cube(20.0)}</object>' for oid, _ in parts
        )
        files[path] = (
            f'<?xml version="1.0"?><model unit="millimeter" {_NS}><resources>{objs}'
            "</resources><build/></model>"
        )
        rels.append(
            f'<Relationship Target="/{path}" Id="rel{n}" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        )
    comps1 = "".join(
        f'<component p:path="/3D/Objects/object_1.model" objectid="{oid}" '
        f'transform="1 0 0 0 1 0 0 0 1 {dx} 0 0"/>'
        for oid, dx in sub["3D/Objects/object_1.model"]
    )
    comps2 = (
        '<component p:path="/3D/Objects/object_2.model" objectid="21" '
        'transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
    )
    ox2, oy2 = grid_origin(1, 2, bed)
    meta_xml = (
        "".join(
            f'<metadata name="{k}">{v}</metadata>'
            for k, v in {
                "DesignModelId": "555",
                "DesignProfileId": "777",
                "ProfileTitle": "Fast profile",
                "ProfileDescription": "0.2 layers",
                "ProfileUserName": "alice",
                "Title": "Benchy",
                "Designer": "bob",
            }.items()
        )
        if profile_meta
        else ""
    )
    root = (
        f'<?xml version="1.0"?><model unit="millimeter" {_NS}>{meta_xml}<resources>'
        f'<object id="1" type="model"><components>{comps1}</components></object>'
        f'<object id="2" type="model"><components>{comps2}</components></object>'
        "</resources><build>"
        '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 40 80 0"/>'
        f'<item objectid="2" transform="1 0 0 0 1 0 0 0 1 {ox2 + 80} {oy2 + 80} 0"/>'
        "</build></model>"
    )

    def part(pid: int, ext: int, subtype: str) -> str:
        return (
            f'<part id="{pid}" subtype="{subtype}"><metadata key="extruder" value="{ext}"/></part>'
        )

    def plate(idx: int, oid: int) -> str:
        return (
            f'<plate><metadata key="plater_id" value="{idx}"/><metadata key="plater_name" value=""/>'
            f'<model_instance><metadata key="object_id" value="{oid}"/>'
            '<metadata key="instance_id" value="0"/></model_instance></plate>'
        )

    model_settings = (
        '<config><object id="1"><metadata key="extruder" value="1"/>'
        + part(11, 1, "normal_part")
        + part(12, 3, "normal_part")
        + part(13, 2, "modifier_part")
        + '</object><object id="2"><metadata key="extruder" value="2"/>'
        + part(21, 2, "normal_part")
        + "</object>"
        + plate(1, 1)
        + plate(2, 2)
        + "</config>"
    )
    settings = {
        "printable_area": ["0x0", f"{bed}x0", f"{bed}x{bed}", f"0x{bed}"],
        "filament_colour": ["#FF0000", "#00FF00", "#0000FF"],
    }
    gcodes = (
        '<?xml version="1.0"?><custom_gcodes_per_layer><plate><plate_info id="2"/>'
        '<layer top_z="10" type="1" extruder="3" color="" extra="" gcode="M400 U1"/>'
        '<mode value="MultiAsSingle"/></plate></custom_gcodes_per_layer>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>',
        )
        zf.writestr(
            "3D/_rels/3dmodel.model.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>",
        )
        zf.writestr("3D/3dmodel.model", root)
        for name, body in files.items():
            zf.writestr(name, body)
        zf.writestr("Metadata/model_settings.config", model_settings)
        zf.writestr("Metadata/project_settings.config", json.dumps(settings))
        zf.writestr("Metadata/custom_gcode_per_layer.xml", gcodes)
    return buf.getvalue()
