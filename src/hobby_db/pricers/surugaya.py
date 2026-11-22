"""Surugaya market price fetcher for second-hand hobby prices."""

from __future__ import annotations

import random
import re
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from hobby_db.models import CatalogItem
from hobby_db.normalize import clean_jan_code, normalize_title, parse_jpy_int
from hobby_db.pricers.base import BasePricer, PriceListing

SURUGAYA_SEARCH_URL = "https://www.suruga-ya.jp/search"

# Isolate Surugaya search page CSS selectors for fast updates if HTML changes.
SURUGAYA_SELECTORS: dict[str, str] = {
    "item_card": ".item_box, .search_item, div.item",
    "title": ".title a, .item_title a, p.title a, .item_name",
    "sell_price": ".price_teika strong, .item_price .price, .price .text-price-custom, .price_sell",
    "link": ".title a, .item_title a, a",
}

_BUYBACK_OR_MSRP_RE = re.compile(r"(?:定価|買取)")


def parse_surugaya_search_html(
    html: str,
    base_url: str = "https://www.suruga-ya.jp",
    selectors: dict[str, str] | None = None,
) -> list[PriceListing]:
    """Parse Surugaya search results HTML into second-hand PriceListing items."""
    sel = selectors or SURUGAYA_SELECTORS
    soup = BeautifulSoup(html, "html.parser")
    listings: list[PriceListing] = []

    for card in soup.select(sel["item_card"]):
        title_node = card.select_one(sel["title"])
        if title_node is None:
            continue
        title = title_node.get_text(" ", strip=True)
        if not title:
            continue

        price_jpy: int | None = None
        for price_node in card.select(sel["sell_price"]):
            parent_text = (
                price_node.parent.get_text(" ", strip=True)
                if price_node.parent is not None
                else price_node.get_text(" ", strip=True)
            )
            price_fragment = BeautifulSoup(str(price_node), "html.parser")
            for strike in price_fragment.select("del, s, strike, .price_del, .del"):
                strike.decompose()
            own_text = price_fragment.get_text(" ", strip=True)
            if _BUYBACK_OR_MSRP_RE.search(own_text) or (
                _BUYBACK_OR_MSRP_RE.search(parent_text) and "中古" not in parent_text
            ):
                continue
            parsed = parse_jpy_int(own_text)
            if parsed is not None and parsed > 0:
                price_jpy = parsed
                break

        if price_jpy is None:
            continue

        link_node = card.select_one(sel["link"])
        href = (link_node.get("href") or "").strip() if link_node is not None else ""
        full_url = urljoin(base_url, href) if href else ""

        listings.append(
            PriceListing(
                title=title,
                price_jpy=price_jpy,
                source="surugaya",
                condition="used",
                url=full_url,
            )
        )

    return listings


class SurugayaPricer(BasePricer):
    """Fetch second-hand prices from Surugaya with randomized 2-4s polite delays."""

    source_name = "surugaya"
    condition = "used"

    def __init__(
        self,
        client: httpx.Client | None = None,
        min_delay: float = 2.0,
        max_delay: float = 4.0,
    ) -> None:
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

    def _search_word(self, query: str) -> list[PriceListing]:
        try:
            response = self._client.get(
                SURUGAYA_SEARCH_URL,
                params={"search_word": query, "inStock": "On"},
            )
            if response.status_code in (403, 404, 429):
                return []
            response.raise_for_status()
            return parse_surugaya_search_html(response.text)
        except httpx.HTTPError:
            return []

    def fetch_listings(self, item: CatalogItem) -> list[PriceListing]:
        """Query Surugaya by override or JAN first, falling back to normalized title."""
        override = item.search_query_override.strip()
        if override:
            self._polite_sleep()
            return self._search_word(override)

        jan = clean_jan_code(item.jan_code)
        if jan:
            self._polite_sleep()
            by_jan = self._search_word(jan)
            if by_jan:
                return by_jan

        fallback = normalize_title(item.title)
        if not fallback:
            return []
        self._polite_sleep()
        return self._search_word(fallback)
