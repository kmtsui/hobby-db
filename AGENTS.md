# Repository Agent Guidelines (`hobby-db`)

## Project Overview
`hobby-db` automates a personal hobby collection database (Gunpla, figures, Premium Bandai orders) backed by a 3-tab Google Sheet (`Catalog`, `Inventory`, `PriceHistory`) and accessed via AppSheet. It combines:
- A Playwright crawler for `https://p-bandai.jp/myorder/` exporting UTF-8 BOM CSVs.
- An idempotent CSV ingestor and Google Sheets sync client preserving manual mobile edits.
- Market price fetchers (Rakuten Ichiba API for sealed/new, Surugaya for used) with junk filtering, outlier removal, median aggregation, and weekly GitHub Actions automation.

## Environment & Commands
Always invoke Python tools through `uv run` (never bare `python`, which may resolve to a broken stub on Windows).

```bash
# Install runtime + dev dependencies
uv sync

# Lint and formatting (Ruff)
uv run ruff check .
uv run ruff check --fix .
uv run ruff format --check .
uv run ruff format .

# Unit tests
uv run pytest -v

# Pre-commit hook management
uv run pre-commit install
uv run pre-commit run --all-files

# CLI entry points
uv run python scripts/bootstrap_sheet.py --help
uv run python scripts/crawl_pbandai.py --help
uv run python scripts/import_orders.py --help
uv run python scripts/update_prices.py --help
```

## Module Map
- `src/hobby_db/config.py`: Sheet tab names, exact column tuples, enum lists, and `Settings` loader.
- `src/hobby_db/models.py`: Pydantic v2 models (`CatalogItem`, `CatalogPriceUpdate`, `InventoryEntry`, `PriceHistoryRecord`, `PBandaiOrderRow`).
- `src/hobby_db/normalize.py`: NFKC title cleaning, Japanese date/price parsers, brand/series inference, and deterministic IDs (`make_item_id`, `make_entry_id`, `make_log_id`).
- `src/hobby_db/crawlers/pbandai.py`: Isolated `SELECTORS` dict, pure BeautifulSoup HTML helpers (supporting live cards and saved `table.myp-table-order` HTML), consolidated-order resolution, BOM CSV writer, and Playwright `login_and_save_state` / `crawl_orders`.
- `src/hobby_db/ingestors/pbandai_csv.py`: Parses P-Bandai CSV exports into deduplicated `CatalogItem` and `InventoryEntry` rows.
- `src/hobby_db/sheets/bootstrap.py`: Google Sheets `batchUpdate` formatting, dropdown validation rules, and `SEED_*` sample rows.
- `src/hobby_db/sheets/client.py`: Idempotent `HobbySheetsClient` (cell-level `batch_update` and `append_rows`).
- `src/hobby_db/pricers/`: `BasePricer`, junk keyword & IQR outlier filtering, `RakutenPricer`, and `SurugayaPricer`.
- `scripts/`: Thin CLI wrappers (`bootstrap_sheet.py`, `crawl_pbandai.py`, `import_orders.py`, `update_prices.py`).
- `tests/`: Offline unit tests with mock HTML fixtures, `httpx.MockTransport`, and an in-memory fake `gspread` spreadsheet.

## Hard Engineering Invariants
1. **Never overwrite entire Google Sheets tabs.** Do not call `set_with_dataframe`, `worksheet.clear()`, or bulk-replace row ranges.
2. **Preserve manual mobile edits in `Inventory`.** `sync_inventory_entries` is strictly append-only for unseen `entry_id`s. Never overwrite existing rows or touch `status`, `storage_location`, `condition`, `sold_price_jpy`, or `notes`.
3. **Restrict `Catalog` price mutations.** `update_catalog_prices` may only batch-update `market_price_new`, `market_price_used`, `price_source`, and `price_updated_at`. When a price field is `None`, leave the existing Sheet value intact rather than clearing it.
4. **Preserve deterministic keys and quantity convention.**
   - `item_id`: cleaned `jan_code` if present, otherwise `item_{sha256(normalized_title.lower())[:12]}`.
   - `entry_id`: `pbandai_{order_id}_{line_index}` for `quantity == 1`, expanding to `_u1.._u{quantity}` when `expand_quantity=True` (shipping assigned to `_u1` / line 1 only).
   - `log_id`: `{item_id}_{YYYYMMDD}` (append-only deduplication in `PriceHistory`).
5. **Keep schema & docs synchronized.** Any column or enum change must be updated consistently across `src/hobby_db/config.py`, `src/hobby_db/models.py`, `src/hobby_db/sheets/bootstrap.py`, `APPSHEET_SETUP.md`, and `tests/test_sheets_client.py`.
6. **Isolate fragile HTML selectors and keep parsers pure.** Keep CSS selectors in `SELECTORS` (`crawlers/pbandai.py`) and `SURUGAYA_SELECTORS` (`pricers/surugaya.py`). Keep HTML parsing pure BeautifulSoup functions taking strings, and import `playwright.sync_api` lazily inside runtime methods so unit tests require no browser binary.
7. **Respect Japanese text normalization rules.** `normalize_title()` applies NFKC, strips `【...】` and parenthesized shipping/sale tags, and normalizes Unicode hyphens while preserving Japanese katakana and the prolonged sound mark `ー` (`U+30FC`). Ruff keeps `RUF001`, `RUF002`, and `RUF003` ignored in `pyproject.toml` because full-width Japanese literals/examples are intentional.
8. **Security, privacy, and polite scraping.**
   - Never commit `.env`, `secrets/`, service account JSONs, `.auth/`, `data/*.csv`, or `data/saved_html/`.
   - Never automate CAPTCHA/2FA bypasses; P-Bandai login must remain manual via `--login`.
   - Keep randomized polite delays in crawlers/pricers (overridable in constructors for fast unit tests).
   - Skip `SEED_*` sample rows in `scripts/update_prices.py`.

## Pre-Completion Checklist
Before finishing any change, ensure all three checks pass:
```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```
