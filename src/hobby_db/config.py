"""Configuration constants, schema definitions, and environment loading."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Google Sheets tab names
TAB_CATALOG = "Catalog"
TAB_INVENTORY = "Inventory"
TAB_PRICE_HISTORY = "PriceHistory"

# Exact ordered columns per tab
CATALOG_COLUMNS: tuple[str, ...] = (
    "item_id",
    "jan_code",
    "title",
    "brand",
    "series",
    "msrp_jpy",
    "release_date",
    "image_url",
    "market_price_new",
    "market_price_used",
    "price_source",
    "price_updated_at",
    "search_query_override",
)

INVENTORY_COLUMNS: tuple[str, ...] = (
    "entry_id",
    "item_id",
    "order_id",
    "shop",
    "order_date",
    "purchase_price_jpy",
    "shipping_jpy",
    "condition",
    "status",
    "storage_location",
    "sold_price_jpy",
    "notes",
)

PRICE_HISTORY_COLUMNS: tuple[str, ...] = (
    "log_id",
    "item_id",
    "recorded_at",
    "price_new",
    "price_used",
    "source",
    "sample_size",
)

PBANDAI_CSV_COLUMNS: tuple[str, ...] = (
    "order_id",
    "order_date",
    "item_title",
    "unit_price_jpy",
    "quantity",
    "shipping_jpy",
    "order_status",
    "delivery_month",
    "image_url",
)

# Enum values shared with Google Sheets Data Validation dropdowns and AppSheet
BRAND_VALUES: tuple[str, ...] = (
    "Bandai Spirits",
    "Premium Bandai",
    "Good Smile Company",
    "Kotobukiya",
    "Alter",
    "Max Factory",
    "MegaHouse",
    "Tamashii Nations",
    "Other",
)

SHOP_VALUES: tuple[str, ...] = (
    "Premium Bandai",
    "AmiAmi",
    "Amazon JP",
    "Surugaya",
    "Mandarake",
    "Yodobashi",
    "Rakuten",
    "Mercari",
    "HobbyLink Japan",
    "Other",
)

CONDITION_VALUES: tuple[str, ...] = (
    "Sealed",
    "Opened - Mint",
    "Built",
    "Box Damaged",
    "Loose",
    "Junk",
)

STATUS_VALUES: tuple[str, ...] = (
    "Pre-ordered",
    "Owned",
    "In Storage",
    "On Display",
    "For Sale",
    "Sold",
    "Cancelled",
)

# Fields updated during market price refreshes (all other Catalog fields remain untouched)
CATALOG_PRICE_FIELDS: tuple[str, ...] = (
    "market_price_new",
    "market_price_used",
    "price_source",
    "price_updated_at",
)

# Fields owned by mobile/AppSheet edits; automated sync never overwrites existing rows
INVENTORY_PROTECTED_FIELDS: tuple[str, ...] = (
    "status",
    "storage_location",
    "condition",
    "sold_price_jpy",
    "notes",
)

GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)


class Settings(BaseModel):
    """Runtime settings loaded from environment variables and optional .env file."""

    google_service_account_file: Path = Path("secrets/service_account.json")
    google_service_account_json: str | None = None
    google_sheet_id: str = ""
    google_sheet_title: str = "Hobby Collection DB"
    rakuten_app_id: str = ""
    rakuten_affiliate_id: str = ""
    stale_days: int = Field(default=7, ge=1)
    pbandai_auth_state_path: Path = Path(".auth/pbandai_state.json")


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from optional .env file and process environment variables."""
    target_env = env_file if env_file is not None else Path(".env")
    if target_env.exists():
        load_dotenv(dotenv_path=target_env, override=False)

    stale_raw = os.getenv("STALE_DAYS", "7").strip()
    try:
        stale_days = max(1, int(stale_raw))
    except ValueError:
        stale_days = 7

    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json is not None:
        sa_json = sa_json.strip() or None

    return Settings(
        google_service_account_file=Path(
            os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "secrets/service_account.json").strip()
        ),
        google_service_account_json=sa_json,
        google_sheet_id=os.getenv("GOOGLE_SHEET_ID", "").strip(),
        google_sheet_title=os.getenv("GOOGLE_SHEET_TITLE", "Hobby Collection DB").strip()
        or "Hobby Collection DB",
        rakuten_app_id=os.getenv("RAKUTEN_APP_ID", "").strip(),
        rakuten_affiliate_id=os.getenv("RAKUTEN_AFFILIATE_ID", "").strip(),
        stale_days=stale_days,
        pbandai_auth_state_path=Path(
            os.getenv("PBANDAI_AUTH_STATE_PATH", ".auth/pbandai_state.json").strip()
        ),
    )
