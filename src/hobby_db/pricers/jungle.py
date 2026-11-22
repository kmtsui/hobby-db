"""Jungle (jungle-scs.co.jp) market price fetcher for unopened and opened hobby prices."""

from __future__ import annotations

import random
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from hobby_db.models import CatalogItem
from hobby_db.normalize import clean_jan_code, normalize_title, parse_jpy_int
from hobby_db.pricers.base import BasePricer, PriceListing

JUNGLE_BASE_URL = "https://jungle-scs.co.jp"
JUNGLE_SEARCH_URL = f"{JUNGLE_BASE_URL}/sale/products/list"

# Verified rank option values on Jungle: 0 = 未開封, 3 = A, 5 = B, 7 = アウトレット
JUNGLE_RANK_NEW = "0"
JUNGLE_RANK_USED_CANDIDATES: tuple[str, ...] = ("3", "5")

JUNGLE_SELECTORS: dict[str, str] = {
    "item_card": ".ec-shelfGrid__item",
    "title": ".ec-productItemRole__title",
    "price": ".ec-price__price",
    "link": ".ec-productItemRole__image a, a",
}

_SOLD_OUT_MARKERS: tuple[str, ...] = ("売切", "在庫なし", "sold out", "soldout")


def parse_jungle_search_html(
    html: str,
    base_url: str = JUNGLE_BASE_URL,
    selectors: dict[str, str] | None = None,
    condition: str = "used",
) -> list[PriceListing]:
    """Parse Jungle search results HTML into PriceListing items."""
    sel = selectors or JUNGLE_SELECTORS
    soup = BeautifulSoup(html, "html.parser")
    listings: list[PriceListing] = []

    for card in soup.select(sel["item_card"]):
        card_text = card.get_text(" ", strip=True).lower()
        if any(marker in card_text for marker in _SOLD_OUT_MARKERS):
            continue

        title_node = card.select_one(sel["title"])
        if title_node is None:
            continue
        title = " ".join(title_node.get_text(" ", strip=True).split())
        if not title:
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
                source="jungle",
                condition=condition,
                url=full_url,
            )
        )

    return listings


class JunglePricer(BasePricer):
    """Fetch unopened (``rank=0``) or opened (``rank=3/5``) prices from Jungle."""

    source_name = "jungle"

    def __init__(
        self,
        condition: str = "used",
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

    def _search_keyword_with_rank(self, query: str, rank: str) -> list[PriceListing]:
        try:
            response = self._client.get(
                JUNGLE_SEARCH_URL,
                params={"name": query, "rank": rank},
            )
            if response.status_code in (403, 404, 429):
                return []
            response.raise_for_status()
            return parse_jungle_search_html(response.text, condition=self.condition)
        except httpx.HTTPError:
            return []

    def _search_keyword(self, query: str) -> list[PriceListing]:
        ranks = (JUNGLE_RANK_NEW,) if self.condition == "new" else JUNGLE_RANK_USED_CANDIDATES
        for idx, rank in enumerate(ranks):
            if idx > 0:
                self._polite_sleep()
            hits = self._search_keyword_with_rank(query, rank)
            if hits:
                return hits
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
