"""Kakaku.com market price fetcher for new and used hobby prices."""

from __future__ import annotations

import random
import time
import urllib.parse
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from hobby_db.models import CatalogItem
from hobby_db.normalize import clean_jan_code, normalize_title, parse_jpy_int
from hobby_db.pricers.base import BasePricer, PriceListing

KAKAKU_SEARCH_BASE_URL = "https://kakaku.com/search_results/"

KAKAKU_SELECTORS: dict[str, str] = {
    "item_card": ".p-resultItem_in, .p-item",
    "title": ".p-item_name a, .p-item_name",
    "price": ".p-item_priceNum, .p-item_price",
    "link": ".p-item_name a",
}


def build_kakaku_search_url(query: str) -> str:
    """Encode a search query as Shift_JIS (`cp932`) path segment for Kakaku.com."""
    encoded = urllib.parse.quote(query.encode("cp932", errors="ignore"), safe="")
    return f"{KAKAKU_SEARCH_BASE_URL}{encoded}/"


def parse_kakaku_search_html(
    html: str | bytes,
    base_url: str = "https://kakaku.com",
    selectors: dict[str, str] | None = None,
    condition: str = "new",
) -> list[PriceListing]:
    """Parse Kakaku.com search results HTML (Shift_JIS or UTF-8) into PriceListings."""
    sel = selectors or KAKAKU_SELECTORS
    soup = BeautifulSoup(html, "html.parser")
    listings: list[PriceListing] = []

    for card in soup.select(sel["item_card"]):
        title_node = card.select_one(sel["title"])
        if title_node is None:
            continue
        title = " ".join(title_node.get_text(" ", strip=True).split())
        if not title:
            continue

        is_used_title = "中古" in title
        if condition == "new" and is_used_title:
            continue
        if condition == "used" and not is_used_title:
            continue

        price_node = card.select_one(sel["price"])
        price_jpy = parse_jpy_int(price_node.get_text(" ", strip=True) if price_node else "")
        if price_jpy is None or price_jpy <= 0:
            continue

        link_node = card.select_one(sel["link"])
        href = (link_node.get("href") or "").strip() if link_node is not None else ""
        full_url = urljoin(base_url, href) if href else ""

        listings.append(
            PriceListing(
                title=title,
                price_jpy=price_jpy,
                source="kakaku",
                condition=condition,
                url=full_url,
            )
        )

    return listings


class KakakuPricer(BasePricer):
    """Fetch new or used retail prices from Kakaku.com."""

    source_name = "kakaku"

    def __init__(
        self,
        condition: str = "new",
        client: httpx.Client | None = None,
        min_delay: float = 1.0,
        max_delay: float = 2.0,
    ) -> None:
        if condition not in ("new", "used"):
            raise ValueError(f"Unsupported condition: {condition}")
        self.condition = condition
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            },
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _polite_sleep(self) -> None:
        if self.max_delay > 0:
            time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _search_keyword(self, query: str) -> list[PriceListing]:
        effective_query = f"{query} 中古" if self.condition == "used" else query
        url = build_kakaku_search_url(effective_query)
        try:
            response = self._client.get(url)
            if response.status_code in (400, 403, 404, 429):
                return []
            response.raise_for_status()
            return parse_kakaku_search_html(response.content, condition=self.condition)
        except httpx.HTTPError:
            return []

    def fetch_listings(self, item: CatalogItem) -> list[PriceListing]:
        override = item.search_query_override.strip()
        if override:
            self._polite_sleep()
            return self._search_keyword(override)

        jan = clean_jan_code(item.jan_code)
        if jan:
            self._polite_sleep()
            by_jan = self._search_keyword(jan)
            if by_jan:
                return by_jan

        fallback = normalize_title(item.title)
        if not fallback:
            return []
        self._polite_sleep()
        return self._search_keyword(fallback)
