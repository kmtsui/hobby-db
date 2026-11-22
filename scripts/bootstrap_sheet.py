"""CLI to create or configure a Google Sheet with AppSheet-ready formatting and seed rows."""

from __future__ import annotations

import argparse
from pathlib import Path

from hobby_db.config import load_settings
from hobby_db.sheets.bootstrap import bootstrap_spreadsheet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or initialize the 3-tab Hobby Collection Google Sheet."
    )
    parser.add_argument(
        "--sheet-id",
        default=None,
        help="Existing Google Sheet ID to configure (overrides GOOGLE_SHEET_ID).",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Title when creating a new Google Sheet (overrides GOOGLE_SHEET_TITLE).",
    )
    parser.add_argument(
        "--share-with",
        default=None,
        help="Optional email address to grant editor access to the created Sheet.",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Skip inserting the flagged SEED_* sample row in each tab.",
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

    result = bootstrap_spreadsheet(
        settings=settings,
        sheet_id=args.sheet_id,
        title=args.title,
        share_with=args.share_with,
        include_seed=not args.no_seed,
    )

    print(f"Google Sheet ready: {result['url']}")
    print(f"Add to your .env:\nGOOGLE_SHEET_ID={result['sheet_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
