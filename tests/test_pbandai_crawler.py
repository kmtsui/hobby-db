"""Tests for Premium Bandai HTML parsing, pagination/login detection, and BOM CSV export."""

from pathlib import Path

import pandas as pd

from hobby_db.crawlers.pbandai import (
    export_orders_csv,
    has_next_page,
    is_login_required,
    parse_orders_from_path,
    parse_orders_html,
    resolve_consolidated_orders,
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


MOCK_MYP_TABLE_HTML_P1 = """
<!doctype html>
<html lang="ja">
<head><meta charset="Shift_JIS"></head>
<body>
  <table class="myp-table myp-table-order">
    <tr><th>注文日</th><th>商品名</th><th>発送月</th><th>ご注文金額</th><th>現在の状況</th></tr>
    <tr>
      <td rowspan="2"><a href="/myorder_d/00069/">2026/11/13<br>（No. 00069）</a></td>
      <td><span class="goods-stat">予約品</span><br>METAL BUILD ガンダムキュリオス</td>
      <td>2027年06月</td>
      <td>39,160円 （税込）<br>クレジットカード</td>
      <td>注文受付済</td>
    </tr>
    <tr><td colspan="4"><div class="myp-actions"></div></td></tr>
    <tr>
      <td rowspan="2"><a href="/myorder_d/00062/">2025/11/10<br>（No. 00062）</a></td>
      <td><span class="goods-stat">在庫品</span><br>【PB対応品】METAL BUILD 交換用パーツ</td>
      <td></td>
      <td>0円 （税込）<br>元払</td>
      <td>配送完了</td>
    </tr>
    <tr><td colspan="4"><div class="myp-actions"></div></td></tr>
    <tr>
      <td rowspan="2"><a href="/myorder_d/00042/">2023/3/14<br>（No. 00042）</a></td>
      <td><span class="goods-stat">予約品</span><br>S.H.Figuarts 孫悟飯ビースト<br>他1品</td>
      <td></td>
      <td>60,790円 （税込）<br>クレジットカード</td>
      <td>配送完了<br>おまとめ注文</td>
    </tr>
    <tr><td colspan="4"><div class="myp-actions"></div></td></tr>
  </table>
</body>
</html>
"""

MOCK_MYP_TABLE_HTML_P2 = """
<!doctype html>
<html lang="ja">
<head><meta charset="Shift_JIS"></head>
<body>
  <table class="myp-table myp-table-order">
    <tr><th>注文日</th><th>商品名</th><th>発送月</th><th>ご注文金額</th><th>現在の状況</th></tr>
    <tr>
      <td rowspan="2"><a href="/myorder_d/00041/">2023/3/14<br>（No. 00041）</a></td>
      <td><span class="goods-stat">予約品</span><br>S.H.Figuarts 孫悟飯ビースト</td>
      <td></td>
      <td>3,810円 （税込）<br>クレジットカード</td>
      <td>おまとめ済み<br>00042</td>
    </tr>
    <tr><td colspan="4"><div class="myp-actions"></div></td></tr>
    <tr>
      <td rowspan="2"><a href="/myorder_d/00037/">2022/11/24<br>（No. 00037）</a></td>
      <td><span class="goods-stat">予約品</span><br>METAL BUILD GNアームズ TYPE-E【2次：2023年7月発送分】</td>
      <td></td>
      <td>58,608円 （税込）<br>クレジットカード</td>
      <td>おまとめ済み<br>00042</td>
    </tr>
    <tr><td colspan="4"><div class="myp-actions"></div></td></tr>
    <tr>
      <td rowspan="2"><a href="/myorder_d/00011/">2019/6/24<br>（No. 00011）</a></td>
      <td><span class="goods-stat">予約品</span><br>【受注販売】METAL BUILD ストライクガンダム<br>他1品</td>
      <td></td>
      <td>24,437円 （税込）<br>コンビニ支払</td>
      <td>配送完了</td>
    </tr>
    <tr><td colspan="4"><div class="myp-actions"></div></td></tr>
  </table>
</body>
</html>
"""


def test_parse_myp_table_cp932_and_consolidation(tmp_path: Path) -> None:
    p1 = tmp_path / "page_1.htm"
    p2 = tmp_path / "page_2.htm"
    p1.write_bytes(MOCK_MYP_TABLE_HTML_P1.encode("cp932"))
    p2.write_bytes(MOCK_MYP_TABLE_HTML_P2.encode("cp932"))

    raw_p1 = parse_orders_html(p1.read_bytes())
    assert len(raw_p1) == 3
    assert raw_p1[0].order_id == "00069"
    assert raw_p1[0].item_title == "METAL BUILD ガンダムキュリオス"
    assert raw_p1[0].unit_price_jpy == 39160
    assert raw_p1[0].delivery_month == "2027-06-01"
    assert raw_p1[1].unit_price_jpy == 0
    assert raw_p1[2].order_status == "配送完了 おまとめ注文"

    resolved = parse_orders_from_path(tmp_path)
    ids = [r.order_id for r in resolved]
    # Wrapper 00042 is dropped because child orders 00041 and 00037 are present
    assert ids == ["00069", "00062", "00041", "00037", "00011"]

    by_id = {r.order_id: r for r in resolved}
    assert by_id["00041"].order_status == "配送完了 (おまとめ元: 00042)"
    assert by_id["00037"].order_status == "配送完了 (おまとめ元: 00042)"
    assert by_id["00011"].item_title == "【受注販売】METAL BUILD ストライクガンダム"
    assert by_id["00011"].order_status == "配送完了 (他1品)"

    # Standalone wrapper without children is preserved
    only_p1 = resolve_consolidated_orders(raw_p1)
    assert any(r.order_id == "00042" for r in only_p1)
