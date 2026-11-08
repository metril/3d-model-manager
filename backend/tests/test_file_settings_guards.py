"""Safety rails around slicing with a 3MF's own settings and auto-arrange."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.models import Printer  # noqa: F401  (model registry)
from app.models.enums import BlobFormat, BlobKind, QueueState
from app.models.library import Model, PrintQueueEntry, Revision
from app.models.slicing import SlicerProfile
from app.pipeline import plates3mf
from app.services.slicer_client import SlicerError
from app.tasks import base, slicing
from tests.server_helpers import set_server
from tests.test_slice_task import (  # noqa: F401
    FakeSlicerClient,
    _seed,
    _verify_source_blob,
    _write_project_3mf,
)


def _zip(members: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _cfg(**kw) -> str:
    return json.dumps({"layer_height": "0.2", "nozzle_diameter": ["0.4"], **kw})


# --- 1. embedded -1 values -------------------------------------------------


def test_clamp_project_settings_drops_below_range_keys_and_keeps_the_rest(tmp_path: Path):
    p = tmp_path / "m.3mf"
    p.write_bytes(
        _zip(
            {
                "Metadata/project_settings.config": _cfg(
                    tree_support_wall_count="-1", raft_first_layer_expansion="-1"
                ),
                "3D/3dmodel.model": "<model/>",
            }
        )
    )
    from app.services.profile_sanitize import KNOWN_RANGES

    changed = plates3mf.clamp_project_settings(p, KNOWN_RANGES)
    assert sorted(changed) == ["raft_first_layer_expansion", "tree_support_wall_count"]
    with zipfile.ZipFile(p) as zf:
        cfg = json.loads(zf.read("Metadata/project_settings.config"))
        assert zf.read("3D/3dmodel.model") == b"<model/>"
    assert "tree_support_wall_count" not in cfg and cfg["layer_height"] == "0.2"
    assert plates3mf.clamp_project_settings(p, KNOWN_RANGES) == []


class _RangeThenOk(FakeSlicerClient):
    """422 'not in range' for each bad key found in the 3MF, like Orca."""

    def slice(self, **kw):
        self.calls += 1
        self.seen = kw
        with zipfile.ZipFile(kw["model"]) as zf:
            cfg = json.loads(zf.read("Metadata/project_settings.config"))
        for key in ("some_key_a", "some_key_b"):
            if key in cfg:
                raise SlicerError(
                    f"slice_failed: slicer exited 238: x\n{key}: -1 not in range [0.0, 5.0]", 422
                )
        return self.result


def test_range_error_in_the_3mf_is_clamped_in_its_copy_and_retried(tmp_path: Path):
    src = tmp_path / "m.3mf"
    _write_project_3mf(
        src, {"some_key_a": "-1", "some_key_b": "-1", "tree_support_wall_count": "-1"}
    )
    fake = _RangeThenOk(result=b"ok")
    out = slicing._slice_with_sanitize(
        fake,
        printer_profile={},
        process_profile=None,
        filament_profiles=[],
        sanitize_model=src,
        model=src,
        filename="m.3mf",
    )
    assert out == b"ok" and fake.calls == 3
    with zipfile.ZipFile(src) as zf:
        cfg = json.loads(zf.read("Metadata/project_settings.config"))
    assert not {"some_key_a", "some_key_b", "tree_support_wall_count"} & cfg.keys()


# --- 4/10. permanence ------------------------------------------------------


def test_sidecar_400_is_permanent_and_orca_error_text_matches_exit_regex():
    assert slicing._is_permanent(SlicerError("bad_request: x", 400))
    assert slicing._is_permanent(SlicerError("slice_failed: slicer exited -24: Unsupported", 422))
    assert not slicing._is_permanent(SlicerError("slice_failed: slicer exited 139: boom", 422))
    assert not slicing._is_permanent(SlicerError("down", 503))


# --- 3. file settings need a 3MF with project settings ----------------------


@pytest.mark.usefixtures("migrated_db")
def test_file_settings_on_a_non_project_source_fails_permanently(monkeypatch: pytest.MonkeyPatch):
    with base.sync_session() as session:
        entry = _seed(session)
        entry.slice_options = {"file_settings": True}
        session.commit()
        entry_id = entry.id
    fake = FakeSlicerClient(result=b"x")
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake)
    slicing.slice_file(entry_id)  # source bytes are not a zip -> no embedded settings
    assert fake.calls == 0
    with base.sync_session() as session:
        e = session.get(PrintQueueEntry, entry_id)
        assert e.state == QueueState.FAILED and e.attempts <= 1


# --- 2. nozzle mismatch -----------------------------------------------------


def test_unusable_reason_flags_nozzle_mismatch():
    from app.services.file_settings import unusable_reason

    printer = SlicerProfile(name="A1M 0.8", nozzle_mm=0.8)
    assert "nozzle" in unusable_reason({"nozzle_mm": 0.4}, printer)
    assert unusable_reason({"nozzle_mm": 0.8}, printer) is None
    assert unusable_reason({"nozzle_mm": None}, printer) is None
    assert unusable_reason(None, printer).startswith("file_settings_unavailable")


async def _file_for(db, seed_file, slug, name, data: bytes, fmt: BlobFormat):
    model = Model(slug=slug, name=slug)
    db.add(model)
    await db.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db.add(rev)
    await db.commit()
    await db.refresh(rev)
    return await seed_file(model, rev, name, data, blob_format=fmt, blob_kind=BlobKind.MESH)


async def _recipe_for(db, nozzle: float):
    from app.models.enums import ProfileKind
    from app.models.slicing import PrintRecipe

    pp = SlicerProfile(
        name=f"p{nozzle}",
        kind=ProfileKind.PRINTER,
        printer_model="Bambu Lab A1 mini",
        nozzle_mm=nozzle,
        data={},
    )
    pr = SlicerProfile(name=f"q{nozzle}", kind=ProfileKind.PROCESS, data={})
    db.add_all([pp, pr])
    await db.flush()
    r = PrintRecipe(
        name=f"r{nozzle}",
        printer_profile_id=pp.id,
        process_profile_id=pr.id,
        filament_profile_ids=[None],
        bed_type="textured_plate",
        options={},
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def test_api_rejects_file_settings_for_stl_and_nozzle_mismatch(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    recipe = await _recipe_for(db_session, 0.4)
    stl = await _file_for(db_session, seed_file, "fs-stl", "a.stl", b"stl", BlobFormat.STL)
    bare = await _file_for(
        db_session,
        seed_file,
        "fs-bare",
        "a.3mf",
        _zip({"3D/3dmodel.model": "x"}),
        BlobFormat.THREEMF,
    )
    wide = await _file_for(
        db_session,
        seed_file,
        "fs-wide",
        "w.3mf",
        _zip({"Metadata/project_settings.config": _cfg(nozzle_diameter=["0.8"])}),
        BlobFormat.THREEMF,
    )
    body = {"recipe_id": recipe.id, "file_settings": True}
    for f, code in (
        (stl, "file_settings_unavailable"),
        (bare, "file_settings_unavailable"),
        (wide, "file_settings_nozzle_mismatch"),
    ):
        r = await authenticated_client.post(f"/api/files/{f.id}/slice", json=body)
        assert r.status_code == 422 and code in r.json()["detail"], (f.rel_path, r.text)
        r = await authenticated_client.post(
            "/api/queue", json={"file_id": f.id, "recipe_id": recipe.id, "file_settings": True}
        )
        assert r.status_code == 422 and code in r.json()["detail"], (f.rel_path, r.text)


# --- 5/6. crafted project_settings never 500 / blow memory --------------------


def test_crafted_embedded_values_are_dropped_not_raised():
    zf = zipfile.ZipFile(
        io.BytesIO(
            _zip(
                {
                    "Metadata/project_settings.config": json.dumps(
                        {
                            "print_settings_id": 123,
                            "printer_settings_id": ["x"],
                            "layer_height": "nan",
                            "nozzle_diameter": ["inf"],
                        }
                    )
                }
            )
        )
    )
    assert plates3mf.read_embedded_process(zf) == {
        "layer_height": None,
        "nozzle_mm": None,
        "print_settings_id": None,
        "printer_settings_id": None,
    }


def test_oversized_project_settings_member_is_refused():
    big = _cfg(pad="x" * (plates3mf.MAX_PROJECT_SETTINGS_BYTES + 10))
    zf = zipfile.ZipFile(io.BytesIO(_zip({"Metadata/project_settings.config": big})))
    assert plates3mf.read_embedded_process(zf) is None


async def test_file_lookup_survives_crafted_settings(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    f = await _file_for(
        db_session,
        seed_file,
        "fs-craft",
        "c.3mf",
        _zip({"Metadata/project_settings.config": _cfg(print_settings_id=123, layer_height="nan")}),
        BlobFormat.THREEMF,
    )
    r = await authenticated_client.get(f"/api/files/{f.id}")
    assert r.status_code == 200, r.text
    assert r.json()["embedded"]["layer_height"] is None
    assert r.json()["embedded"]["print_settings_id"] is None


async def test_embedded_lookup_is_cached_by_blob_hash(
    authenticated_client: httpx.AsyncClient, db_session, seed_file, monkeypatch
) -> None:
    from app.services import file_settings

    f = await _file_for(
        db_session,
        seed_file,
        "fs-cache",
        "k.3mf",
        _zip({"Metadata/project_settings.config": _cfg()}),
        BlobFormat.THREEMF,
    )
    calls = []
    real = file_settings._read_ranged
    monkeypatch.setattr(file_settings, "_read_ranged", lambda b, p: calls.append(1) or real(b, p))
    file_settings._cache.clear()
    for _ in range(3):
        assert (await authenticated_client.get(f"/api/files/{f.id}")).status_code == 200
    assert len(calls) == 1


# --- 7. siblings ------------------------------------------------------------


@pytest.mark.usefixtures("migrated_db")
def test_slice_siblings_require_equal_file_settings():
    from app.models.library import PrintBatch
    from app.services.dispatcher import slice_siblings

    with base.sync_session() as session:
        first = _seed(session, plate=1)
        batch = PrintBatch(name="b", model_id=first.model_id, quantity=3, status="active")
        session.add(batch)
        session.flush()

        def mk(pos, plate, opts):
            e = PrintQueueEntry(
                model_id=first.model_id,
                position=pos,
                file_id=first.file_id,
                recipe_id=first.recipe_id,
                plate=plate,
                state=QueueState.SLICING,
                batch_id=batch.id,
                slice_options=opts,
            )
            session.add(e)
            return e

        first.batch_id = batch.id
        first.slice_options = {"all_plates": True, "file_settings": True}
        same = mk(2, 2, {"all_plates": True, "file_settings": True})
        other = mk(3, 3, {"all_plates": True})
        session.commit()
        ids = {e.id for e in slice_siblings(session, first)}
        assert ids == {same.id} and other.id not in ids


# --- 11. arrange spill ------------------------------------------------------


def _model_settings(plates: dict[int, int]) -> str:
    out = ["<config>"]
    oid = 1
    for idx, n in plates.items():
        out.append(f'<plate><metadata key="plater_id" value="{idx}"/>')
        for _ in range(n):
            out.append(
                f'<model_instance><metadata key="object_id" value="{oid}"/>'
                '<metadata key="instance_id" value="0"/></model_instance>'
            )
            oid += 1
        out.append("</plate>")
    out.append("</config>")
    return "".join(out)


def _slice_info(objs: int) -> str:
    return "<config><plate>" + "<object/>" * objs + "</plate></config>"


class _Meta:
    def __init__(self, n: int):
        self.plates = [{"index": i + 1} for i in range(n)]


def test_arrange_spill_onto_more_plates_fails_clearly(tmp_path: Path):
    src = tmp_path / "s.3mf"
    src.write_bytes(_zip({"Metadata/model_settings.config": _model_settings({1: 2})}))
    out = tmp_path / "o.3mf"
    out.write_bytes(_zip({"Metadata/slice_info.config": _slice_info(1)}))
    with pytest.raises(ValueError, match="spread the parts over 2 plates"):
        slicing._guard_arrange_spill(src, out, _Meta(2), None)
    slicing._guard_arrange_spill(src, out, _Meta(1), None)  # nothing spilled


def test_arrange_dropping_parts_from_the_sliced_plate_fails_clearly(tmp_path: Path):
    src = tmp_path / "s.3mf"
    src.write_bytes(_zip({"Metadata/model_settings.config": _model_settings({1: 3, 2: 1})}))
    out = tmp_path / "o.3mf"
    out.write_bytes(_zip({"Metadata/slice_info.config": _slice_info(2)}))
    with pytest.raises(ValueError, match="could not fit all 3 parts of plate 1"):
        slicing._guard_arrange_spill(src, out, _Meta(1), 1)
    out.write_bytes(_zip({"Metadata/slice_info.config": _slice_info(3)}))
    slicing._guard_arrange_spill(src, out, _Meta(1), 1)
