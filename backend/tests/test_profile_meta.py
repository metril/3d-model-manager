"""Profile scope metadata: `model_key` aliases, `derive_profile_meta`,
`profile_in_scope`, the scope resolver and the startup backfill."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import SlicerProfile
from app.models.enums import ProfileKind
from app.services.profile_scope import resolve_nozzle
from app.services.slicer_profiles import (
    backfill_profile_meta,
    derive_profile_meta,
    model_key,
    parse_bundle,
    profile_in_scope,
)
from app.tasks import base
from tests.slicer_fixtures import write_bundle

P, F, M = ProfileKind.PROCESS, ProfileKind.FILAMENT, ProfileKind.PRINTER


# --- model_key -------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["A1 mini", "Bambu Lab A1 mini", "A1M", "N1", "a1-mini", "BambuLab A1mini"]
)
def test_a1_mini_spellings_share_a_key(text):
    assert model_key(text) == "a1mini"


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("N2S", "a1"),
        ("A1", "a1"),
        ("C12", "p1s"),
        ("P1S", "p1s"),
        ("C11", "p1p"),
        ("X1C", "x1carbon"),
        ("BL-P001", "x1carbon"),
        ("X1 Carbon", "x1carbon"),
        ("BL-P002", "x1"),
        ("O1D", "h2d"),
        ("N7", "p2s"),
    ],
)
def test_alias_table(text, key):
    assert model_key(text) == key


def test_model_key_blank_and_distinct():
    assert model_key(None) is None and model_key("  ") is None
    assert model_key("A1") != model_key("A1 mini")  # key equality, never substring


# --- derive_profile_meta ---------------------------------------------------


def test_compatible_printers_parse_with_nozzles():
    meta = derive_profile_meta(
        P,
        {
            "compatible_printers": [
                "Bambu Lab A1 mini 0.4 nozzle",
                "Bambu Lab A1 mini 0.2 nozzle",
            ],
            "layer_height": "0.2",
        },
        name="0.20mm Standard @BBL A1M",
    )
    assert meta.compat == [
        {"model_key": "a1mini", "nozzle_mm": 0.4},
        {"model_key": "a1mini", "nozzle_mm": 0.2},
    ]
    assert meta.printer_model_key == "a1mini"
    assert meta.layer_height_mm == 0.2


def test_mixed_models_keep_exact_compat_and_null_single_key():
    meta = derive_profile_meta(
        P,
        {"compatible_printers": ["Bambu Lab A1 mini 0.4 nozzle", "Bambu Lab P1S 0.4 nozzle"]},
        name="x",
    )
    assert meta.printer_model_key is None
    assert {c["model_key"] for c in meta.compat} == {"a1mini", "p1s"}
    row = SimpleNamespace(kind=P, compat=meta.compat, printer_model_key=None, layer_height_mm=None)
    assert profile_in_scope(row, "a1mini", 0.4)
    assert profile_in_scope(row, "p1s", 0.4)
    assert not profile_in_scope(row, "x1carbon", 0.4)


def test_unparseable_entry_becomes_model_key_any_nozzle():
    meta = derive_profile_meta(F, {"compatible_printers": ["Bambu Lab X1 Carbon"]}, name="f")
    assert meta.compat == [{"model_key": "x1carbon", "nozzle_mm": None}]


def test_machine_lookup_resolves_entry():
    meta = derive_profile_meta(
        F,
        {"compatible_printers": ["My Custom Printer"]},
        name="f",
        machine_lookup=lambda n: ("Bambu Lab A1", 0.6),
    )
    assert meta.compat == [{"model_key": "a1", "nozzle_mm": 0.6}]


def test_printer_kind_one_entry_from_data():
    meta = derive_profile_meta(
        M, {"printer_model": "N1", "nozzle_diameter": ["0.4"]}, name="Bambu Lab A1 mini 0.4 nozzle"
    )
    assert meta.compat == [{"model_key": "a1mini", "nozzle_mm": 0.4}]
    assert meta.layer_height_mm is None


def test_fallback_inherits_parent_compat():
    parent = [{"model_key": "p1s", "nozzle_mm": 0.4}]
    meta = derive_profile_meta(
        P, {"inherits": "0.20mm Standard @BBL X1C"}, name="mine", parent_lookup=lambda n: parent
    )
    assert meta.compat == parent and meta.compat is not parent


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("0.20mm Standard @BBL A1M", {"model_key": "a1mini", "nozzle_mm": None}),
        ("PLA @Bambu Lab A1 mini 0.4 nozzle", {"model_key": "a1mini", "nozzle_mm": 0.4}),
        ("0.12mm Fine @BBL X1C 0.2 nozzle", {"model_key": "x1carbon", "nozzle_mm": 0.2}),
    ],
)
def test_fallback_name_suffix(name, expected):
    assert derive_profile_meta(P, {}, name=name).compat == [expected]


def test_nothing_derivable_is_universal():
    for name in ("My hand-made preset", "Thing @System", "Generic PLA"):
        meta = derive_profile_meta(F, {"inherits": "Nope"}, name=name, parent_lookup=lambda n: None)
        assert meta.compat is None and meta.printer_model_key is None
    row = SimpleNamespace(kind=F, compat=None, printer_model_key=None, layer_height_mm=None)
    assert profile_in_scope(row, "a1mini", 0.4)


def test_layer_height_data_then_name_prefix_process_only():
    assert derive_profile_meta(P, {"layer_height": "0.16"}, name="x").layer_height_mm == 0.16
    assert derive_profile_meta(P, {}, name="0.08mm Extra Fine").layer_height_mm == 0.08
    assert derive_profile_meta(P, {}, name="nope").layer_height_mm is None
    assert derive_profile_meta(F, {"layer_height": "0.2"}, name="0.2mm x").layer_height_mm is None


# --- profile_in_scope ------------------------------------------------------


def _row(kind=P, compat=None, key=None, layer=None):
    return SimpleNamespace(kind=kind, compat=compat, printer_model_key=key, layer_height_mm=layer)


def test_nozzle_tolerance_and_null_nozzle():
    row = _row(compat=[{"model_key": "a1mini", "nozzle_mm": 0.4}])
    assert profile_in_scope(row, "a1mini", 0.405)
    assert not profile_in_scope(row, "a1mini", 0.42)
    assert not profile_in_scope(row, "a1mini", 0.2)
    any_noz = _row(compat=[{"model_key": "a1mini", "nozzle_mm": None}])
    assert profile_in_scope(any_noz, "a1mini", 0.8)
    assert profile_in_scope(row, "a1mini", None)


def test_layer_tolerance_process_only():
    row = _row(compat=[{"model_key": "a1mini", "nozzle_mm": None}], layer=0.2)
    assert profile_in_scope(row, "a1mini", 0.4, 0.2)
    assert profile_in_scope(row, "a1mini", 0.4, 0.204)
    assert not profile_in_scope(row, "a1mini", 0.4, 0.16)
    filament = _row(kind=F, compat=[{"model_key": "a1mini", "nozzle_mm": None}])
    assert profile_in_scope(filament, "a1mini", 0.4, 0.16)


# --- nozzle resolution order ----------------------------------------------


def test_resolve_nozzle_order():
    assert resolve_nozzle(0.6, 0.4, 0.8) == (0.8, "override")
    assert resolve_nozzle(0.6, 0.4) == (0.6, "live")
    assert resolve_nozzle(None, 0.2) == (0.2, "last_seen")
    assert resolve_nozzle(0.0, None) == (0.4, "assumed")


# --- bundle import + backfill ---------------------------------------------


def test_parse_bundle_attaches_meta(tmp_path):
    parsed = {p.name: p for p in parse_bundle(write_bundle(tmp_path / "b"))}
    proc = parsed["0.20mm Standard @BBL A1M"].meta
    assert proc.compat == [{"model_key": "a1mini", "nozzle_mm": 0.4}]
    assert proc.layer_height_mm == 0.2
    assert parsed["Bambu Lab A1 mini 0.4 nozzle"].meta.printer_model_key == "a1mini"
    assert parsed["Bambu Lab X1 Carbon 0.4 nozzle"].meta.printer_model_key == "x1carbon"


def test_backfill_idempotent_and_never_touches_user_columns():
    with base.sync_session() as session:
        session.add_all(
            [
                SlicerProfile(
                    name="0.20mm Standard @BBL A1M",
                    kind=P,
                    printer_model="USER EDIT",
                    nozzle_mm=9.9,
                    data={"layer_height": "0.2"},
                ),
                SlicerProfile(name="Hand made", kind=F, data={}),
            ]
        )
        session.commit()
        assert backfill_profile_meta(session) == 1
        rows = {r.name: r for r in session.execute(select(SlicerProfile)).scalars()}
        proc = rows["0.20mm Standard @BBL A1M"]
        assert proc.compat == [{"model_key": "a1mini", "nozzle_mm": None}]
        assert proc.layer_height_mm == 0.2
        assert (proc.printer_model, proc.nozzle_mm) == ("USER EDIT", 9.9)
        assert rows["Hand made"].compat is None
        before = proc.compat
        backfill_profile_meta(session)
        session.refresh(proc)
        assert proc.compat == before and proc.printer_model == "USER EDIT"
