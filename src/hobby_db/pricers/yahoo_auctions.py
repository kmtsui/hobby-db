"""Yahoo! Auctions closed-search pricer for realized new and used hammer prices."""

from __future__ import annotations

import json
import random
import time

import httpx
from bs4 import BeautifulSoup

from hobby_db.models import CatalogItem
from hobby_db.normalize import clean_jan_code, normalize_title, parse_jpy_int
from hobby_db.pricers.base import BasePricer, PriceListing

YAHOO_AUCTIONS_CLOSED_URL = "https://auctions.yahoo.co.jp/closedsearch/closedsearch"
YAHOO_AUCTIONS_ITEM_URL = "https://page.auctions.yahoo.co.jp/jp/auction/"

YAHOO_AUCTIONS_SELECTORS: dict[str, str] = {
    "next_data": "script#__NEXT_DATA__",
    "item_card": "li.Product",
    "title": ".Product__titleLink",
    "price": ".Product__priceValue",
}


_FALLBACK_NOTICE_MARKERS: tuple[str, ...] = (
    "に一致する商品はありません",
    "キーワードの一部を利用した結果を表示しています",
)


def parse_yahoo_auctions_html(
    html: str,
    selectors: dict[str, str] | None = None,
    condition: str = "used",
) -> list[PriceListing]:
    """Parse Yahoo! Auctions closed-search HTML (``__NEXT_DATA__`` or DOM fallback)."""
    sel = selectors or YAHOO_AUCTIONS_SELECTORS
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    if any(marker in page_text for marker in _FALLBACK_NOTICE_MARKERS):
        return []
    listings: list[PriceListing] = []

    next_data = soup.select_one(sel["next_data"])
    if next_data is not None and next_data.string:
        try:
            payload = json.loads(next_data.string)
            items = (
                payload.get("props", {})
                .get("pageProps", {})
                .get("initialState", {})
                .get("search", {})
                .get("items", {})
                .get("listing", {})
                .get("items", [])
            )
            if isinstance(items, list) and items:
                for entry in items:
                    if not isinstance(entry, dict) or entry.get("isNoPrice"):
                        continue
                    title = str(entry.get("title", "")).strip()
                    price_jpy = parse_jpy_int(entry.get("price"))
                    if not title or price_jpy is None or price_jpy <= 0:
                        continue
                    auction_id = str(entry.get("auctionId", "")).strip()
                    url = f"{YAHOO_AUCTIONS_ITEM_URL}{auction_id}" if auction_id else ""
                    listings.append(
                        PriceListing(
                            title=title,
                            price_jpy=price_jpy,
                            source="yahoo_auctions",
                            condition=condition,
                            url=url,
                        )
                    )
                return listings
        except json.JSONDecodeError:
            pass

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
        href = str(title_node.get("href") or "").strip()
        listings.append(
            PriceListing(
                title=title,
                price_jpy=price_jpy,
                source="yahoo_auctions",
                condition=condition,
                url=href,
            )
        )

    return listings


class YahooAuctionsPricer(BasePricer):
    """Fetch realized new (`istatus=1`) or used (`istatus=2,3,4,5`) sold prices."""

    source_name = "yahoo_auctions"

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

    def _search_keyword(self, query: str) -> list[PriceListing]:
        istatus = "1" if self.condition == "new" else "2,3,4,5"
        try:
            response = self._client.get(
                YAHOO_AUCTIONS_CLOSED_URL,
                params={"p": query, "istatus": istatus},
            )
            if response.status_code in (403, 404, 429):
                return []
            response.raise_for_status()
            return parse_yahoo_auctions_html(response.text, condition=self.condition)
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
