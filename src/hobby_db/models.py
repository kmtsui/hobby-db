"""Pydantic models for Catalog, Inventory, PriceHistory, and crawler CSV rows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from hobby_db.config import (
    CATALOG_COLUMNS,
    INVENTORY_COLUMNS,
    PBANDAI_CSV_COLUMNS,
    PRICE_HISTORY_COLUMNS,
)


def utc_now_str() -> str:
    """Return current UTC timestamp formatted for Google Sheets DateTime detection."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _cell_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


class SheetModelMixin(BaseModel):
    """Shared serialization helpers for converting models to/from sheet rows."""

    def _to_row(self, columns: tuple[str, ...]) -> list[str | int]:
        row: list[str | int] = []
        for col in columns:
            val = getattr(self, col)
            row.append("" if val is None else val)
        return row


class CatalogItem(SheetModelMixin):
    """Master product entry in the ``Catalog`` tab."""

    item_id: str
    jan_code: str = ""
    title: str
    brand: str = ""
    series: str = ""
    msrp_jpy: int | None = None
    release_date: str = ""
    image_url: str = ""
    market_price_new: int | None = None
    market_price_used: int | None = None
    price_source: str = ""
    price_updated_at: str = ""
    search_query_override: str = ""

    def to_sheet_row(self) -> list[str | int]:
        return self._to_row(CATALOG_COLUMNS)

    @classmethod
    def from_sheet_row(cls, row: dict[str, Any]) -> CatalogItem:
        return cls(
            item_id=_cell_str(row.get("item_id")),
            jan_code=_cell_str(row.get("jan_code")),
            title=_cell_str(row.get("title")),
            brand=_cell_str(row.get("brand")),
            series=_cell_str(row.get("series")),
            msrp_jpy=_cell_int(row.get("msrp_jpy")),
            release_date=_cell_str(row.get("release_date")),
            image_url=_cell_str(row.get("image_url")),
            market_price_new=_cell_int(row.get("market_price_new")),
            market_price_used=_cell_int(row.get("market_price_used")),
            price_source=_cell_str(row.get("price_source")),
            price_updated_at=_cell_str(row.get("price_updated_at")),
            search_query_override=_cell_str(row.get("search_query_override")),
        )


class CatalogPriceUpdate(BaseModel):
    """Partial update payload touching only market price columns on ``Catalog``."""

    item_id: str
    market_price_new: int | None = None
    market_price_used: int | None = None
    price_source: str = ""
    price_updated_at: str = Field(default_factory=utc_now_str)


class InventoryEntry(SheetModelMixin):
    """Individual owned or pre-ordered copy in the ``Inventory`` tab."""

    entry_id: str
    item_id: str
    order_id: str = ""
    shop: str = "Premium Bandai"
    order_date: str = ""
    purchase_price_jpy: int | None = None
    shipping_jpy: int | None = 0
    condition: str = "Sealed"
    status: str = "Pre-ordered"
    storage_location: str = ""
    sold_price_jpy: int | None = None
    notes: str = ""

    def to_sheet_row(self) -> list[str | int]:
        return self._to_row(INVENTORY_COLUMNS)

    @classmethod
    def from_sheet_row(cls, row: dict[str, Any]) -> InventoryEntry:
        return cls(
            entry_id=_cell_str(row.get("entry_id")),
            item_id=_cell_str(row.get("item_id")),
            order_id=_cell_str(row.get("order_id")),
            shop=_cell_str(row.get("shop")),
            order_date=_cell_str(row.get("order_date")),
            purchase_price_jpy=_cell_int(row.get("purchase_price_jpy")),
            shipping_jpy=_cell_int(row.get("shipping_jpy")),
            condition=_cell_str(row.get("condition")),
            status=_cell_str(row.get("status")),
            storage_location=_cell_str(row.get("storage_location")),
            sold_price_jpy=_cell_int(row.get("sold_price_jpy")),
            notes=_cell_str(row.get("notes")),
        )


class PriceHistoryRecord(SheetModelMixin):
    """Append-only market price snapshot in the ``PriceHistory`` tab."""

    log_id: str
    item_id: str
    recorded_at: str = Field(default_factory=utc_now_str)
    price_new: int | None = None
    price_used: int | None = None
    source: str = ""
    sample_size: int = 0

    def to_sheet_row(self) -> list[str | int]:
        return self._to_row(PRICE_HISTORY_COLUMNS)

    @classmethod
    def from_sheet_row(cls, row: dict[str, Any]) -> PriceHistoryRecord:
        return cls(
            log_id=_cell_str(row.get("log_id")),
            item_id=_cell_str(row.get("item_id")),
            recorded_at=_cell_str(row.get("recorded_at")),
            price_new=_cell_int(row.get("price_new")),
            price_used=_cell_int(row.get("price_used")),
            source=_cell_str(row.get("source")),
            sample_size=_cell_int(row.get("sample_size")) or 0,
        )


class PBandaiOrderRow(BaseModel):
    """Raw line item extracted from Premium Bandai order history."""

    order_id: str
    order_date: str = ""
    item_title: str
    unit_price_jpy: int | None = None
    quantity: int = Field(default=1, ge=1)
    shipping_jpy: int = Field(default=0, ge=0)
    order_status: str = ""
    delivery_month: str = ""
    image_url: str = ""

    def to_csv_dict(self) -> dict[str, str | int]:
        return {
            col: ("" if getattr(self, col) is None else getattr(self, col))
            for col in PBANDAI_CSV_COLUMNS
        }
