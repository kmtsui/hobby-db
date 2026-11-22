"""CLI to authenticate, crawl Premium Bandai order history, export CSV, and optionally import."""

from __future__ import annotations

import argparse
from pathlib import Path

from hobby_db.config import load_settings
from hobby_db.crawlers.pbandai import (
    crawl_orders,
    export_orders_csv,
    login_and_save_state,
)

try:
    from scripts.import_orders import import_orders_file
except ModuleNotFoundError:
    from import_orders import import_orders_file  # type: ignore[import-not-found]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl p-bandai.jp/myorder/ to UTF-8 CSV and optionally import to Google Sheets."
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Launch headed browser to log in manually and save Playwright auth state.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/pbandai_orders.csv"),
        help="Destination CSV path (default: data/pbandai_orders.csv).",
    )
    parser.add_argument(
        "--auth-state",
        type=Path,
        default=None,
        help="Path to Playwright storage_state JSON (overrides PBANDAI_AUTH_STATE_PATH).",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show browser window during crawling instead of running headless.",
    )
    parser.add_argument(
        "--dump-html",
        nargs="?",
        const=Path("data/debug_html"),
        type=Path,
        default=None,
        help="Save raw HTML of each crawled order page (default dir: data/debug_html).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Stop after crawling at most N pagination pages.",
    )
    parser.add_argument(
        "--import",
        dest="auto_import",
        action="store_true",
        help="Automatically import the exported CSV into Google Sheets.",
    )
    parser.add_argument(
        "--no-expand-quantity",
        action="store_true",
        help="When --import is set, keep quantity > 1 as a single Inventory row.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="When --import is set, validate CSV import without writing to Google Sheets.",
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
    auth_state_path = args.auth_state or settings.pbandai_auth_state_path

    if args.login:
        saved_path = login_and_save_state(auth_state_path)
        print(f"Saved Premium Bandai auth state to: {saved_path}")
        return 0

    rows = crawl_orders(
        auth_state_path=auth_state_path,
        headless=not args.headed,
        max_pages=args.max_pages,
        dump_html_dir=args.dump_html,
    )
    csv_path = export_orders_csv(rows, args.out)
    print(f"Exported {len(rows)} order line item(s) to: {csv_path}")

    if args.auto_import:
        stats = import_orders_file(
            csv_path=csv_path,
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
