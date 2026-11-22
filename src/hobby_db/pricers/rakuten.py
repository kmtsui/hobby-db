"""Rakuten Ichiba API pricer for first-hand / sealed market prices."""

from __future__ import annotations

import random
import time
from typing import Any

import httpx

from hobby_db.models import CatalogItem
from hobby_db.normalize import clean_jan_code, normalize_title
from hobby_db.pricers.base import BasePricer, PriceListing

RAKUTEN_ICHIBA_URL = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"


class RakutenPricer(BasePricer):
    """Fetch first-hand / sealed market prices via the Rakuten Ichiba Item Search API."""

    source_name = "rakuten"
    condition = "new"

    def __init__(
        self,
        app_id: str,
        affiliate_id: str = "",
        client: httpx.Client | None = None,
        hits: int = 20,
        min_delay: float = 1.0,
        max_delay: float = 1.5,
    ) -> None:
        if not app_id.strip():
            raise ValueError("RAKUTEN_APP_ID is required for RakutenPricer.")
        self.app_id = app_id.strip()
        self.affiliate_id = affiliate_id.strip()
        self.hits = hits
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=15.0)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _polite_sleep(self) -> None:
        if self.max_delay > 0:
            time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _search_keyword(self, keyword: str) -> list[PriceListing]:
        params: dict[str, Any] = {
            "applicationId": self.app_id,
            "keyword": keyword,
            "hits": self.hits,
            "availability": 1,
            "format": "json",
            "formatVersion": 2,
        }
        if self.affiliate_id:
            params["affiliateId"] = self.affiliate_id

        try:
            response = self._client.get(RAKUTEN_ICHIBA_URL, params=params)
            if response.status_code == 404:
                return []
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError:
            return []

        listings: list[PriceListing] = []
        for raw_entry in payload.get("Items", []):
            item_data = raw_entry.get("Item", raw_entry) if isinstance(raw_entry, dict) else {}
            if not isinstance(item_data, dict):
                continue

            # Keep first-hand/sealed offers only; skip used marketplace listings on Rakuten.
            if item_data.get("usedFlag") == 1:
                continue
            title = str(item_data.get("itemName", "")).strip()
            if "中古" in title:
                continue

            raw_price = item_data.get("itemPrice")
            try:
                price_jpy = int(raw_price)
            except (TypeError, ValueError):
                continue

            if price_jpy <= 0 or not title:
                continue

            listings.append(
                PriceListing(
                    title=title,
                    price_jpy=price_jpy,
                    source=self.source_name,
                    condition=self.condition,
                    url=str(item_data.get("itemUrl", "")).strip(),
                )
            )
        return listings

    def fetch_listings(self, item: CatalogItem) -> list[PriceListing]:
        """Query Rakuten by JAN code first, falling back to search_query_override or title."""
        jan = clean_jan_code(item.jan_code)
        if jan:
            self._polite_sleep()
            by_jan = self._search_keyword(jan)
            if by_jan:
                return by_jan

        fallback_query = item.search_query_override.strip() or normalize_title(item.title)
        if not fallback_query:
            return []
        self._polite_sleep()
        return self._search_keyword(fallback_query)
