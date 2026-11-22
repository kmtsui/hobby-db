"""Tests for junk-listing filtering, IQR outlier removal, median aggregation, and pricers."""

from datetime import UTC, datetime, timedelta

import httpx

from hobby_db.models import CatalogItem
from hobby_db.pricers.base import (
    PriceListing,
    aggregate_listings,
    filter_outliers_iqr,
    is_item_stale,
    is_junk_listing,
)
from hobby_db.pricers.rakuten import RakutenPricer
from hobby_db.pricers.surugaya import SurugayaPricer, parse_surugaya_search_html


def test_is_junk_listing_filters_all_required_terms_and_fullwidth() -> None:
    junk_titles = [
        "MG ゼータガンダム ジャンク品",
        "METAL BUILD ストライク 箱のみ",
        "PG ガンダム 説明書のみ",
        "ガンプラ 大量 まとめ売り",
        "MG サザビー パーツ取り用",
        "HG 空箱 コレクション",
        "ｼﾞｬﾝｸ ﾊﾟｰﾂセット",
    ]
    for title in junk_titles:
        assert is_junk_listing(title) is True, f"Expected junk for: {title}"

    assert is_junk_listing("MG 1/100 ゼータガンダム Ver.Ka 未組立") is False


def test_filter_outliers_and_aggregate_median() -> None:
    raw_prices = [300, 7800, 8000, 8200, 8400, 8500, 45000]
    filtered = filter_outliers_iqr(raw_prices)
    assert 300 not in filtered
    assert 45000 not in filtered
    assert filtered == [7800, 8000, 8200, 8400, 8500]

    listings = [
        PriceListing(
            title="MG ガンダム ジャンク", price_jpy=1500, source="surugaya", condition="used"
        ),
        PriceListing(title="MG ガンダム 空箱", price_jpy=500, source="surugaya", condition="used"),
        PriceListing(title="MG ガンダム A", price_jpy=8000, source="surugaya", condition="used"),
        PriceListing(title="MG ガンダム B", price_jpy=8200, source="surugaya", condition="used"),
        PriceListing(title="MG ガンダム C", price_jpy=8400, source="surugaya", condition="used"),
        PriceListing(title="MG ガンダム D", price_jpy=8600, source="surugaya", condition="used"),
        PriceListing(
            title="MG ガンダム 高額プレ値", price_jpy=99000, source="surugaya", condition="used"
        ),
    ]
    quote = aggregate_listings(listings, source="surugaya", condition="used")
    assert quote.filtered_junk_count == 2
    assert quote.filtered_outlier_count == 1
    assert quote.sample_size == 4
    assert quote.median_price_jpy == 8300


def test_is_item_stale() -> None:
    now = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
    fresh_ts = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    stale_ts = (now - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")

    assert is_item_stale(CatalogItem(item_id="1", title="A", price_updated_at=""), 7, now) is True
    assert (
        is_item_stale(CatalogItem(item_id="1", title="A", price_updated_at="bad"), 7, now) is True
    )
    assert (
        is_item_stale(CatalogItem(item_id="1", title="A", price_updated_at=stale_ts), 7, now)
        is True
    )
    assert (
        is_item_stale(CatalogItem(item_id="1", title="A", price_updated_at=fresh_ts), 7, now)
        is False
    )


def test_rakuten_pricer_prefers_jan_and_falls_back_to_title() -> None:
    seen_keywords: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        kw = request.url.params.get("keyword", "")
        seen_keywords.append(kw)
        if kw == "4573102650000":
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {
                            "itemName": "MG ゼータガンダム Ver.Ka 新品",
                            "itemPrice": 7400,
                            "usedFlag": 0,
                            "itemUrl": "https://r.example/1",
                        },
                        {
                            "itemName": "MG ゼータガンダム Ver.Ka 中古",
                            "itemPrice": 5000,
                            "usedFlag": 1,
                            "itemUrl": "https://r.example/2",
                        },
                        {
                            "itemName": "MG ゼータガンダム Ver.Ka 説明書のみ",
                            "itemPrice": 500,
                            "usedFlag": 0,
                            "itemUrl": "https://r.example/3",
                        },
                    ]
                },
            )
        if kw == "4573102000000":
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(
            200,
            json={
                "Items": [
                    {
                        "itemName": "Fallback Item 新品",
                        "itemPrice": 9100,
                        "usedFlag": 0,
                        "itemUrl": "https://r.example/4",
                    },
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pricer = RakutenPricer(app_id="dummy-app-id", client=client, min_delay=0, max_delay=0)

    item_with_jan = CatalogItem(
        item_id="4573102650000", jan_code="4573102650000", title="MG ゼータ"
    )
    quote1 = pricer.get_quote(item_with_jan)
    assert quote1.median_price_jpy == 7400
    assert quote1.sample_size == 1
    assert seen_keywords == ["4573102650000"]

    seen_keywords.clear()
    item_fallback = CatalogItem(
        item_id="4573102000000",
        jan_code="4573102000000",
        title="【2次：2025年8月発送】テスト商品",
        search_query_override="カスタム検索語",
    )
    quote2 = pricer.get_quote(item_fallback)
    assert seen_keywords == ["4573102000000", "カスタム検索語"]
    assert quote2.median_price_jpy == 9100


def test_surugaya_pricer_parses_html_and_filters_junk() -> None:
    mock_html = """
    <html><body>
      <div class="item_box">
        <p class="title"><a href="/product/detail/101">MG ゼータガンダム Ver.Ka</a></p>
        <div class="price_teika">中古：<strong>6,200円</strong></div>
      </div>
      <div class="item_box">
        <p class="title"><a href="/product/detail/102">MG ゼータガンダム Ver.Ka</a></p>
        <div class="price_teika">中古：<strong>6,600円</strong></div>
      </div>
      <div class="item_box">
        <p class="title"><a href="/product/detail/103">MG ゼータガンダム Ver.Ka 箱のみ</a></p>
        <div class="price_teika">中古：<strong>400円</strong></div>
      </div>
      <div class="item_box">
        <p class="title"><a href="/product/detail/104">MG ゼータガンダム Buyback Only</a></p>
        <div class="price_teika">買取価格：<strong>3,500円</strong></div>
      </div>
    </body></html>
    """
    parsed = parse_surugaya_search_html(mock_html)
    assert len(parsed) == 3  # Buyback-only entry skipped at parse time

    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=mock_html))
    )
    pricer = SurugayaPricer(client=client, min_delay=0, max_delay=0)
    quote = pricer.get_quote(CatalogItem(item_id="item_x", title="MG ゼータガンダム Ver.Ka"))
    assert quote.median_price_jpy == 6400
    assert quote.sample_size == 2
    assert quote.filtered_junk_count == 1


def test_surugaya_pricer_handles_price_ranges_and_strikethrough_discounts() -> None:
    mock_html = """
    <html><body>
      <div class="item_box">
        <p class="title"><a href="/product/detail/201">METAL BUILD デスティニーガンダム(ハイネ機)</a></p>
        <div class="price_teika"><span class="text-red"><strong>￥21,600  ～ ￥27,000 </strong></span></div>
      </div>
      <div class="item_box">
        <p class="title"><a href="/product/detail/202">METAL BUILD デスティニーガンダム(ハイネ機)</a></p>
        <div class="price_teika">中古：<strong><del>￥27,000</del> ￥22,000</strong></div>
      </div>
    </body></html>
    """
    parsed = parse_surugaya_search_html(mock_html)
    assert [item.price_jpy for item in parsed] == [21600, 22000]
