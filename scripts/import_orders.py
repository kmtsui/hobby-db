"""CLI to import a Premium Bandai CSV export into Google Sheets idempotently."""

from __future__ import annotations

import argparse
from pathlib import Path

from hobby_db.config import load_settings
from hobby_db.ingestors.pbandai_csv import ingest_pbandai_csv
from hobby_db.sheets.client import HobbySheetsClient


def import_orders_file(
    csv_path: Path,
    expand_quantity: bool = True,
    dry_run: bool = False,
    env_file: Path | None = None,
) -> dict[str, dict[str, int]]:
    """Parse a Premium Bandai CSV and idempotently sync Catalog + Inventory tabs."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    catalog_items, inventory_entries = ingest_pbandai_csv(
        csv_path,
        expand_quantity=expand_quantity,
    )

    if dry_run:
        return {
            "catalog": {"parsed": len(catalog_items), "added": 0, "updated": 0, "skipped": 0},
            "inventory": {"parsed": len(inventory_entries), "added": 0, "skipped": 0},
        }

    settings = load_settings(env_file)
    client = HobbySheetsClient.from_settings(settings)
    catalog_stats = client.upsert_catalog_items(catalog_items)
    inventory_stats = client.sync_inventory_entries(inventory_entries)
    return {
        "catalog": catalog_stats,
        "inventory": inventory_stats,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a Premium Bandai UTF-8 CSV export into Google Sheets."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        default=Path("data/pbandai_orders.csv"),
        help="Path to the Premium Bandai CSV file (default: data/pbandai_orders.csv).",
    )
    parser.add_argument(
        "--no-expand-quantity",
        action="store_true",
        help="Keep quantity > 1 as a single Inventory row instead of expanding _u1.._uN rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate the CSV without writing to Google Sheets.",
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

    stats = import_orders_file(
        csv_path=args.csv_path,
        expand_quantity=not args.no_expand_quantity,
        dry_run=args.dry_run,
        env_file=args.env_file,
    )
    mode = "DRY RUN" if args.dry_run else "SYNCED"
    print(f"[{mode}] Catalog: {stats['catalog']}")
    print(f"[{mode}] Inventory: {stats['inventory']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
