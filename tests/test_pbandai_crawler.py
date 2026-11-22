"""Tests for Premium Bandai HTML parsing, pagination/login detection, and BOM CSV export."""

from pathlib import Path

import pandas as pd

from hobby_db.crawlers.pbandai import (
    export_orders_csv,
    has_next_page,
    is_login_required,
    parse_orders_html,
)

MOCK_MYORDER_HTML = """
<!doctype html>
<html lang="ja">
<body>
  <div class="order-list">
    <article class="p-order-history__item" data-order-id="PB-2025-0001">
      <div class="p-order-history__order-number">注文番号：PB-2025-0001</div>
      <div class="p-order-history__order-date">ご注文日：2025年5月10日</div>
      <div class="p-order-history__status">注文状況：受注完了</div>
      <div class="p-order-history__shipping">送料：660円</div>

      <div class="p-order-history__product">
        <img class="p-order-history__thumb" src="/img/item_alpha.jpg" alt="alpha" />
        <div class="p-order-history__product-name">【2次：2025年10月発送】MG 1/100 ガンダムMk-V</div>
        <div class="p-order-history__price">8,800円</div>
        <div class="p-order-history__qty">数量：2</div>
        <div class="p-order-history__delivery">お届け月：2025年10月発送</div>
      </div>

      <div class="p-order-history__product">
        <img class="p-order-history__thumb" data-src="/img/item_beta.jpg" alt="beta" />
        <div class="p-order-history__product-name">【抽選販売】HG 1/144 デスティニーガンダムSpecII</div>
        <div class="p-order-history__price">２，６４０円</div>
        <div class="p-order-history__qty">数量：1</div>
        <div class="p-order-history__delivery">2025年11月発送</div>
      </div>
    </article>

    <article class="p-order-history__item">
      <div class="p-order-history__order-number">注文番号：PB-2025-0002</div>
      <div class="p-order-history__order-date">2025/04/01</div>
      <div class="p-order-history__status">発送済み</div>
      <div class="p-order-history__shipping">0円</div>
      <div class="p-order-history__product">
        <img class="p-order-history__thumb" src="https://cdn.p-bandai.jp/img/zeta.png" />
        <div class="p-order-history__product-name">METAL BUILD ゼータガンダム</div>
        <div class="p-order-history__price">44,000円</div>
        <div class="p-order-history__qty">1</div>
        <div class="p-order-history__delivery">2025年4月</div>
      </div>
    </article>
  </div>

  <nav class="pagination">
    <a rel="next" href="/myorder/?page=2">次へ</a>
  </nav>
</body>
</html>
"""

MOCK_LOGIN_HTML = """
<!doctype html>
<html>
<body>
  <form action="/login" method="post">
    <input name="loginId" type="text" />
  </form>
</body>
</html>
"""


def test_parse_orders_html_extracts_all_fields_and_shipping() -> None:
    rows = parse_orders_html(MOCK_MYORDER_HTML)
    assert len(rows) == 3

    first, second, third = rows
    assert first.order_id == "PB-2025-0001"
    assert first.order_date == "2025-05-10"
    assert first.item_title == "【2次：2025年10月発送】MG 1/100 ガンダムMk-V"
    assert first.unit_price_jpy == 8800
    assert first.quantity == 2
    assert first.shipping_jpy == 660
    assert first.order_status == "受注完了"
    assert first.delivery_month == "2025-10-01"
    assert first.image_url == "https://p-bandai.jp/img/item_alpha.jpg"

    # Second item in same order gets shipping=0 to avoid double counting
    assert second.order_id == "PB-2025-0001"
    assert second.unit_price_jpy == 2640
    assert second.quantity == 1
    assert second.shipping_jpy == 0
    assert second.image_url == "https://p-bandai.jp/img/item_beta.jpg"

    assert third.order_id == "PB-2025-0002"
    assert third.order_date == "2025-04-01"
    assert third.order_status == "発送済み"
    assert third.image_url == "https://cdn.p-bandai.jp/img/zeta.png"


def test_login_and_pagination_detection() -> None:
    assert is_login_required("https://p-bandai.jp/myorder/", MOCK_MYORDER_HTML) is False
    assert is_login_required("https://p-bandai.jp/login", MOCK_MYORDER_HTML) is True
    assert is_login_required("https://p-bandai.jp/myorder/", MOCK_LOGIN_HTML) is True

    assert has_next_page(MOCK_MYORDER_HTML) is True
    assert (
        has_next_page('<nav><a rel="next" class="disabled" href="/myorder/?page=3">次へ</a></nav>')
        is False
    )
    assert has_next_page("<div>No pagination</div>") is False


def test_export_orders_csv_writes_utf8_bom(tmp_path: Path) -> None:
    rows = parse_orders_html(MOCK_MYORDER_HTML)
    out_csv = tmp_path / "orders.csv"
    export_orders_csv(rows, out_csv)

    raw_bytes = out_csv.read_bytes()
    assert raw_bytes.startswith(b"\xef\xbb\xbf")

    df = pd.read_csv(out_csv, encoding="utf-8-sig", dtype=str).fillna("")
    assert len(df) == 3
    assert df.loc[0, "order_id"] == "PB-2025-0001"
    assert df.loc[0, "item_title"] == "【2次：2025年10月発送】MG 1/100 ガンダムMk-V"
