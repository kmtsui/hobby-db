"""Ingest Premium Bandai UTF-8 CSV exports into Catalog and Inventory models.

Quantity convention:
- When ``expand_quantity=True`` (default), a line item with ``quantity == 1`` gets
  ``entry_id = "pbandai_{order_id}_{line_index}"``. If ``quantity > 1``, it expands
  into ``"pbandai_{order_id}_{line_index}_u1"`` .. ``"_u{quantity}"`` so each physical
  box can track its own status, condition, storage location, and resale price.
  Order shipping is assigned to ``_u1`` (``0`` for ``_u2+``).
- When ``expand_quantity=False``, one row ``"pbandai_{order_id}_{line_index}"`` is
  emitted with ``"Qty: {quantity}"`` recorded in ``notes`` when ``quantity > 1``.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd

from hobby_db.models import CatalogItem, InventoryEntry
from hobby_db.normalize import (
    infer_series_and_brand,
    make_entry_id,
    make_item_id,
    normalize_title,
    parse_delivery_month,
    parse_jp_date,
    parse_jpy_int,
)


def map_pbandai_status(raw_status: str) -> str:
    """Map Japanese or English Premium Bandai order status to Inventory enum values."""
    text = (raw_status or "").strip()
    lower = text.lower()
    if any(token in text for token in ("キャンセル", "取消")) or "cancel" in lower:
        return "Cancelled"
    if (
        any(
            token in text
            for token in (
                "発送済",
                "発送完了",
                "配達完了",
                "出荷済",
                "出荷完了",
                "お届け済",
                "お届け完了",
            )
        )
        or "delivered" in lower
        or "shipped" in lower
    ):
        return "Owned"
    return "Pre-ordered"


def _merge_catalog_item(existing: CatalogItem, incoming: CatalogItem) -> CatalogItem:
    """Merge non-empty metadata fields when duplicate Catalog items appear in CSV."""
    updates: dict[str, object] = {}
    for field in ("jan_code", "brand", "series", "release_date", "image_url"):
        if not getattr(existing, field) and getattr(incoming, field):
            updates[field] = getattr(incoming, field)
    if existing.msrp_jpy is None and incoming.msrp_jpy is not None:
        updates["msrp_jpy"] = incoming.msrp_jpy
    if not updates:
        return existing
    return existing.model_copy(update=updates)


def ingest_pbandai_csv(
    csv_path: Path,
    expand_quantity: bool = True,
) -> tuple[list[CatalogItem], list[InventoryEntry]]:
    """Parse a Premium Bandai CSV export into deduplicated Catalog and Inventory rows."""
    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str).fillna("")

    catalog_by_id: dict[str, CatalogItem] = {}
    inventory_entries: list[InventoryEntry] = []
    line_counters: dict[str, int] = defaultdict(int)

    for record in df.to_dict(orient="records"):
        order_id = str(record.get("order_id", "")).strip()
        raw_title = str(record.get("item_title", "")).strip()
        if not order_id or not raw_title:
            continue

        line_counters[order_id] += 1
        line_index = line_counters[order_id]

        clean_title = normalize_title(raw_title)
        item_id = make_item_id(clean_title)
        brand, series = infer_series_and_brand(clean_title, default_brand="Premium Bandai")
        unit_price = parse_jpy_int(record.get("unit_price_jpy"))
        shipping_jpy = parse_jpy_int(record.get("shipping_jpy")) or 0
        quantity = max(1, parse_jpy_int(record.get("quantity")) or 1)
        order_date = (
            parse_jp_date(str(record.get("order_date", "")))
            or str(record.get("order_date", "")).strip()
        )
        release_date = parse_delivery_month(str(record.get("delivery_month", "")))
        image_url = str(record.get("image_url", "")).strip()
        status = map_pbandai_status(str(record.get("order_status", "")))

        catalog_item = CatalogItem(
            item_id=item_id,
            title=clean_title,
            brand=brand,
            series=series,
            msrp_jpy=unit_price,
            release_date=release_date,
            image_url=image_url,
        )
        if item_id in catalog_by_id:
            catalog_by_id[item_id] = _merge_catalog_item(catalog_by_id[item_id], catalog_item)
        else:
            catalog_by_id[item_id] = catalog_item

        if expand_quantity and quantity > 1:
            for unit_idx in range(1, quantity + 1):
                entry_id = make_entry_id("pbandai", order_id, line_index, unit_index=unit_idx)
                inventory_entries.append(
                    InventoryEntry(
                        entry_id=entry_id,
                        item_id=item_id,
                        order_id=order_id,
                        shop="Premium Bandai",
                        order_date=order_date,
                        purchase_price_jpy=unit_price,
                        shipping_jpy=shipping_jpy if unit_idx == 1 else 0,
                        condition="Sealed",
                        status=status,
                    )
                )
        else:
            entry_id = make_entry_id("pbandai", order_id, line_index)
            notes = f"Qty: {quantity}" if quantity > 1 else ""
            inventory_entries.append(
                InventoryEntry(
                    entry_id=entry_id,
                    item_id=item_id,
                    order_id=order_id,
                    shop="Premium Bandai",
                    order_date=order_date,
                    purchase_price_jpy=unit_price,
                    shipping_jpy=shipping_jpy,
                    condition="Sealed",
                    status=status,
                    notes=notes,
                )
            )

    return list(catalog_by_id.values()), inventory_entries
