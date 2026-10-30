import pytest

from app.services import energy_price
from app.services.app_config import AppConfig
from app.services.ha_client import HaUnreachable

HA = {"energy_price_source": "homeassistant", "energy_price_ha_entity": "sensor.tariff"}


class _Client:
    def __init__(self, result):
        self.result = result

    def state(self, entity_id):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _patch_cfg(monkeypatch, **kw):
    cfg = AppConfig(
        printer_enabled=True,
        scan_interval_s=1,
        collection_sync_interval_s=1,
        watch_interval_s=1,
        watch_stable_s=1,
        **kw,
    )
    monkeypatch.setattr(energy_price, "get_app_config_sync", lambda s, st: cfg)
    monkeypatch.setattr(energy_price, "_warned", False)


def test_fixed_source_skips_sampling(monkeypatch):
    _patch_cfg(monkeypatch)
    assert energy_price.sample_energy_price(None, None, _Client({"state": "0.2"})) is None


def test_samples_numeric_state(monkeypatch):
    _patch_cfg(monkeypatch, **HA)
    assert energy_price.sample_energy_price(None, None, _Client({"state": "0.27"})) == 0.27


@pytest.mark.parametrize(
    "result", [{"state": "unavailable"}, {"state": "unknown"}, None, HaUnreachable("x")]
)
def test_unavailable_falls_back_and_logs_once(monkeypatch, caplog, result):
    _patch_cfg(monkeypatch, **HA)
    with caplog.at_level("WARNING"):
        assert energy_price.sample_energy_price(None, None, _Client(result)) is None
        assert energy_price.sample_energy_price(None, None, _Client(result)) is None
    assert len([r for r in caplog.records if "fixed energy price" in r.message]) == 1
