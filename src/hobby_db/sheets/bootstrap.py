"""Google Sheet initializer with AppSheet-ready formatting, dropdowns, and seed rows."""

from __future__ import annotations

from typing import Any

import gspread

from hobby_db.config import (
    BRAND_VALUES,
    CATALOG_COLUMNS,
    CONDITION_VALUES,
    INVENTORY_COLUMNS,
    PRICE_HISTORY_COLUMNS,
    SHOP_VALUES,
    STATUS_VALUES,
    TAB_CATALOG,
    TAB_INVENTORY,
    TAB_PRICE_HISTORY,
    Settings,
)
from hobby_db.models import CatalogItem, InventoryEntry, PriceHistoryRecord
from hobby_db.sheets.client import build_gspread_client

_TAB_SCHEMAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (TAB_CATALOG, CATALOG_COLUMNS),
    (TAB_INVENTORY, INVENTORY_COLUMNS),
    (TAB_PRICE_HISTORY, PRICE_HISTORY_COLUMNS),
)

_DATA_VALIDATION_END_ROW = 1000

# Flagged sample rows so AppSheet auto-detects Price, Date, DateTime, Image, and Enum types
# immediately upon connecting the Sheet. Delete these SEED_* rows after linking AppSheet.
SEED_CATALOG_ITEM = CatalogItem(
    item_id="SEED_4573102650000",
    jan_code="4573102650000",
    title="[SEED SAMPLE - DELETE LATER] MG 1/100 Zeta Gundam Ver.Ka",
    brand="Bandai Spirits",
    series="Gunpla",
    msrp_jpy=7150,
    release_date="2023-04-29",
    image_url="https://placehold.co/600x600.png",
    market_price_new=7500,
    market_price_used=6200,
    price_source="rakuten,surugaya",
    price_updated_at="2026-01-01 00:00:00",
    search_query_override="MG Zeta Gundam Ver.Ka",
)

SEED_INVENTORY_ENTRY = InventoryEntry(
    entry_id="SEED_pbandai_00000000_1",
    item_id="SEED_4573102650000",
    order_id="SEED-00000000",
    shop="Premium Bandai",
    order_date="2026-01-01",
    purchase_price_jpy=7150,
    shipping_jpy=660,
    condition="Sealed",
    status="Pre-ordered",
    storage_location="Closet Shelf A",
    sold_price_jpy=0,
    notes="SEED ROW - safe to delete after AppSheet schema generation",
)

SEED_PRICE_HISTORY_RECORD = PriceHistoryRecord(
    log_id="SEED_4573102650000_20260101",
    item_id="SEED_4573102650000",
    recorded_at="2026-01-01 00:00:00",
    price_new=7500,
    price_used=6200,
    source="rakuten,surugaya",
    sample_size=5,
)


def _format_range_request(
    sheet_id: int,
    col_idx: int,
    number_format: dict[str, str],
) -> dict[str, Any]:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": _DATA_VALIDATION_END_ROW,
                "startColumnIndex": col_idx,
                "endColumnIndex": col_idx + 1,
            },
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": number_format,
                }
            },
            "fields": "userEnteredFormat.numberFormat",
        }
    }


def _dropdown_request(
    sheet_id: int,
    col_idx: int,
    values: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": _DATA_VALIDATION_END_ROW,
                "startColumnIndex": col_idx,
                "endColumnIndex": col_idx + 1,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": v} for v in values],
                },
                "showCustomUi": True,
                "strict": False,
            },
        }
    }


def build_bootstrap_requests(sheet_ids: dict[str, int]) -> list[dict[str, Any]]:
    """Build Google Sheets API batchUpdate requests for formatting, types, and dropdowns."""
    requests: list[dict[str, Any]] = []

    for tab_name, columns in _TAB_SCHEMAS:
        sheet_id = sheet_ids[tab_name]
        col_count = len(columns)

        # Freeze header row 1 and style headers
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            }
        )
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": col_count,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "backgroundColor": {"red": 0.91, "green": 0.933, "blue": 0.961},
                        }
                    },
                    "fields": "userEnteredFormat(textFormat.bold,backgroundColor)",
                }
            }
        )
        requests.append(
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": col_count,
                    }
                }
            }
        )

    # Explicit column formats so AppSheet infers Text, Price, Date, DateTime, and Number
    cat_idx = {col: idx for idx, col in enumerate(CATALOG_COLUMNS)}
    inv_idx = {col: idx for idx, col in enumerate(INVENTORY_COLUMNS)}
    hist_idx = {col: idx for idx, col in enumerate(PRICE_HISTORY_COLUMNS)}

    cat_id = sheet_ids[TAB_CATALOG]
    for text_col in ("item_id", "jan_code"):
        requests.append(
            _format_range_request(cat_id, cat_idx[text_col], {"type": "TEXT", "pattern": "@"})
        )
    for currency_col in ("msrp_jpy", "market_price_new", "market_price_used"):
        requests.append(
            _format_range_request(
                cat_id, cat_idx[currency_col], {"type": "CURRENCY", "pattern": "¥#,##0"}
            )
        )
    requests.append(
        _format_range_request(
            cat_id, cat_idx["release_date"], {"type": "DATE", "pattern": "yyyy-mm-dd"}
        )
    )
    requests.append(
        _format_range_request(
            cat_id,
            cat_idx["price_updated_at"],
            {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"},
        )
    )
    requests.append(_dropdown_request(cat_id, cat_idx["brand"], BRAND_VALUES))

    inv_id = sheet_ids[TAB_INVENTORY]
    for text_col in ("entry_id", "item_id", "order_id"):
        requests.append(
            _format_range_request(inv_id, inv_idx[text_col], {"type": "TEXT", "pattern": "@"})
        )
    for currency_col in ("purchase_price_jpy", "shipping_jpy", "sold_price_jpy"):
        requests.append(
            _format_range_request(
                inv_id, inv_idx[currency_col], {"type": "CURRENCY", "pattern": "¥#,##0"}
            )
        )
    requests.append(
        _format_range_request(
            inv_id, inv_idx["order_date"], {"type": "DATE", "pattern": "yyyy-mm-dd"}
        )
    )
    requests.append(_dropdown_request(inv_id, inv_idx["shop"], SHOP_VALUES))
    requests.append(_dropdown_request(inv_id, inv_idx["condition"], CONDITION_VALUES))
    requests.append(_dropdown_request(inv_id, inv_idx["status"], STATUS_VALUES))

    hist_id = sheet_ids[TAB_PRICE_HISTORY]
    for text_col in ("log_id", "item_id"):
        requests.append(
            _format_range_request(hist_id, hist_idx[text_col], {"type": "TEXT", "pattern": "@"})
        )
    for currency_col in ("price_new", "price_used"):
        requests.append(
            _format_range_request(
                hist_id, hist_idx[currency_col], {"type": "CURRENCY", "pattern": "¥#,##0"}
            )
        )
    requests.append(
        _format_range_request(
            hist_id,
            hist_idx["recorded_at"],
            {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"},
        )
    )
    requests.append(
        _format_range_request(
            hist_id, hist_idx["sample_size"], {"type": "NUMBER", "pattern": "#,##0"}
        )
    )

    return requests


def _ensure_tab_with_headers(
    spreadsheet: gspread.Spreadsheet,
    tab_name: str,
    headers: tuple[str, ...],
) -> gspread.Worksheet:
    try:
        ws = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(headers))

    existing_values = ws.get_all_values()
    if not existing_values or existing_values[0] != list(headers):
        ws.update([list(headers)], "A1", value_input_option="USER_ENTERED")
    return ws


def bootstrap_spreadsheet(
    settings: Settings,
    sheet_id: str | None = None,
    title: str | None = None,
    share_with: str | None = None,
    include_seed: bool = True,
) -> dict[str, str]:
    """Create or configure a Google Sheet with AppSheet-ready tabs, formatting, and seed rows."""
    gc = build_gspread_client(settings)
    target_sheet_id = (sheet_id or settings.google_sheet_id).strip()
    target_title = (title or settings.google_sheet_title).strip() or "Hobby Collection DB"

    spreadsheet = gc.open_by_key(target_sheet_id) if target_sheet_id else gc.create(target_title)

    if share_with:
        spreadsheet.share(share_with.strip(), perm_type="user", role="writer")

    worksheets: dict[str, gspread.Worksheet] = {}
    for tab_name, headers in _TAB_SCHEMAS:
        worksheets[tab_name] = _ensure_tab_with_headers(spreadsheet, tab_name, headers)

    # Remove default empty Sheet1 if created automatically by gc.create()
    try:
        default_ws = spreadsheet.worksheet("Sheet1")
        if len(spreadsheet.worksheets()) > len(_TAB_SCHEMAS) and not default_ws.get_all_values():
            spreadsheet.del_worksheet(default_ws)
    except gspread.WorksheetNotFound:
        pass

    sheet_ids = {name: ws.id for name, ws in worksheets.items()}
    requests = build_bootstrap_requests(sheet_ids)
    spreadsheet.batch_update({"requests": requests})

    if include_seed:
        seed_map = {
            TAB_CATALOG: SEED_CATALOG_ITEM.to_sheet_row(),
            TAB_INVENTORY: SEED_INVENTORY_ENTRY.to_sheet_row(),
            TAB_PRICE_HISTORY: SEED_PRICE_HISTORY_RECORD.to_sheet_row(),
        }
        for tab_name, seed_row in seed_map.items():
            ws = worksheets[tab_name]
            if len(ws.get_all_values()) <= 1:
                ws.append_row(seed_row, value_input_option="USER_ENTERED")

    return {
        "sheet_id": spreadsheet.id,
        "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}",
    }
