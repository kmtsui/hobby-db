"""Playwright crawler and HTML parser for Premium Bandai order history."""

from __future__ import annotations

import csv
import random
import re
import time
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from hobby_db.config import PBANDAI_CSV_COLUMNS
from hobby_db.models import PBandaiOrderRow
from hobby_db.normalize import parse_delivery_month, parse_jp_date, parse_jpy_int

PBANDAI_MYORDER_URL = "https://p-bandai.jp/myorder/"

# Isolate all DOM/CSS selectors here so markup changes on p-bandai.jp can be updated in seconds.
# Use scripts/crawl_pbandai.py --dump-html to inspect live order history HTML if selectors break.
SELECTORS: dict[str, str] = {
    "login_indicator": "form[action*='login'], input[name='loginId'], .p-login, #loginForm",
    "order_card": ".p-order-history__item, .order-history-box, .myorder-item, article[data-order-id]",
    "order_id": ".p-order-history__order-number, .order-number, [data-order-number]",
    "order_date": ".p-order-history__order-date, .order-date, [data-order-date]",
    "order_status": ".p-order-history__status, .order-status, [data-order-status]",
    "shipping_jpy": ".p-order-history__shipping, .order-shipping, [data-shipping-fee]",
    "item_row": ".p-order-history__product, .order-product-row, .order-item",
    "item_title": ".p-order-history__product-name, .product-name, .item-title",
    "unit_price_jpy": ".p-order-history__price, .product-price, .unit-price",
    "quantity": ".p-order-history__qty, .product-qty, .item-quantity",
    "delivery_month": ".p-order-history__delivery, .delivery-month, [data-delivery-month]",
    "image": "img.p-order-history__thumb, img.product-thumb, img",
    "next_page": "a[rel='next'], .pagination a.next, .p-pagination__next a, a.pager-next",
}

_PREFIX_STRIP_RE = re.compile(
    r"^(?:注文番号|ご注文番号|注文日|ご注文日|注文状況|ステータス|送料|配送手数料|数量|個数|お届け月|発送予定)\s*[:：]\s*"
)


def _clean_text(node: Tag | None) -> str:
    if node is None:
        return ""
    text = node.get_text(" ", strip=True)
    return _PREFIX_STRIP_RE.sub("", text).strip()


def _select_text(parent: Tag, selector: str) -> str:
    return _clean_text(parent.select_one(selector))


def _resolve_image_url(container: Tag, selector: str, base_url: str) -> str:
    img = container.select_one(selector)
    if img is None:
        return ""
    src = (img.get("src") or img.get("data-src") or "").strip()
    if not src or src.startswith("data:"):
        return ""
    return urljoin(base_url, src)


def is_login_required(
    url: str,
    html: str,
    selectors: dict[str, str] | None = None,
) -> bool:
    """Detect whether Premium Bandai redirected to login/auth instead of order history."""
    lowered_url = (url or "").lower()
    if any(token in lowered_url for token in ("/login", "auth.", "id.bandainamco", "signin")):
        return True

    sel = selectors or SELECTORS
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(sel["order_card"]) is not None:
        return False
    return soup.select_one(sel["login_indicator"]) is not None


def has_next_page(html: str, selectors: dict[str, str] | None = None) -> bool:
    """Return True if an enabled next-page link exists in the order history HTML."""
    sel = selectors or SELECTORS
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one(sel["next_page"])
    if link is None:
        return False
    classes = " ".join(link.get("class") or []).lower()
    if "disabled" in classes or link.get("aria-disabled") == "true":
        return False
    href = (link.get("href") or "").strip()
    return bool(href and href != "#")


def parse_orders_html(
    html: str,
    base_url: str = "https://p-bandai.jp",
    selectors: dict[str, str] | None = None,
) -> list[PBandaiOrderRow]:
    """Parse Premium Bandai order history HTML into structured order rows."""
    sel = selectors or SELECTORS
    soup = BeautifulSoup(html, "html.parser")
    rows: list[PBandaiOrderRow] = []

    for card in soup.select(sel["order_card"]):
        order_id = (card.get("data-order-id") or _select_text(card, sel["order_id"])).strip()
        if not order_id:
            continue

        raw_date = _select_text(card, sel["order_date"])
        order_date = parse_jp_date(raw_date) or raw_date
        order_status = _select_text(card, sel["order_status"])
        order_shipping = parse_jpy_int(_select_text(card, sel["shipping_jpy"])) or 0

        item_nodes = card.select(sel["item_row"])
        if not item_nodes and card.select_one(sel["item_title"]) is not None:
            item_nodes = [card]

        for idx, item_node in enumerate(item_nodes):
            title = _select_text(item_node, sel["item_title"])
            if not title:
                continue

            unit_price = parse_jpy_int(_select_text(item_node, sel["unit_price_jpy"]))
            qty_parsed = parse_jpy_int(_select_text(item_node, sel["quantity"]))
            quantity = max(1, qty_parsed) if qty_parsed is not None else 1

            raw_delivery = _select_text(item_node, sel["delivery_month"]) or _select_text(
                card, sel["delivery_month"]
            )
            delivery_month = parse_delivery_month(raw_delivery) or raw_delivery
            image_url = _resolve_image_url(item_node, sel["image"], base_url)

            # Assign order-level shipping to the first item row only so multi-item
            # orders do not double-count shipping fees.
            shipping_for_line = order_shipping if idx == 0 else 0

            rows.append(
                PBandaiOrderRow(
                    order_id=order_id,
                    order_date=order_date,
                    item_title=title,
                    unit_price_jpy=unit_price,
                    quantity=quantity,
                    shipping_jpy=shipping_for_line,
                    order_status=order_status,
                    delivery_month=delivery_month,
                    image_url=image_url,
                )
            )

    return rows


def export_orders_csv(rows: Sequence[PBandaiOrderRow], output_path: Path) -> Path:
    """Write crawled order rows to a UTF-8 with BOM (``utf-8-sig``) CSV file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PBANDAI_CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_dict())
    return output_path


def login_and_save_state(auth_state_path: Path) -> Path:
    """Launch headed Chromium for manual Premium Bandai login and save storage_state."""
    from playwright.sync_api import sync_playwright

    auth_state_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(locale="ja-JP")
        page = context.new_page()
        page.goto(PBANDAI_MYORDER_URL, wait_until="domcontentloaded")
        print(
            "\nComplete Premium Bandai login (including CAPTCHA/2FA) in the opened browser.\n"
            "Once your order history page is visible, press Enter here to save session state..."
        )
        input()
        context.storage_state(path=str(auth_state_path))
        browser.close()
    return auth_state_path


def crawl_orders(
    auth_state_path: Path,
    headless: bool = True,
    max_pages: int | None = None,
    dump_html_dir: Path | None = None,
    min_delay: float = 1.5,
    max_delay: float = 3.0,
) -> list[PBandaiOrderRow]:
    """Crawl all pages of Premium Bandai order history reusing saved Playwright auth state."""
    from playwright.sync_api import sync_playwright

    if not auth_state_path.exists():
        login_and_save_state(auth_state_path)

    if dump_html_dir is not None:
        dump_html_dir.mkdir(parents=True, exist_ok=True)

    collected_rows: list[PBandaiOrderRow] = []
    seen_order_ids: set[str] = set()

    with sync_playwright() as playwright:
        for attempt in range(2):
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(
                storage_state=str(auth_state_path),
                locale="ja-JP",
            )
            page = context.new_page()
            page.goto(PBANDAI_MYORDER_URL, wait_until="networkidle")
            html = page.content()

            if is_login_required(page.url, html):
                browser.close()
                if attempt == 0:
                    print("Saved Premium Bandai session expired; launching headed login...")
                    login_and_save_state(auth_state_path)
                    continue
                raise RuntimeError("Premium Bandai still requires login after re-authentication.")

            page_num = 1
            while True:
                if dump_html_dir is not None:
                    dump_path = dump_html_dir / f"page_{page_num:03d}.html"
                    dump_path.write_text(html, encoding="utf-8")

                page_rows = parse_orders_html(html)
                page_order_ids: set[str] = set()
                for row in page_rows:
                    if row.order_id in seen_order_ids:
                        continue
                    collected_rows.append(row)
                    page_order_ids.add(row.order_id)
                seen_order_ids.update(page_order_ids)

                if max_pages is not None and page_num >= max_pages:
                    break
                if not has_next_page(html):
                    break

                time.sleep(random.uniform(min_delay, max_delay))
                next_locator = page.locator(SELECTORS["next_page"]).first
                if next_locator.count() == 0:
                    break
                next_locator.click()
                page.wait_for_load_state("networkidle")
                html = page.content()
                page_num += 1

            context.storage_state(path=str(auth_state_path))
            browser.close()
            break

    return collected_rows
