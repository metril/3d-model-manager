"""Slice-dialog preset lists: stock/user dedup, display sort, printer affinity."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SlicerProfile
from app.models.enums import ProfileKind, ProfileSource
from app.services.slicer_profiles import dedup_stock_profiles, profile_preferred, sort_profiles

SYNCED = ProfileSource.SYNCED


def _p(pid, name, kind=ProfileKind.FILAMENT, source=ProfileSource.IMPORT, compat=None):
    return SimpleNamespace(
        id=pid, name=name, kind=kind, source=source, compat=compat, printer_model_key=None
    )


def test_dedup_prefers_user_copy_over_stock():
    stock = _p(1, "Bambu PLA Basic", source=SYNCED)
    copy = _p(2, "Bambu PLA Basic - Copy")
    other = _p(3, "Generic PETG", source=SYNCED)
    # same name, different kind is not a duplicate
    proc = _p(4, "Bambu PLA Basic", kind=ProfileKind.PROCESS, source=SYNCED)
    kept, overriding = dedup_stock_profiles([stock, copy, other, proc])
    assert [p.id for p in kept] == [2, 3, 4]
    assert overriding == {2}


def test_sort_user_before_stock_vendor_then_natural_name():
    rows = [
        _p(1, "Generic PA12", source=SYNCED),
        _p(2, "Bambu PLA", source=SYNCED),
        _p(3, "Generic PA6", source=SYNCED),
        _p(4, "Zed PLA", source=SYNCED),
        _p(5, "Bambu Support For PLA", source=SYNCED),
        _p(6, "My PLA"),
    ]
    assert [p.id for p in sort_profiles(rows)] == [6, 2, 3, 1, 4, 5]


def test_sort_printer_affinity_floats_matching_model_and_nozzle():
    kind = ProfileKind.PRINTER
    a1m_04 = _p(1, "Zeta", kind, SYNCED, [{"model_key": "a1mini", "nozzle_mm": 0.4}])
    a1m_06 = _p(2, "Alpha", kind, SYNCED, [{"model_key": "a1mini", "nozzle_mm": 0.6}])
    x1 = _p(3, "Aardvark", kind, SYNCED, [{"model_key": "x1carbon", "nozzle_mm": 0.4}])
    universal = _p(4, "Any", kind, SYNCED)
    assert profile_preferred(a1m_04, "a1mini", 0.4)
    assert not profile_preferred(a1m_06, "a1mini", 0.4)
    assert not profile_preferred(universal, "a1mini", 0.4)
    assert profile_preferred(a1m_06, "a1mini", None)
    out = sort_profiles([x1, a1m_06, universal, a1m_04], "a1mini", 0.4)
    assert out[0].id == 1


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_list_profiles_sorted_with_dedup(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    for name, source in (
        ("Zed PLA", SYNCED),
        ("Bambu PLA", SYNCED),
        ("Bambu PLA - Copy", ProfileSource.IMPORT),
        ("Mine", ProfileSource.IMPORT),
    ):
        db_session.add(
            SlicerProfile(name=name, kind=ProfileKind.FILAMENT, data={"name": name}, source=source)
        )
    await db_session.commit()

    r = await authenticated_client.get("/api/profiles?kind=filament")
    assert [p["name"] for p in r.json()["items"]] == [
        "Bambu PLA - Copy",
        "Mine",
        "Bambu PLA",
        "Zed PLA",
    ]

    r = await authenticated_client.get("/api/profiles?kind=filament&dedup=1")
    items = r.json()["items"]
    assert [p["name"] for p in items] == ["Bambu PLA - Copy", "Mine", "Zed PLA"]
    assert items[0]["overrides_stock"] is True
    assert items[1]["overrides_stock"] is None


@pytest.mark.usefixtures("library_root", "data_dir")
async def test_dedup_keeps_stock_when_user_copy_out_of_scope(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    for name, source, nozzle in (
        ("Bambu PLA", SYNCED, 0.4),
        ("Bambu PLA - Copy", ProfileSource.IMPORT, 0.6),
    ):
        db_session.add(
            SlicerProfile(
                name=name,
                kind=ProfileKind.FILAMENT,
                data={"name": name},
                source=source,
                compat=[{"model_key": "a1mini", "nozzle_mm": nozzle}],
            )
        )
    await db_session.commit()
    r = await authenticated_client.get("/api/profiles?kind=filament&nozzle_mm=0.4&dedup=1")
    items = r.json()["items"]
    assert [p["name"] for p in items] == ["Bambu PLA"]
    assert items[0]["overrides_stock"] is None
