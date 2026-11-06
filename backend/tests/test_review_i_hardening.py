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


def test_idle_decay_lowers_rate_and_archived_history_counts():
    from datetime import UTC, datetime, timedelta

    from app.services import stock_forecast as sf

    now = datetime(2026, 11, 1, tzinfo=UTC)
    recs = [
        sf.UsageRecord(now - timedelta(days=60), 100),
        sf.UsageRecord(now - timedelta(days=58), 100),
    ]
    fresh = sf.history_rate(recs, now - timedelta(days=58))
    stale = sf.history_rate(recs, now)  # 58 idle days since the last print
    assert fresh is not None and stale is not None and stale[0] < fresh[0]

    spool = sf.StockSpool(1, "PLA", "B", "red", 1000, 0, now - timedelta(days=100))
    base = sf.forecast_sku([spool], {}, sf.SkuSettings(), 0, now)
    with_archived = sf.forecast_sku([spool], {}, sf.SkuSettings(), 0, now, extra_history=recs)
    assert with_archived.daily_rate_g != base.daily_rate_g  # archived usage was used


def test_unconfigured_sku_uses_global_safety_margin():
    from datetime import UTC, datetime

    from app.services import stock_forecast as sf

    now = datetime(2026, 11, 1, tzinfo=UTC)
    spool = sf.StockSpool(1, "PLA", "B", "red", 1000, 500, datetime(2026, 9, 1, tzinfo=UTC))
    fm = sf.forecast_all(
        [spool], {}, {}, 0, now, default_settings=sf.SkuSettings(safety_margin_value=40)
    )
    default = sf.forecast_all([spool], {}, {}, 0, now)
    key = spool.key
    assert fm[key][0].reorder_point_g > default[key][0].reorder_point_g


def test_slicer_client_caps_streamed_response_and_closes():
    import httpx
    import pytest

    from app.services import slicer_client as sc

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000)

    http = httpx.Client(base_url="http://s", transport=httpx.MockTransport(handler))
    with sc.SlicerClient("http://s", http=http) as c:
        with pytest.raises(sc.SlicerError, match="exceeds"):
            c._request("GET", "/x", max_bytes=100)
        assert c._request("GET", "/x", max_bytes=10_000).content == b"x" * 5000
    assert http.is_closed


def test_cron_due_timezone_and_dom_dow_or():
    from datetime import UTC, datetime

    from app.services.notify.delivery import cron_due

    # 09:00 America/New_York (EST, UTC-5) == 14:00 UTC
    last, now = datetime(2026, 12, 1, 13, 59, tzinfo=UTC), datetime(2026, 12, 1, 14, 1, tzinfo=UTC)
    assert cron_due("0 9 * * *", last, now, "America/New_York")
    assert not cron_due("0 9 * * *", last, now)  # UTC reading: 09:00 UTC is not in range
    # dom=15 OR dow=Tue(2): 2026-12-01 is a Tuesday, not the 15th
    tue = datetime(2026, 12, 1, 8, 59, tzinfo=UTC), datetime(2026, 12, 1, 9, 1, tzinfo=UTC)
    assert cron_due("0 9 15 * 2", *tue)
    assert not cron_due("0 9 15 * 3", *tue)  # neither Wed nor the 15th
