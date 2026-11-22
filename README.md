# Hobby Collection Database (`hobby-db`)

Automated personal hobby collection pipeline (Gunpla, figures, Premium Bandai orders) backed by **Google Sheets** and accessed via **AppSheet** on mobile and PC.

## Features
- **One-Command Sheet Bootstrap:** Creates `Catalog`, `Inventory`, and `PriceHistory` tabs with frozen headers, `¥#,##0` / `YYYY-MM-DD` / DateTime formats, Data Validation dropdowns, and `SEED_*` type-inference rows.
- **Premium Bandai Order Crawler:** Playwright crawler with persistent manual login state (`.auth/pbandai_state.json`), isolated `SELECTORS`, `--dump-html` debugging, and UTF-8 BOM CSV export.
- **Idempotent Google Sheets Sync:** Deterministic `item_id` (JAN or normalized title hash), `entry_id` (`pbandai_{order_id}_{line_index}` or `_u1.._uN`), and `log_id` (`{item_id}_{YYYYMMDD}`). Never overwrites manual mobile edits in `Inventory`.
- **Market Price Sync:** Rakuten Ichiba API (sealed/new) + Surugaya (used) with junk-listing filtering, IQR outlier removal, median aggregation, and weekly GitHub Actions automation.

---

## Quick Start

### 1. Install Dependencies
```bash
uv sync
uv run playwright install chromium
```

### 2. Configure Credentials & Environment
```bash
cp .env.example .env
```
Place your Google Cloud Service Account JSON at `secrets/service_account.json` (enable Google Sheets API and Google Drive API in GCP), then edit `.env`.

### 3. Bootstrap the Google Sheet
Create a new Google Sheet and share it with your personal Google account (for AppSheet access):
```bash
uv run python scripts/bootstrap_sheet.py --share-with you@example.com
```
Copy the printed `GOOGLE_SHEET_ID=...` into `.env`. To initialize an existing Sheet you already created, pass `--sheet-id <ID>` (after sharing that Sheet with the service account email).

Next, complete the short manual checklist in [APPSHEET_SETUP.md](APPSHEET_SETUP.md).

### 4. Link AppSheet & Add Views for `Inventory` and `PriceHistory`
By default AppSheet only generates a navigation tab for `Catalog`. To view and edit all three tabs on PC and mobile:
1. Under **Data -> Add new table**, add `Inventory` and `PriceHistory`, then link `item_id` (`Ref -> Catalog`) per [APPSHEET_SETUP.md](APPSHEET_SETUP.md).
2. Under **Views -> Primary Navigation -> +**:
   - Add **Inventory** (`For this data: Inventory`, View type: `table` or `deck`, sort by `order_date` descending, optionally group by `status`).
   - Add **PriceHistory** (`For this data: PriceHistory`, View type: `table` or `chart`; optional since history also renders inline on each `Catalog` detail screen).
3. Click **Save**. Keep the app in **Prototype (Not Deployed)** mode on the AppSheet FREE plan (do not disable **Require user sign-in**) and open it via **Share -> Copy sharing links -> Browser link** on PC or the AppSheet mobile app.

---

## Daily Workflows

### Login to Premium Bandai (First Run or Expired Session)
```bash
uv run python scripts/crawl_pbandai.py --login
```

### Crawl Orders & Sync Directly to Google Sheets
```bash
uv run python scripts/crawl_pbandai.py --out data/pbandai_orders.csv --import
```
Use `--dump-html` to inspect raw HTML if P-Bandai markup changes, or `--no-expand-quantity` to keep multi-unit orders as a single `Inventory` row.

### Offline HTML Fallback (If Playwright Is Blocked by Bot Protection)
Save each page of `https://p-bandai.jp/myorder/` from your regular browser (**Webpage, HTML Only**) into `data/saved_html/`, then parse and import offline:
```bash
uv run python scripts/crawl_pbandai.py --from-html data/saved_html --out data/pbandai_orders.csv --import
```

### Import an Existing CSV Manually
```bash
uv run python scripts/import_orders.py data/pbandai_orders.csv
```

### Refresh Stale Market Prices
By default, `update_prices.py` crawls both `new` and `used` prices across all 6 token-free sources (`mandarake`, `surugaya`, `yahoo_shopping`, `kakaku`, `yahoo_auctions`, `jungle`, plus `rakuten` if `RAKUTEN_APP_ID` is set) and computes pooled IQR-filtered medians.

Test on 5 items without modifying the Sheet:
```bash
uv run python scripts/update_prices.py --limit 5 --dry-run
```
Run a full stale refresh (default: older than `STALE_DAYS=7`), or pass `--force` / `--sources`:
```bash
uv run python scripts/update_prices.py
uv run python scripts/update_prices.py --force --sources mandarake,surugaya,yahoo_shopping,jungle
```

---

## GitHub Actions Weekly Price Sync
Add these repository secrets in GitHub (**Settings -> Secrets and variables -> Actions**):
- `GOOGLE_SERVICE_ACCOUNT_JSON`: Full contents of `secrets/service_account.json`
- `GOOGLE_SHEET_ID`: Target spreadsheet ID
- `RAKUTEN_APP_ID`: Rakuten developer Application ID
- `RAKUTEN_AFFILIATE_ID` *(optional)*

The workflow `.github/workflows/price_sync.yml` runs every Sunday at 03:00 UTC and supports manual triggers.

---

## Quantity Convention
By default (`expand_quantity=True`), a Premium Bandai line item with `quantity == 1` produces `entry_id = pbandai_{order_id}_{line_index}`. Items with `quantity > 1` expand into `pbandai_{order_id}_{line_index}_u1`, `_u2`, ... (shipping fee attached to `_u1`) so each physical box tracks its own condition, storage location, and sale price in AppSheet. Pass `--no-expand-quantity` to keep one row per order line instead.

---

## Development (Lint, Format, Pre-commit & Tests)
Install dev tools via `uv sync`, then enable the local Git pre-commit hook (runs Ruff lint, Ruff format check, and pytest before every commit):
```bash
uv run pre-commit install
```

Run checks manually:
```bash
uv run ruff check .
uv run ruff format .
uv run pytest -v
uv run pre-commit run --all-files
```
