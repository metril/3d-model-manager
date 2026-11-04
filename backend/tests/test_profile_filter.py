from types import SimpleNamespace as NS

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.importers import makerworld
from app.importers.base import ImportFile
from app.importers.makerworld import MakerWorldImporter
from app.importers.profile_filter import filter_profiles
from tests.cassettes import makerworld_fixtures as fx
from tests.test_makerworld_importer import _mock_design_client


def _f(rid, product, code, others=(), nozzle=0.4):
    compat = [{"model": code, "product": product, "nozzle": nozzle}]
    compat += [{"model": c, "product": p, "nozzle": nozzle} for c, p in others]
    return ImportFile(remote_id=rid, filename=f"{rid}.zip", meta={"compat": compat})


def _p(model, nozzle=None, enabled=True):
    return NS(model=model, nozzle_mm=nozzle, enabled=enabled)


def test_all_mode_keeps_everything():
    files = [_f("a", "P1S", "C12")]
    assert filter_profiles(files, [_p("A1")], "all") == files


def test_primary_match_case_space_insensitive():
    a, b = _f("a", "P1S", "C12"), _f("b", "X1 Carbon", "BL-P001")
    assert filter_profiles([a, b], [_p("p1s")], "owned") == [a]
    assert filter_profiles([a, b], [_p("x1_carbon")], "owned") == [b]
    assert filter_profiles([a, b], [_p("X1C")], "owned") == [b]


def test_dev_code_and_other_compat():
    a = _f("a", "P1S", "C12", others=[("N1", "A1 mini")])
    b = _f("b", "P1S", "C12")
    assert filter_profiles([a, b], [_p("A1 Mini")], "owned") == [a]
    assert filter_profiles([a, b], [_p("N1")], "owned") == [a]


def test_a1_does_not_match_a1_mini():
    a = _f("a", "A1 mini", "N1")
    b = _f("b", "A1", "N2S")
    assert filter_profiles([a, b], [_p("A1")], "owned") == [b]


def test_nozzle_checked_when_both_known():
    a, b = _f("a", "A1", "N2S", nozzle=0.4), _f("b", "A1", "N2S", nozzle=0.6)
    assert filter_profiles([a, b], [_p("A1", 0.4)], "owned") == [a]
    assert filter_profiles([a, b], [_p("A1", None)], "owned") == [a, b]


def test_zero_match_keeps_all():
    files = [_f("a", "P1S", "C12"), _f("b", "X1E", "C13")]
    assert filter_profiles(files, [_p("H2D")], "owned") == files


def test_disabled_and_modelless_printers_ignored():
    files = [_f("a", "P1S", "C12"), _f("b", "A1", "N2S")]
    assert filter_profiles(files, [_p("P1S", enabled=False)], "owned") == files
    assert filter_profiles(files, [_p(None)], "owned") == files
    assert filter_profiles(files, [_p("P1S", enabled=False), _p("A1")], "owned") == [files[1]]


def test_files_without_meta_are_kept():
    raw = ImportFile(remote_id="raw", filename="raw.zip")
    a = _f("a", "P1S", "C12")
    assert filter_profiles([raw, a], [_p("P1S")], "owned") == [raw, a]


def test_list_files_captures_profile_meta(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: ("t", "global"))
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(fx.DESIGN_137041))
    files = MakerWorldImporter().list_files("137041")
    assert len(files) == 3
    m = files[0].meta
    assert m["compat"][0] == {"model": "C12", "product": "P1S", "nozzle": 0.4}
    assert any(c["product"] == "A1 mini" for c in m["compat"])
    assert len(m["plates"]) == 3
    assert m["plates"][0]["thumb"].endswith("plate_1.png")
    assert m["plates"][0]["prediction"] and m["plates"][0]["weight"]
    # the 3rd profile (Box for couplers) lacks A1 mini in real data
    kept = filter_profiles(files, [_p("A1 Mini")], "owned")
    assert [f.meta["instance_id"] for f in kept] == [151331, 151334]


def test_list_files_tolerates_missing_extention(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: ("t", "global"))
    design = {
        **fx.DESIGN_137041,
        "instances": [
            {"id": 1, "profileId": 2, "hasZipStl": True, "extention": None},
            {
                "id": 3,
                "profileId": 4,
                "hasZipStl": True,
                "extention": {"modelInfo": {"plates": "x"}},
            },
        ],
    }
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(design))
    files = MakerWorldImporter().list_files("137041")
    assert [f.meta["compat"] for f in files] == [[], []]


def test_alembic_single_head():
    cfg = Config("alembic.ini")
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
