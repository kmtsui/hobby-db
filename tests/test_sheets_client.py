"""Tests for idempotent Google Sheets client and bootstrap request builder."""

from __future__ import annotations

import re
from typing import Any

from hobby_db.config import (
    CATALOG_COLUMNS,
    INVENTORY_COLUMNS,
    PRICE_HISTORY_COLUMNS,
    TAB_CATALOG,
    TAB_INVENTORY,
    TAB_PRICE_HISTORY,
)
from hobby_db.models import (
    CatalogItem,
    CatalogPriceUpdate,
    InventoryEntry,
    PriceHistoryRecord,
)
from hobby_db.sheets.bootstrap import build_bootstrap_requests
from hobby_db.sheets.client import HobbySheetsClient, col_index_to_letter

_A1_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _letter_to_col_index(letters: str) -> int:
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


class FakeWorksheet:
    """In-memory mock of a gspread.Worksheet supporting get_all_values, append_rows, batch_update."""

    def __init__(self, title: str, sheet_id: int, headers: tuple[str, ...]) -> None:
        self.title = title
        self.id = sheet_id
        self.rows: list[list[Any]] = [list(headers)]
        self.batch_updates: list[list[dict[str, Any]]] = []

    def get_all_values(self) -> list[list[str]]:
        return [["" if cell is None else str(cell) for cell in row] for row in self.rows]

    def append_rows(
        self, values: list[list[Any]], value_input_option: str = "USER_ENTERED"
    ) -> None:
        assert value_input_option == "USER_ENTERED"
        self.rows.extend([list(r) for r in values])

    def batch_update(
        self,
        data: list[dict[str, Any]],
        value_input_option: str = "USER_ENTERED",
    ) -> None:
        assert value_input_option == "USER_ENTERED"
        self.batch_updates.append(data)
        for item in data:
            match = _A1_CELL_RE.match(item["range"])
            assert match is not None
            col_idx = _letter_to_col_index(match.group(1))
            row_idx = int(match.group(2)) - 1
            while len(self.rows[row_idx]) <= col_idx:
                self.rows[row_idx].append("")
            self.rows[row_idx][col_idx] = item["values"][0][0]


class FakeSpreadsheet:
    """In-memory mock of a gspread.Spreadsheet."""

    def __init__(self) -> None:
        self._tabs = {
            TAB_CATALOG: FakeWorksheet(TAB_CATALOG, 1, CATALOG_COLUMNS),
            TAB_INVENTORY: FakeWorksheet(TAB_INVENTORY, 2, INVENTORY_COLUMNS),
            TAB_PRICE_HISTORY: FakeWorksheet(TAB_PRICE_HISTORY, 3, PRICE_HISTORY_COLUMNS),
        }

    def worksheet(self, name: str) -> FakeWorksheet:
        return self._tabs[name]


def test_col_index_to_letter() -> None:
    assert col_index_to_letter(1) == "A"
    assert col_index_to_letter(13) == "M"
    assert col_index_to_letter(27) == "AA"


def test_sync_inventory_preserves_mobile_edits() -> None:
    spreadsheet = FakeSpreadsheet()
    inv_ws = spreadsheet.worksheet(TAB_INVENTORY)
    # Simulate an existing row edited by the user on AppSheet mobile
    existing = InventoryEntry(
        entry_id="pbandai_ORD-1_1",
        item_id="item_abc",
        order_id="ORD-1",
        shop="Premium Bandai",
        order_date="2025-05-01",
        purchase_price_jpy=8800,
        shipping_jpy=660,
        condition="Built",
        status="On Display",
        storage_location="Living Room Detolf #2",
        sold_price_jpy=15000,
        notes="Panel lined and topcoated",
    )
    inv_ws.append_rows([existing.to_sheet_row()])

    client = HobbySheetsClient(spreadsheet)  # type: ignore[arg-type]
    # Re-crawl attempts to push default Sealed / Pre-ordered status for ORD-1 plus a new ORD-2
    incoming = [
        InventoryEntry(
            entry_id="pbandai_ORD-1_1",
            item_id="item_abc",
            order_id="ORD-1",
            shop="Premium Bandai",
            order_date="2025-05-01",
            purchase_price_jpy=8800,
            shipping_jpy=660,
            condition="Sealed",
            status="Pre-ordered",
            storage_location="",
            sold_price_jpy=None,
            notes="",
        ),
        InventoryEntry(
            entry_id="pbandai_ORD-2_1",
            item_id="item_xyz",
            order_id="ORD-2",
            shop="Premium Bandai",
            order_date="2025-06-01",
            purchase_price_jpy=5500,
            shipping_jpy=660,
            condition="Sealed",
            status="Pre-ordered",
        ),
    ]

    stats = client.sync_inventory_entries(incoming)
    assert stats == {"added": 1, "skipped": 1}

    entries = client.get_inventory_entries()
    assert len(entries) == 2
    # Protected mobile fields must remain completely intact
    assert entries[0].condition == "Built"
    assert entries[0].status == "On Display"
    assert entries[0].storage_location == "Living Room Detolf #2"
    assert entries[0].sold_price_jpy == 15000
    assert entries[0].notes == "Panel lined and topcoated"
    assert entries[1].entry_id == "pbandai_ORD-2_1"


def test_upsert_catalog_and_update_prices_only_touch_allowed_columns() -> None:
    spreadsheet = FakeSpreadsheet()
    client = HobbySheetsClient(spreadsheet)  # type: ignore[arg-type]

    initial = CatalogItem(
        item_id="item_1",
        jan_code="",
        title="MG ガンダムMk-V",
        brand="Bandai Spirits",
        series="Gunpla",
        msrp_jpy=8800,
        market_price_used=11000,
        search_query_override="Custom Query Keep Me",
    )
    assert client.upsert_catalog_items([initial]) == {"added": 1, "updated": 0, "skipped": 0}

    # Backfill blank jan_code & image_url, without overwriting existing title or search_query_override
    backfill = CatalogItem(
        item_id="item_1",
        jan_code="4573102611111",
        title="Different Title Should Be Ignored",
        brand="Other",
        image_url="https://example.com/mkv.png",
        search_query_override="Should Not Overwrite",
    )
    assert client.upsert_catalog_items([backfill]) == {"added": 0, "updated": 1, "skipped": 0}

    loaded = client.get_catalog_items()[0]
    assert loaded.jan_code == "4573102611111"
    assert loaded.image_url == "https://example.com/mkv.png"
    assert loaded.title == "MG ガンダムMk-V"
    assert loaded.brand == "Bandai Spirits"
    assert loaded.search_query_override == "Custom Query Keep Me"

    # Price update with market_price_used=None must keep existing 11000 while updating new price
    price_update = CatalogPriceUpdate(
        item_id="item_1",
        market_price_new=13500,
        market_price_used=None,
        price_source="rakuten",
        price_updated_at="2026-11-22 10:00:00",
    )
    assert client.update_catalog_prices([price_update]) == {"updated": 1, "skipped": 0}

    updated = client.get_catalog_items()[0]
    assert updated.market_price_new == 13500
    assert updated.market_price_used == 11000
    assert updated.price_source == "rakuten"
    assert updated.price_updated_at == "2026-11-22 10:00:00"
    assert updated.search_query_override == "Custom Query Keep Me"


def test_append_price_history_is_idempotent_per_day() -> None:
    spreadsheet = FakeSpreadsheet()
    client = HobbySheetsClient(spreadsheet)  # type: ignore[arg-type]

    rec1 = PriceHistoryRecord(
        log_id="item_1_20261122",
        item_id="item_1",
        recorded_at="2026-11-22 10:00:00",
        price_new=13500,
        price_used=11000,
        source="rakuten,surugaya",
        sample_size=6,
    )
    rec_dup = rec1.model_copy(update={"price_new": 14000})
    rec2 = PriceHistoryRecord(
        log_id="item_1_20261129",
        item_id="item_1",
        recorded_at="2026-11-29 10:00:00",
        price_new=13800,
        price_used=11200,
        source="rakuten,surugaya",
        sample_size=7,
    )

    assert client.append_price_history([rec1]) == {"added": 1, "skipped": 0}
    assert client.append_price_history([rec_dup, rec2]) == {"added": 1, "skipped": 1}
    assert len(client.get_price_history()) == 2


def test_build_bootstrap_requests_configures_formats_and_dropdowns() -> None:
    requests = build_bootstrap_requests({TAB_CATALOG: 10, TAB_INVENTORY: 20, TAB_PRICE_HISTORY: 30})
    frozen = [r for r in requests if "updateSheetProperties" in r]
    validations = [r["setDataValidation"] for r in requests if "setDataValidation" in r]
    formats = [
        r["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"]
        for r in requests
        if "repeatCell" in r and "numberFormat" in r["repeatCell"]["cell"]["userEnteredFormat"]
    ]

    assert len(frozen) == 3
    assert all(
        r["updateSheetProperties"]["properties"]["gridProperties"]["frozenRowCount"] == 1
        for r in frozen
    )
    assert len(validations) == 4  # brand, shop, condition, status
    assert all(v["rule"]["condition"]["type"] == "ONE_OF_LIST" for v in validations)
    assert any(fmt["pattern"] == "¥#,##0" for fmt in formats)
    assert any(fmt["pattern"] == "yyyy-mm-dd" for fmt in formats)
    assert any(fmt["pattern"] == "yyyy-mm-dd hh:mm:ss" for fmt in formats)
