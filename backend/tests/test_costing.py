from app.models.enums import ArchiveResult
from app.services.costing import CostRates, RunCost, charged_grams, price_run

R = CostRates(filament_per_kg=20.0, machine_per_hour=1.5)


def test_price_run_sums_components():
    c = price_run(250.0, 7200, R)
    assert (c.filament_cost, c.machine_cost, c.total_cost) == (5.0, 3.0, 8.0)


def test_price_run_unknowns():
    assert price_run(None, None, R).total_cost is None
    assert price_run(100.0, None, R) == RunCost(2.0, None, None, 2.0)


def test_charged_grams_completed_is_full_estimate():
    assert (
        charged_grams(100.0, ArchiveResult.COMPLETED, layer=10, total_layers=200, progress_pct=5)
        == 100.0
    )


def test_charged_grams_partial_by_layer():
    for r in (ArchiveResult.FAILED, ArchiveResult.CANCELED, ArchiveResult.STOPPED):
        assert charged_grams(100.0, r, layer=50, total_layers=200, progress_pct=None) == 25.0


def test_charged_grams_partial_edges():
    assert (
        charged_grams(100.0, ArchiveResult.FAILED, layer=5, total_layers=0, progress_pct=40) == 40.0
    )
    assert (
        charged_grams(100.0, ArchiveResult.FAILED, layer=None, total_layers=None, progress_pct=None)
        == 0.0
    )
    assert (
        charged_grams(100.0, ArchiveResult.FAILED, layer=300, total_layers=200, progress_pct=None)
        == 100.0
    )
    assert (
        charged_grams(None, ArchiveResult.FAILED, layer=1, total_layers=2, progress_pct=None)
        is None
    )
