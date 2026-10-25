"""Shared helpers for T5 spool housekeeping (suppliers, locations, catalog)."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import SpoolSupplier, StorageLocation, Supplier
from app.schemas.inventory import SpoolSupplierOut

CATALOG_JSON = Path(__file__).resolve().parent.parent / "data" / "color_catalog.json"


def name_key(name: str) -> str:
    return name.strip().lower()


def load_default_catalog() -> list[dict]:
    return json.loads(CATALOG_JSON.read_text())


async def spool_suppliers(db: AsyncSession, spool_id: int) -> list[SpoolSupplierOut]:
    rows = (
        await db.execute(
            select(SpoolSupplier, Supplier.name)
            .join(Supplier, Supplier.id == SpoolSupplier.supplier_id)
            .where(SpoolSupplier.spool_id == spool_id)
            .order_by(SpoolSupplier.id)
        )
    ).all()
    return [
        SpoolSupplierOut(
            supplier_id=link.supplier_id,
            supplier_name=name,
            article_number=link.article_number,
            quoted_price_per_kg=float(link.quoted_price_per_kg)
            if link.quoted_price_per_kg is not None
            else None,
            is_purchase_source=link.is_purchase_source,
        )
        for link, name in rows
    ]


async def get_or_create_location(
    db: AsyncSession, name: str, user_id: int | None
) -> StorageLocation:
    key = name_key(name)
    loc = (
        await db.execute(select(StorageLocation).where(StorageLocation.name_key == key))
    ).scalar_one_or_none()
    if loc is None:
        loc = StorageLocation(name=name.strip(), name_key=key, created_by_user_id=user_id)
        db.add(loc)
        await db.flush()
    return loc
