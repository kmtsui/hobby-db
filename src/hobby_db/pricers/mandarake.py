"""Mandarake market price fetcher for unopened (new) and opened (used) items."""

from __future__ import annotations

import contextlib
import random
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from hobby_db.models import CatalogItem
from hobby_db.normalize import clean_jan_code, normalize_title, parse_jpy_int
from hobby_db.pricers.base import BasePricer, PriceListing

MANDARAKE_BASE_URL = "https://order.mandarake.co.jp"
MANDARAKE_ENTRY_URL = f"{MANDARAKE_BASE_URL}/order/?lang=en"
MANDARAKE_SEARCH_URL = f"{MANDARAKE_BASE_URL}/order/listPage/list"

MANDARAKE_SELECTORS: dict[str, str] = {
    "item_card": "div.block[data-itemidx], .thumlarge .block, .entry .block",
    "title": ".title a, .title",
    "price": ".price",
    "link": ".title a, .thum a",
}

_NEW_CONDITION_MARKERS: tuple[str, ...] = (
    "unopened",
    "sealed",
    "未開封",
    "未組立",
    "新品",
)


def _classify_mandarake_condition(card_text: str, title: str) -> str:
    lowered = f"{title} {card_text}".lower()
    if any(marker in lowered for marker in _NEW_CONDITION_MARKERS):
        return "new"
    return "used"


def parse_mandarake_search_html(
    html: str,
    base_url: str = MANDARAKE_BASE_URL,
    selectors: dict[str, str] | None = None,
    condition: str | None = "used",
) -> list[PriceListing]:
    """Parse Mandarake search HTML into PriceListing items.

    When ``condition`` is ``"new"`` or ``"used"``, only matching listings are returned.
    Pass ``condition=None`` to keep both conditions tagged on each listing.
    """
    sel = selectors or MANDARAKE_SELECTORS
    soup = BeautifulSoup(html, "html.parser")
    listings: list[PriceListing] = []

    for card in soup.select(sel["item_card"]):
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

        detected_condition = _classify_mandarake_condition(
            card.get_text(" ", strip=True),
            title,
        )
        if condition is not None and detected_condition != condition:
            continue

        link_node = card.select_one(sel["link"])
        href = (link_node.get("href") or "").strip() if link_node is not None else ""
        full_url = urljoin(base_url, href) if href else ""

        listings.append(
            PriceListing(
                title=title,
                price_jpy=price_jpy,
                source="mandarake",
                condition=detected_condition,
                url=full_url,
            )
        )

    return listings


class MandarakePricer(BasePricer):
    """Fetch new or used prices from Mandarake with polite delays."""

    source_name = "mandarake"

    def __init__(
        self,
        condition: str = "used",
        client: httpx.Client | None = None,
        min_delay: float = 1.5,
        max_delay: float = 2.5,
        seed_session: bool = True,
    ) -> None:
        if condition not in ("new", "used"):
            raise ValueError(f"Unsupported condition: {condition}")
        self.condition = condition
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._seeded = not seed_session
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

    def _ensure_session(self) -> None:
        if self._seeded:
            return
        with contextlib.suppress(httpx.HTTPError):
            self._client.get(MANDARAKE_ENTRY_URL)
        self._seeded = True

    def _search_all_conditions(self, query: str) -> list[PriceListing]:
        self._ensure_session()
        params = {"keyword": query, "soldOut": "1", "lang": "en"}
        try:
            response = self._client.get(MANDARAKE_SEARCH_URL, params=params)
            if "www.mandarake.co.jp" in str(response.url):
                self._seeded = False
                self._ensure_session()
                response = self._client.get(MANDARAKE_SEARCH_URL, params=params)
            if response.status_code in (403, 404, 429):
                return []
            response.raise_for_status()
            return parse_mandarake_search_html(response.text, condition=None)
        except httpx.HTTPError:
            return []

    def fetch_all_listings(self, item: CatalogItem) -> list[PriceListing]:
        """Fetch all Mandarake listings for an item (both new and used tagged)."""
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
        """Return ``(new_listings, used_listings)`` using a single search request."""
        all_listings = self.fetch_all_listings(item)
        new_items = [listing for listing in all_listings if listing.condition == "new"]
        used_items = [listing for listing in all_listings if listing.condition == "used"]
        return new_items, used_items

    def fetch_listings(self, item: CatalogItem) -> list[PriceListing]:
        all_listings = self.fetch_all_listings(item)
        return [listing for listing in all_listings if listing.condition == self.condition]
