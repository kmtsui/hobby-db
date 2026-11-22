"""CLI to refresh stale Catalog market prices and append snapshots to PriceHistory."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from hobby_db.config import load_settings
from hobby_db.models import CatalogPriceUpdate, PriceHistoryRecord
from hobby_db.normalize import make_log_id
from hobby_db.pricers.base import BasePricer, is_item_stale
from hobby_db.pricers.rakuten import RakutenPricer
from hobby_db.pricers.surugaya import SurugayaPricer
from hobby_db.sheets.client import HobbySheetsClient


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
        default="rakuten,surugaya",
        help="Comma-separated price sources to query (default: rakuten,surugaya).",
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

    requested_sources = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    pricers: list[BasePricer] = []
    for source in requested_sources:
        if source == "rakuten":
            if not settings.rakuten_app_id:
                print("Skipping rakuten pricer: RAKUTEN_APP_ID is not set.")
                continue
            pricers.append(
                RakutenPricer(
                    app_id=settings.rakuten_app_id,
                    affiliate_id=settings.rakuten_affiliate_id,
                )
            )
        elif source == "surugaya":
            pricers.append(SurugayaPricer())
        else:
            print(f"Unknown source ignored: {source}")

    if not pricers:
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
            price_new: int | None = None
            price_used: int | None = None
            used_sources: list[str] = []
            sample_total = 0

            for pricer in pricers:
                quote = pricer.get_quote(item)
                if quote.median_price_jpy is None:
                    continue
                used_sources.append(quote.source)
                sample_total += quote.sample_size
                if quote.condition == "new":
                    price_new = quote.median_price_jpy
                elif quote.condition == "used":
                    price_used = quote.median_price_jpy

            if not used_sources:
                print(f"No quotes found for {item.item_id} ({item.title})")
                continue

            source_label = ",".join(used_sources)
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
            print(
                f"Quoted {item.item_id}: new={price_new} used={price_used} "
                f"source={source_label} samples={sample_total}"
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
        for pricer in pricers:
            close_fn = getattr(pricer, "close", None)
            if callable(close_fn):
                close_fn()


if __name__ == "__main__":
    raise SystemExit(main())
