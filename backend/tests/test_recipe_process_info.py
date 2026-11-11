"""`GET /recipes` exposes each recipe's process profile name + layer height."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SlicerProfile
from app.models.enums import ProfileKind

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def test_recipe_list_exposes_process_name_and_layer_height(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    printer = SlicerProfile(name="P1", kind=ProfileKind.PRINTER, data={})
    filament = SlicerProfile(name="F1", kind=ProfileKind.FILAMENT, data={})
    proc = SlicerProfile(
        name="0.28mm Extra Draft", kind=ProfileKind.PROCESS, layer_height_mm=0.28, data={}
    )
    db_session.add_all([printer, filament, proc])
    await db_session.commit()
    for n in ("R1", "R2"):
        r = await authenticated_client.post(
            "/api/recipes",
            json={
                "name": n,
                "printer_profile_id": printer.id,
                "process_profile_id": proc.id,
                "filament_profile_ids": [filament.id],
                "bed_type": "textured_pei",
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["process_layer_height_mm"] == 0.28
    rows = (await authenticated_client.get("/api/recipes")).json()
    assert [(x["process_profile_name"], x["process_layer_height_mm"]) for x in rows] == [
        ("0.28mm Extra Draft", 0.28)
    ] * 2
