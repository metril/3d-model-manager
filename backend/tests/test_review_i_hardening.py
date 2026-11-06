"""Review wave 2 (PR-I): hardening of small service helpers."""

from __future__ import annotations

import math

from app.services import ams_match
from app.services.home_assistant import evaluate


def test_bad_hex_colour_does_not_raise():
    assert ams_match.delta_e_ok("zzzzzz", "ffffff") == 0.0
    assert ams_match.delta_e_ok("", "ffffff") == 0.0
    m = ams_match.match(
        [ams_match.FilamentNeed(1, "PLA", "nothex", 5.0)],
        [ams_match.TrayInfo(0, "PLA", "#ff0000")],
    )
    assert m[0].tray == 0


def test_non_finite_states_never_fire():
    for bad in ("nan", "inf", "-inf"):
        assert evaluate({"op": "gt", "value": 1}, bad) is False
        assert evaluate({"op": "lt", "value": 1}, bad) is False
    assert evaluate({"op": "gt", "value": float("nan")}, "5") is False
    assert evaluate({"op": "gt", "value": 1}, "5") is True
    assert math.isfinite(1.0)
