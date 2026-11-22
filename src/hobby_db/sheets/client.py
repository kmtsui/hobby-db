"""Idempotent Google Sheets batch upsert and append client."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from hobby_db.config import (
    GOOGLE_SCOPES,
    TAB_CATALOG,
    TAB_INVENTORY,
    TAB_PRICE_HISTORY,
    Settings,
)
from hobby_db.models import (
    CatalogItem,
    CatalogPriceUpdate,
    InventoryEntry,
    PriceHistoryRecord,
)

# Metadata fields eligible to backfill if blank on an existing Catalog row
_CATALOG_BACKFILL_FIELDS: tuple[str, ...] = (
    "jan_code",
    "brand",
    "series",
    "msrp_jpy",
    "release_date",
    "image_url",
)


def col_index_to_letter(index_1based: int) -> str:
    """Convert a 1-based column index to an A1 notation column letter."""
    if index_1based < 1:
        raise ValueError(f"Column index must be >= 1, got {index_1based}")
    letters: list[str] = []
    num = index_1based
    while num > 0:
        num, rem = divmod(num - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def build_gspread_client(settings: Settings) -> gspread.Client:
    """Authenticate gspread using inline service account JSON or a credentials file."""
    if settings.google_service_account_json:
        info = json.loads(settings.google_service_account_json)
        creds = Credentials.from_service_account_info(info, scopes=list(GOOGLE_SCOPES))
        return gspread.authorize(creds)

    sa_path = settings.google_service_account_file
    if not sa_path.exists():
        raise FileNotFoundError(
            f"Service account credentials file not found: {sa_path}. "
            "Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON."
        )
    creds = Credentials.from_service_account_file(str(sa_path), scopes=list(GOOGLE_SCOPES))
    return gspread.authorize(creds)


class HobbySheetsClient:
    """Idempotent reader/writer for the 3-tab Hobby Collection Google Sheet.

    Never clears or overwrites whole worksheets. All updates target specific
    A1 cells via ``batch_update`` or append unseen keyed rows via ``append_rows``.
    """

    def __init__(self, spreadsheet: gspread.Spreadsheet) -> None:
        self.spreadsheet = spreadsheet

    @classmethod
    def from_settings(cls, settings: Settings, sheet_id: str | None = None) -> HobbySheetsClient:
        target_id = (sheet_id or settings.google_sheet_id).strip()
        if not target_id:
            raise ValueError(
                "GOOGLE_SHEET_ID is required. Run scripts/bootstrap_sheet.py first or set it in .env."
            )
        gc = build_gspread_client(settings)
        return cls(gc.open_by_key(target_id))

    def _get_worksheet(self, tab_name: str) -> gspread.Worksheet:
        return self.spreadsheet.worksheet(tab_name)

    def _read_tab(self, tab_name: str) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
        ws = self._get_worksheet(tab_name)
        values = ws.get_all_values()
        if not values:
            return [], []

        headers = [str(col).strip() for col in values[0]]
        rows: list[tuple[int, dict[str, str]]] = []
        for row_num, raw_row in enumerate(values[1:], start=2):
            if not any(str(cell).strip() for cell in raw_row):
                continue
            row_dict = {
                header: (str(raw_row[idx]).strip() if idx < len(raw_row) else "")
                for idx, header in enumerate(headers)
                if header
            }
            rows.append((row_num, row_dict))
        return headers, rows

    def get_catalog_items(self) -> list[CatalogItem]:
        """Load all non-empty rows from ``Catalog``."""
        _, rows = self._read_tab(TAB_CATALOG)
        return [CatalogItem.from_sheet_row(row) for _, row in rows if row.get("item_id")]

    def get_inventory_entries(self) -> list[InventoryEntry]:
        """Load all non-empty rows from ``Inventory``."""
        _, rows = self._read_tab(TAB_INVENTORY)
        return [InventoryEntry.from_sheet_row(row) for _, row in rows if row.get("entry_id")]

    def get_price_history(self) -> list[PriceHistoryRecord]:
        """Load all non-empty rows from ``PriceHistory``."""
        _, rows = self._read_tab(TAB_PRICE_HISTORY)
        return [PriceHistoryRecord.from_sheet_row(row) for _, row in rows if row.get("log_id")]

    def upsert_catalog_items(self, items: Sequence[CatalogItem]) -> dict[str, int]:
        """Append new ``Catalog`` items and backfill blank metadata cells on existing items."""
        ws = self._get_worksheet(TAB_CATALOG)
        headers, rows = self._read_tab(TAB_CATALOG)
        col_map = {name: idx + 1 for idx, name in enumerate(headers)}

        existing_by_id: dict[str, tuple[int, dict[str, str]]] = {}
        for row_num, row_dict in rows:
            item_id = row_dict.get("item_id", "")
            if item_id:
                existing_by_id[item_id] = (row_num, row_dict)

        new_rows: list[list[str | int]] = []
        cell_updates: list[dict[str, Any]] = []
        seen_new_ids: set[str] = set()
        updated_item_ids: set[str] = set()
        skipped = 0

        for item in items:
            if not item.item_id:
                skipped += 1
                continue
            if item.item_id in existing_by_id:
                row_num, existing_row = existing_by_id[item.item_id]
                row_changed = False
                for field in _CATALOG_BACKFILL_FIELDS:
                    if field not in col_map:
                        continue
                    incoming_val = getattr(item, field)
                    if existing_row.get(field, "") == "" and incoming_val not in (None, ""):
                        cell_ref = f"{col_index_to_letter(col_map[field])}{row_num}"
                        cell_updates.append({"range": cell_ref, "values": [[incoming_val]]})
                        existing_row[field] = str(incoming_val)
                        row_changed = True
                if row_changed:
                    updated_item_ids.add(item.item_id)
                else:
                    skipped += 1
            elif item.item_id not in seen_new_ids:
                seen_new_ids.add(item.item_id)
                new_rows.append(item.to_sheet_row())
            else:
                skipped += 1

        if cell_updates:
            ws.batch_update(cell_updates, value_input_option="USER_ENTERED")
        if new_rows:
            ws.append_rows(new_rows, value_input_option="USER_ENTERED")

        return {
            "added": len(new_rows),
            "updated": len(updated_item_ids),
            "skipped": skipped,
        }

    def sync_inventory_entries(self, entries: Sequence[InventoryEntry]) -> dict[str, int]:
        """Append only unseen ``entry_id`` rows to ``Inventory``, preserving manual edits."""
        ws = self._get_worksheet(TAB_INVENTORY)
        _, rows = self._read_tab(TAB_INVENTORY)
        existing_ids = {
            row_dict.get("entry_id", "") for _, row_dict in rows if row_dict.get("entry_id")
        }

        new_rows: list[list[str | int]] = []
        skipped = 0
        for entry in entries:
            if not entry.entry_id or entry.entry_id in existing_ids:
                skipped += 1
                continue
            existing_ids.add(entry.entry_id)
            new_rows.append(entry.to_sheet_row())

        if new_rows:
            ws.append_rows(new_rows, value_input_option="USER_ENTERED")

        return {"added": len(new_rows), "skipped": skipped}

    def update_catalog_prices(self, updates: Sequence[CatalogPriceUpdate]) -> dict[str, int]:
        """Batch-update only market price columns on matching ``Catalog`` rows."""
        ws = self._get_worksheet(TAB_CATALOG)
        headers, rows = self._read_tab(TAB_CATALOG)
        col_map = {name: idx + 1 for idx, name in enumerate(headers)}
        row_by_id = {
            row_dict.get("item_id", ""): row_num
            for row_num, row_dict in rows
            if row_dict.get("item_id")
        }

        cell_updates: list[dict[str, Any]] = []
        updated_count = 0
        skipped_count = 0

        for update in updates:
            row_num = row_by_id.get(update.item_id)
            if row_num is None:
                skipped_count += 1
                continue

            item_cells: list[dict[str, Any]] = []
            if update.market_price_new is not None and "market_price_new" in col_map:
                ref = f"{col_index_to_letter(col_map['market_price_new'])}{row_num}"
                item_cells.append({"range": ref, "values": [[update.market_price_new]]})
            if update.market_price_used is not None and "market_price_used" in col_map:
                ref = f"{col_index_to_letter(col_map['market_price_used'])}{row_num}"
                item_cells.append({"range": ref, "values": [[update.market_price_used]]})
            if update.price_source and "price_source" in col_map:
                ref = f"{col_index_to_letter(col_map['price_source'])}{row_num}"
                item_cells.append({"range": ref, "values": [[update.price_source]]})
            if update.price_updated_at and "price_updated_at" in col_map:
                ref = f"{col_index_to_letter(col_map['price_updated_at'])}{row_num}"
                item_cells.append({"range": ref, "values": [[update.price_updated_at]]})

            if item_cells:
                cell_updates.extend(item_cells)
                updated_count += 1
            else:
                skipped_count += 1

        if cell_updates:
            ws.batch_update(cell_updates, value_input_option="USER_ENTERED")

        return {"updated": updated_count, "skipped": skipped_count}

    def append_price_history(
        self,
        records: Sequence[PriceHistoryRecord],
    ) -> dict[str, int]:
        """Append unseen ``log_id`` rows to ``PriceHistory``."""
        ws = self._get_worksheet(TAB_PRICE_HISTORY)
        _, rows = self._read_tab(TAB_PRICE_HISTORY)
        existing_ids = {
            row_dict.get("log_id", "") for _, row_dict in rows if row_dict.get("log_id")
        }

        new_rows: list[list[str | int]] = []
        skipped = 0
        for record in records:
            if not record.log_id or record.log_id in existing_ids:
                skipped += 1
                continue
            existing_ids.add(record.log_id)
            new_rows.append(record.to_sheet_row())

        if new_rows:
            ws.append_rows(new_rows, value_input_option="USER_ENTERED")

        return {"added": len(new_rows), "skipped": skipped}
