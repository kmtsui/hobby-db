# AppSheet Minimal-Click Setup Checklist

`scripts/bootstrap_sheet.py` already created the `Catalog`, `Inventory`, and `PriceHistory` tabs, froze row 1, applied `¥#,##0` / `YYYY-MM-DD` / DateTime formats, configured Google Sheets dropdown validations (`brand`, `shop`, `condition`, `status`), and inserted one `SEED_*` sample row per tab so AppSheet auto-detects column types.

Complete only these manual clicks in the AppSheet editor:

## 1. Connect the Google Sheet
1. Go to [AppSheet](https://www.appsheet.com) -> **Create** -> **App** -> **Start with existing data**.
2. Choose **Google Sheets** and select your bootstrapped spreadsheet (default: `Hobby Collection DB`).
3. AppSheet opens with `Catalog` loaded. In **Data** -> **Add new table**, add `Inventory` and `PriceHistory`.

## 2. Verify Keys & Labels
Open **Data** -> each table's columns and confirm (AppSheet usually auto-selects these from row 1 + seed data):
- **Catalog**: Key = `item_id`, Label = `title`, Image = `image_url` (`Image` type), Price columns = `Price` (`JPY` / `¥`).
- **Inventory**: Key = `entry_id`, Label = `order_id`, Price columns = `Price`.
- **PriceHistory**: Key = `log_id`, Label = `recorded_at` (`DateTime` type), Price columns = `Price`.

## 3. Link `item_id` Foreign Keys (`Ref`)
1. In **Data -> Inventory -> `item_id`**:
   - Change **Type** to `Ref`.
   - Set **Source table** to `Catalog`.
   - Check **Is a part of?** (optional, shows owned copies inline on each Catalog detail view).
2. In **Data -> PriceHistory -> `item_id`**:
   - Change **Type** to `Ref`.
   - Set **Source table** to `Catalog`.
   - Check **Is a part of?** so price trend history renders directly inside each Catalog item view.
3. Click **Save**.

## 4. Add Portfolio Virtual Columns on `Inventory`
In **Data -> Inventory -> Add virtual column**, paste these two formulas:

### `unrealized_gain_jpy` (Type: `Price`, Currency: `¥`)
Compares the current sealed/used market price (or realized `sold_price_jpy` if sold) against purchase cost plus shipping:
```appsheet
IF(
  [status] = "Cancelled",
  0,
  IF(
    [status] = "Sold",
    [sold_price_jpy] - ([purchase_price_jpy] + [shipping_jpy]),
    IF(
      [condition] = "Sealed",
      IF(
        ISNOTBLANK([item_id].[market_price_new]),
        [item_id].[market_price_new],
        [item_id].[market_price_used]
      ),
      IF(
        ISNOTBLANK([item_id].[market_price_used]),
        [item_id].[market_price_used],
        [item_id].[market_price_new]
      )
    ) - ([purchase_price_jpy] + [shipping_jpy])
  )
)
```

### `roi_pct` (Type: `Percent`, Decimal digits: `1`)
Uses `DECIMAL(...)` to avoid AppSheet integer-division truncation:
```appsheet
IF(
  ([purchase_price_jpy] + [shipping_jpy]) > 0,
  DECIMAL([unrealized_gain_jpy]) / DECIMAL([purchase_price_jpy] + [shipping_jpy]),
  0.0
)
```

## 5. Clean Up Seed Rows
Once **Save** finishes cleanly in AppSheet and column types look correct, open your Google Sheet and delete row 2 (`SEED_*`) from `Catalog`, `Inventory`, and `PriceHistory`.
