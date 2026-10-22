import pytest

from app.services.ams_match import (
    FilamentNeed,
    Match,
    TrayInfo,
    delta_e_ok,
    hex_to_oklab,
    match,
    to_ams_mapping,
)


def _need(fid=1, type_="PLA", color="#FF0000", grams=10.0):
    return FilamentNeed(filament_id=fid, type=type_, color=color, grams=grams)


def _tray(tray, type_, color):
    return TrayInfo(tray=tray, type=type_, color=color)


@pytest.mark.parametrize(
    ("need", "trays", "overrides", "exp_tray", "exp_status"),
    [
        # exact type + same colour -> ok
        (_need(type_="PLA", color="#FF0000"), [_tray(0, "PLA", "#FF0000")], None, 0, "ok"),
        # PLA red vs PLA trays {orange, red} -> the red tray
        (
            _need(type_="PLA", color="#FF0000"),
            [_tray(0, "PLA", "#FFA500"), _tray(1, "PLA", "#FF0000")],
            None,
            1,
            "ok",
        ),
        # PLA vs only PETG, no override -> none
        (_need(type_="PLA", color="#FF0000"), [_tray(0, "PETG", "#FF0000")], None, None, "none"),
        # no trays -> none
        (_need(type_="PLA", color="#FF0000"), [], None, None, "none"),
        # type match but ΔE > 0.10 -> color
        (_need(type_="PLA", color="#FFFFFF"), [_tray(0, "PLA", "#000000")], None, 0, "color"),
        # "pla " vs "PLA" matches
        (_need(type_="pla ", color="#FF0000"), [_tray(0, "PLA", "#FF0000")], None, 0, "ok"),
        # "PETG-CF" != "PETG"
        (
            _need(type_="PETG-CF", color="#FF0000"),
            [_tray(0, "PETG", "#FF0000")],
            None,
            None,
            "none",
        ),
        # override to a PETG tray for a PLA need -> status type, tray kept
        (_need(type_="PLA", color="#FF0000"), [_tray(0, "PETG", "#FF0000")], {1: 0}, 0, "type"),
    ],
)
def test_match_table(need, trays, overrides, exp_tray, exp_status):
    [result] = match([need], trays, overrides)
    assert result.tray == exp_tray
    assert result.status == exp_status


def test_match_two_needs_share_one_tray():
    trays = [_tray(0, "PLA", "#FF0000")]
    needs = [_need(fid=1), _need(fid=2)]
    results = match(needs, trays)
    assert [r.tray for r in results] == [0, 0]
    assert [r.status for r in results] == ["ok", "ok"]


def test_delta_e_identity_and_order():
    assert delta_e_ok("#FFFFFF", "#FFFFFF") == 0
    assert delta_e_ok("#FFFFFF", "#000000") == pytest.approx(1.0, abs=1e-6)


def test_hex_to_oklab_white_black():
    assert hex_to_oklab("#FFFFFF") == pytest.approx((1.0, 0.0, 0.0), abs=1e-6)
    assert hex_to_oklab("#000000") == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)


def test_to_ams_mapping_gaps():
    matches = [
        Match(filament_id=1, tray=5, status="ok", delta_e=0.0),
        Match(filament_id=3, tray=7, status="ok", delta_e=0.0),
    ]
    assert to_ams_mapping(matches) == [5, -1, 7]


def test_to_ams_mapping_external_passthrough():
    matches = [Match(filament_id=1, tray=254, status="ok", delta_e=0.0)]
    assert to_ams_mapping(matches) == [254]
