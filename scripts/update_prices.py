"""CLI to refresh stale Catalog market prices across zero-token sources and log PriceHistory."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from hobby_db.config import Settings, load_settings
from hobby_db.models import CatalogItem, CatalogPriceUpdate, PriceHistoryRecord
from hobby_db.normalize import make_log_id
from hobby_db.pricers.base import (
    BasePricer,
    PriceListing,
    PriceQuote,
    aggregate_listings,
    is_item_stale,
    is_junk_listing,
)
from hobby_db.pricers.jungle import JunglePricer
from hobby_db.pricers.kakaku import KakakuPricer
from hobby_db.pricers.mandarake import MandarakePricer
from hobby_db.pricers.rakuten import RakutenPricer
from hobby_db.pricers.surugaya import SurugayaPricer
from hobby_db.pricers.yahoo_auctions import YahooAuctionsPricer
from hobby_db.pricers.yahoo_shopping import YahooShoppingPricer
from hobby_db.sheets.client import HobbySheetsClient

DEFAULT_SOURCES = "mandarake,surugaya,yahoo_shopping,kakaku,yahoo_auctions,jungle"


def build_domain_pricers(
    requested_sources: Sequence[str],
    conditions: Sequence[str],
    settings: Settings,
) -> dict[str, dict[str, BasePricer]]:
    """Instantiate per-domain pricers for the requested sources and conditions."""
    domain_pricers: dict[str, dict[str, BasePricer]] = {}

    for source in requested_sources:
        if source == "rakuten":
            if not settings.rakuten_app_id:
                print("Skipping rakuten pricer: RAKUTEN_APP_ID is not set.")
                continue
            if "new" in conditions:
                domain_pricers["rakuten"] = {
                    "new": RakutenPricer(
                        app_id=settings.rakuten_app_id,
                        affiliate_id=settings.rakuten_affiliate_id,
                    )
                }
        elif source == "mandarake":
            domain_pricers["mandarake"] = {"both": MandarakePricer(condition="used")}
        elif source == "surugaya":
            domain_pricers["surugaya"] = {c: SurugayaPricer(condition=c) for c in conditions}
        elif source == "yahoo_shopping":
            domain_pricers["yahoo_shopping"] = {"both": YahooShoppingPricer(condition="new")}
        elif source == "kakaku":
            domain_pricers["kakaku"] = {c: KakakuPricer(condition=c) for c in conditions}
        elif source == "yahoo_auctions":
            domain_pricers["yahoo_auctions"] = {
                c: YahooAuctionsPricer(condition=c) for c in conditions
            }
        elif source == "jungle":
            domain_pricers["jungle"] = {c: JunglePricer(condition=c) for c in conditions}
        else:
            print(f"Unknown source ignored: {source}")

    return domain_pricers


def _fetch_domain_listings(
    cond_pricers: dict[str, BasePricer],
    item: CatalogItem,
    conditions: Sequence[str],
) -> dict[str, list[PriceListing]]:
    """Fetch new/used listings sequentially for a single domain."""
    result: dict[str, list[PriceListing]] = {"new": [], "used": []}
    if "both" in cond_pricers:
        both_pricer = cond_pricers["both"]
        if isinstance(both_pricer, (MandarakePricer, YahooShoppingPricer)):
            new_hits, used_hits = both_pricer.fetch_both_conditions(item)
            if "new" in conditions:
                result["new"] = new_hits
            if "used" in conditions:
                result["used"] = used_hits
            return result

    for cond in conditions:
        pricer = cond_pricers.get(cond)
        if pricer is not None:
            result[cond] = pricer.fetch_listings(item)
    return result


def fetch_item_quotes(
    item: CatalogItem,
    domain_pricers: dict[str, dict[str, BasePricer]],
    conditions: Sequence[str],
) -> tuple[PriceQuote | None, PriceQuote | None]:
    """Query all configured domains concurrently and compute pooled new/used median quotes."""
    pooled: dict[str, list[PriceListing]] = {"new": [], "used": []}

    with ThreadPoolExecutor(max_workers=max(1, len(domain_pricers))) as executor:
        futures = [
            executor.submit(_fetch_domain_listings, cond_pricers, item, conditions)
            for cond_pricers in domain_pricers.values()
        ]
        for future in as_completed(futures):
            domain_result = future.result()
            pooled["new"].extend(domain_result.get("new", []))
            pooled["used"].extend(domain_result.get("used", []))

    quotes: dict[str, PriceQuote | None] = {"new": None, "used": None}
    for cond in conditions:
        listings = pooled.get(cond, [])
        contributors = sorted(
            {
                listing.source
                for listing in listings
                if listing.price_jpy > 0 and not is_junk_listing(listing.title)
            }
        )
        if not contributors:
            continue
        quote = aggregate_listings(
            listings=listings,
            source=",".join(contributors),
            condition=cond,
        )
        if quote.median_price_jpy is not None:
            quotes[cond] = quote

    return quotes["new"], quotes["used"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh stale Catalog market prices and log snapshots to PriceHistory."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of stale items to refresh in this run.",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=None,
        help="Refresh items older than N days (overrides STALE_DAYS).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh all non-SEED items regardless of price_updated_at.",
    )
    parser.add_argument(
        "--sources",
        default=DEFAULT_SOURCES,
        help=f"Comma-separated price sources to query (default: {DEFAULT_SOURCES}).",
    )
    parser.add_argument(
        "--conditions",
        default="new,used",
        help="Comma-separated conditions to fetch: new, used, or new,used (default: new,used).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch market quotes without writing updates to Google Sheets.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional path to .env file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings(args.env_file)
    stale_days = args.stale_days if args.stale_days is not None else settings.stale_days

    requested_sources = list(
        dict.fromkeys(s.strip().lower() for s in args.sources.split(",") if s.strip())
    )
    if (
        args.sources == DEFAULT_SOURCES
        and settings.rakuten_app_id
        and "rakuten" not in requested_sources
    ):
        requested_sources.append("rakuten")

    conditions = list(
        dict.fromkeys(
            c.strip().lower()
            for c in args.conditions.split(",")
            if c.strip().lower() in ("new", "used")
        )
    )
    if not conditions:
        print("No valid conditions specified (expected new, used). Exiting.")
        return 0

    domain_pricers = build_domain_pricers(requested_sources, conditions, settings)
    if not domain_pricers:
        print("No active price sources configured. Exiting.")
        return 0

    client = HobbySheetsClient.from_settings(settings)
    now_dt = datetime.now(UTC)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    try:
        catalog_items = [
            item
            for item in client.get_catalog_items()
            if item.item_id and not item.item_id.startswith("SEED_")
        ]
        targets = (
            catalog_items
            if args.force
            else [
                item
                for item in catalog_items
                if is_item_stale(item, stale_days=stale_days, now=now_dt)
            ]
        )
        if args.limit is not None and args.limit >= 0:
            targets = targets[: args.limit]

        if not targets:
            print("All Catalog items are up to date.")
            return 0

        price_updates: list[CatalogPriceUpdate] = []
        history_records: list[PriceHistoryRecord] = []

        for item in targets:
            new_quote, used_quote = fetch_item_quotes(item, domain_pricers, conditions)
            if new_quote is None and used_quote is None:
                print(f"No quotes found for {item.item_id} ({item.title})")
                continue

            price_new = new_quote.median_price_jpy if new_quote else None
            price_used = used_quote.median_price_jpy if used_quote else None
            all_sources: set[str] = set()
            sample_total = 0
            if new_quote:
                all_sources.update(s for s in new_quote.source.split(",") if s)
                sample_total += new_quote.sample_size
            if used_quote:
                all_sources.update(s for s in used_quote.source.split(",") if s)
                sample_total += used_quote.sample_size

            source_label = ",".join(sorted(all_sources))
            price_updates.append(
                CatalogPriceUpdate(
                    item_id=item.item_id,
                    market_price_new=price_new,
                    market_price_used=price_used,
                    price_source=source_label,
                    price_updated_at=now_str,
                )
            )
            history_records.append(
                PriceHistoryRecord(
                    log_id=make_log_id(item.item_id, now_dt),
                    item_id=item.item_id,
                    recorded_at=now_str,
                    price_new=price_new,
                    price_used=price_used,
                    source=source_label,
                    sample_size=sample_total,
                )
            )
            new_n = new_quote.sample_size if new_quote else 0
            used_n = used_quote.sample_size if used_quote else 0
            print(
                f"Quoted {item.item_id}: new={price_new} (n={new_n}) "
                f"used={price_used} (n={used_n}) sources={source_label}"
            )

        if args.dry_run:
            print(f"[DRY RUN] Would update {len(price_updates)} Catalog row(s).")
            return 0

        if price_updates:
            cat_stats = client.update_catalog_prices(price_updates)
            hist_stats = client.append_price_history(history_records)
            print(f"Catalog prices updated: {cat_stats}")
            print(f"PriceHistory appended: {hist_stats}")
        else:
            print("No market quotes obtained; sheet left unchanged.")
        return 0
    finally:
        for cond_pricers in domain_pricers.values():
            for pricer in cond_pricers.values():
                close_fn = getattr(pricer, "close", None)
                if callable(close_fn):
                    close_fn()


if __name__ == "__main__":
    raise SystemExit(main())
