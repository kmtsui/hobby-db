"""Yahoo! Shopping HTML pricer for new and second-hand hobby prices."""

from __future__ import annotations

import random
import re
import time

import httpx
from bs4 import BeautifulSoup

from hobby_db.models import CatalogItem
from hobby_db.normalize import clean_jan_code, normalize_title, parse_jpy_int
from hobby_db.pricers.base import BasePricer, PriceListing

YAHOO_SHOPPING_SEARCH_URL = "https://shopping.yahoo.co.jp/search"

YAHOO_SHOPPING_SELECTORS: dict[str, str] = {
    "item_card": (
        "div[class*='SearchResult_SearchResultItem__contents'], li[class*='LoopList__item']"
    ),
    "title_link": "a[data-beacon*='prc:'], a[href*='store.shopping.yahoo.co.jp']",
    "price": "[class*='price'], [class*='Price']",
}

_BEACON_PRICE_RE = re.compile(r"(?:^|;)prc:(\d+)")
_BEACON_URL_RE = re.compile(r"(?:^|;)targurl:([^;]+)")


def parse_yahoo_shopping_html(
    html: str,
    selectors: dict[str, str] | None = None,
    condition: str | None = "new",
) -> list[PriceListing]:
    """Parse Yahoo! Shopping search results HTML into PriceListing items.

    When ``condition`` is ``"new"`` or ``"used"``, filters by detecting ``中古`` in the
    listing title/card text. Pass ``condition=None`` to return both conditions tagged.
    """
    sel = selectors or YAHOO_SHOPPING_SELECTORS
    soup = BeautifulSoup(html, "html.parser")
    listings: list[PriceListing] = []
    seen_urls: set[str] = set()

    cards = soup.select(sel["item_card"])
    candidates = cards if cards else soup.select(sel["title_link"])

    for node in candidates:
        if node.name == "a":
            anchor = node
            card = node.parent
        else:
            anchors = [a for a in node.select(sel["title_link"]) if a.get_text(" ", strip=True)]
            if not anchors:
                continue
            anchor = anchors[0]
            card = node

        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            continue

        card_text = card.get_text(" ", strip=True) if card is not None else title
        detected_condition = "used" if ("中古" in title or "中古" in card_text) else "new"
        if condition is not None and detected_condition != condition:
            continue

        beacon = str(anchor.get("data-beacon") or "")
        beacon_price_match = _BEACON_PRICE_RE.search(beacon)
        if beacon_price_match:
            price_jpy: int | None = int(beacon_price_match.group(1))
        else:
            price_node = card.select_one(sel["price"]) if card is not None else None
            price_text = (
                price_node.get_text(" ", strip=True) if price_node is not None else card_text
            )
            price_jpy = parse_jpy_int(price_text)

        if price_jpy is None or price_jpy <= 0:
            continue

        href = (anchor.get("href") or "").strip()
        if not href:
            url_match = _BEACON_URL_RE.search(beacon)
            if url_match:
                href = f"https://{url_match.group(1).lstrip('/')}"

        dedup_key = href or f"{title}:{price_jpy}"
        if dedup_key in seen_urls:
            continue
        seen_urls.add(dedup_key)

        listings.append(
            PriceListing(
                title=title,
                price_jpy=price_jpy,
                source="yahoo_shopping",
                condition=detected_condition,
                url=href,
            )
        )

    return listings


class YahooShoppingPricer(BasePricer):
    """Fetch new and used prices from Yahoo! Shopping in a single search query."""

    source_name = "yahoo_shopping"

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

    def _search_all_conditions(self, query: str) -> list[PriceListing]:
        try:
            response = self._client.get(
                YAHOO_SHOPPING_SEARCH_URL,
                params={"p": query},
            )
            if response.status_code in (403, 404, 429):
                return []
            response.raise_for_status()
            return parse_yahoo_shopping_html(response.text, condition=None)
        except httpx.HTTPError:
            return []

    def fetch_all_listings(self, item: CatalogItem) -> list[PriceListing]:
        """Fetch all Yahoo! Shopping listings for an item (both new and used tagged)."""
        override = item.search_query_override.strip()
        if override:
            self._polite_sleep()
            return self._search_all_conditions(override)

        jan = clean_jan_code(item.jan_code)
        if jan:
            self._polite_sleep()
            by_jan = self._search_all_conditions(jan)
            if by_jan:
                return by_jan

        fallback = normalize_title(item.title)
        if not fallback:
            return []
        self._polite_sleep()
        return self._search_all_conditions(fallback)

    def fetch_both_conditions(
        self,
        item: CatalogItem,
    ) -> tuple[list[PriceListing], list[PriceListing]]:
        """Return ``(new_listings, used_listings)`` from a single Yahoo! Shopping request."""
        all_listings = self.fetch_all_listings(item)
        new_items = [listing for listing in all_listings if listing.condition == "new"]
        used_items = [listing for listing in all_listings if listing.condition == "used"]
        return new_items, used_items

    def fetch_listings(self, item: CatalogItem) -> list[PriceListing]:
        all_listings = self.fetch_all_listings(item)
        return [listing for listing in all_listings if listing.condition == self.condition]
