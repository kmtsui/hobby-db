"""Abstract pricer interface, junk/outlier filtering, median aggregation, and staleness checks."""

from __future__ import annotations

import statistics
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from hobby_db.models import CatalogItem

JUNK_KEYWORDS: tuple[str, ...] = (
    "ジャンク",
    "箱のみ",
    "説明書のみ",
    "まとめ売り",
    "パーツ",
    "空箱",
    "欠品",
    "破損",
    "部品取り",
    "箱だけ",
)

_NORMALIZED_JUNK_KEYWORDS: tuple[str, ...] = tuple(
    unicodedata.normalize("NFKC", kw).lower() for kw in JUNK_KEYWORDS
)


class PriceListing(BaseModel):
    """Single marketplace listing before junk/outlier filtering."""

    title: str
    price_jpy: int
    source: str
    condition: str
    url: str = ""


class PriceQuote(BaseModel):
    """Aggregated median market price from filtered listings."""

    source: str
    condition: str
    median_price_jpy: int | None = None
    sample_size: int = 0
    filtered_junk_count: int = 0
    filtered_outlier_count: int = 0


def is_junk_listing(
    title: str,
    extra_keywords: Sequence[str] = (),
) -> bool:
    """Return True if a listing title contains junk, box-only, manual-only, or parts keywords."""
    if not title:
        return True
    normalized = unicodedata.normalize("NFKC", title).lower()
    if any(kw in normalized for kw in _NORMALIZED_JUNK_KEYWORDS):
        return True
    for raw_kw in extra_keywords:
        kw = unicodedata.normalize("NFKC", raw_kw).lower().strip()
        if kw and kw in normalized:
            return True
    return False


def filter_outliers_iqr(prices: Sequence[int]) -> list[int]:
    """Filter extreme price outliers using the 1.5*IQR fence."""
    valid = [p for p in prices if p > 0]
    if len(valid) < 4:
        return valid

    q1, median, q3 = statistics.quantiles(valid, n=4, method="inclusive")
    iqr = q3 - q1
    if iqr <= 0:
        low = median * 0.5
        high = median * 2.0
    else:
        low = q1 - 1.5 * iqr
        high = q3 + 1.5 * iqr

    kept = [p for p in valid if low <= p <= high]
    return kept if kept else valid


def aggregate_listings(
    listings: Sequence[PriceListing],
    source: str,
    condition: str,
    filter_outliers: bool = True,
    extra_junk_keywords: Sequence[str] = (),
) -> PriceQuote:
    """Filter junk listings and outliers, then compute median price and sample size."""
    non_junk: list[PriceListing] = []
    junk_count = 0

    for item in listings:
        if item.price_jpy <= 0 or is_junk_listing(item.title, extra_keywords=extra_junk_keywords):
            junk_count += 1
            continue
        non_junk.append(item)

    if not non_junk:
        return PriceQuote(
            source=source,
            condition=condition,
            median_price_jpy=None,
            sample_size=0,
            filtered_junk_count=junk_count,
            filtered_outlier_count=0,
        )

    raw_prices = [item.price_jpy for item in non_junk]
    final_prices = filter_outliers_iqr(raw_prices) if filter_outliers else raw_prices
    outlier_count = len(raw_prices) - len(final_prices)
    median_val = round(statistics.median(final_prices))

    return PriceQuote(
        source=source,
        condition=condition,
        median_price_jpy=median_val,
        sample_size=len(final_prices),
        filtered_junk_count=junk_count,
        filtered_outlier_count=outlier_count,
    )


def parse_timestamp(value: str) -> datetime | None:
    """Parse ``YYYY-MM-DD HH:MM:SS`` or ISO-8601 timestamp into UTC-aware datetime."""
    text = (value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(normalized, fmt)
            return dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def is_item_stale(
    item: CatalogItem,
    stale_days: int = 7,
    now: datetime | None = None,
) -> bool:
    """Return True if an item's market price is missing or older than ``stale_days``."""
    parsed = parse_timestamp(item.price_updated_at)
    if parsed is None:
        return True
    ref_now = now if now is not None else datetime.now(UTC)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=UTC)
    return (ref_now - parsed) >= timedelta(days=stale_days)


class BasePricer(ABC):
    """Abstract base class for marketplace price fetchers."""

    source_name: str
    condition: str

    def build_search_query(self, item: CatalogItem) -> str:
        """Select override search query or fallback to item title."""
        if item.search_query_override.strip():
            return item.search_query_override.strip()
        return item.title.strip()

    @abstractmethod
    def fetch_listings(self, item: CatalogItem) -> list[PriceListing]:
        """Fetch raw marketplace listings for a CatalogItem."""

    def get_quote(self, item: CatalogItem) -> PriceQuote:
        """Fetch listings, filter junk/outliers, and return aggregated PriceQuote."""
        listings = self.fetch_listings(item)
        return aggregate_listings(
            listings=listings,
            source=self.source_name,
            condition=self.condition,
        )
