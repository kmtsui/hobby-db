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
from hobby_db.pricers.jungle import JunglePricer, parse_jungle_search_html
from hobby_db.pricers.kakaku import KakakuPricer, build_kakaku_search_url, parse_kakaku_search_html
from hobby_db.pricers.mandarake import MandarakePricer, parse_mandarake_search_html
from hobby_db.pricers.rakuten import RakutenPricer
from hobby_db.pricers.surugaya import SurugayaPricer, parse_surugaya_search_html
from hobby_db.pricers.yahoo_auctions import YahooAuctionsPricer, parse_yahoo_auctions_html
from hobby_db.pricers.yahoo_shopping import YahooShoppingPricer, parse_yahoo_shopping_html


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


def test_surugaya_pricer_queries_new_and_used_classifications() -> None:
    seen_classified: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cls = request.url.params.get("sale_classified", "")
        seen_classified.append(cls)
        label = "新品" if cls == "新品" else "中古"
        price = "32,000円" if cls == "新品" else "24,000円"
        html = f"""
        <div class="item_box">
          <p class="title"><a href="/product/1">METAL BUILD サンプル</a></p>
          <div class="price_teika">{label}：<strong>{price}</strong></div>
        </div>
        """
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    item = CatalogItem(item_id="item_1", title="METAL BUILD サンプル")

    q_new = SurugayaPricer(condition="new", client=client, min_delay=0, max_delay=0).get_quote(item)
    q_used = SurugayaPricer(condition="used", client=client, min_delay=0, max_delay=0).get_quote(
        item
    )
    assert seen_classified == ["新品", "中古"]
    assert q_new.median_price_jpy == 32000
    assert q_used.median_price_jpy == 24000


def test_mandarake_pricer_splits_unopened_and_opened_with_session_seed() -> None:
    mock_html = """
    <div class="thumlarge">
      <div class="block" data-itemidx="1">
        <div class="basic"><p class="stock">In stock</p></div>
        <div class="title"><a href="/order/detailPage/item?itemCode=1">BANDAI METAL BUILD Destiny Gundam (Unopened)</a></div>
        <div class="price"><p>45,000 yen</p></div>
      </div>
      <div class="block" data-itemidx="2">
        <div class="basic"><p class="stock">Store Front Item</p></div>
        <div class="title"><a href="/order/detailPage/item?itemCode=2">BANDAI METAL BUILD Destiny Gundam</a></div>
        <div class="price"><p>34,000 yen</p></div>
      </div>
      <div class="block" data-itemidx="3">
        <div class="basic"><p class="stock">In stock</p></div>
        <div class="title"><a href="/order/detailPage/item?itemCode=3">METAL BUILD Destiny Box Only 空箱</a></div>
        <div class="price"><p>1,000 yen</p></div>
      </div>
    </div>
    """
    new_only = parse_mandarake_search_html(mock_html, condition="new")
    used_only = parse_mandarake_search_html(mock_html, condition="used")
    assert [x.price_jpy for x in new_only] == [45000]
    assert [x.price_jpy for x in used_only] == [34000, 1000]

    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request.url.path)
        if request.url.path == "/order/":
            return httpx.Response(200, text="<html></html>")
        return httpx.Response(200, text=mock_html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pricer = MandarakePricer(client=client, min_delay=0, max_delay=0)
    new_hits, used_hits = pricer.fetch_both_conditions(
        CatalogItem(item_id="item_m", title="METAL BUILD Destiny")
    )
    assert requests_seen == ["/order/", "/order/listPage/list"]
    assert [x.price_jpy for x in new_hits] == [45000]
    assert [x.price_jpy for x in used_hits] == [34000, 1000]


def test_yahoo_shopping_pricer_parses_beacon_and_condition_flags() -> None:
    mock_html = """
    <div class="SearchResult_SearchResultItem__contents__abc">
      <a data-beacon="catid:15170;targurl:store.shopping.yahoo.co.jp/shop/a.html;prc:52800;" href="https://store.shopping.yahoo.co.jp/shop/a.html">METAL BUILD デスティニー 新品</a>
      <span class="price">52,800円</span>
    </div>
    <div class="SearchResult_SearchResultItem__contents__abc">
      <a href="https://store.shopping.yahoo.co.jp/shop/b.html">【中古】METAL BUILD デスティニー ショップB</a>
      <span class="price">31,400円</span>
    </div>
    """
    new_only = parse_yahoo_shopping_html(mock_html, condition="new")
    used_only = parse_yahoo_shopping_html(mock_html, condition="used")
    assert [(x.price_jpy, x.condition) for x in new_only] == [(52800, "new")]
    assert [(x.price_jpy, x.condition) for x in used_only] == [(31400, "used")]

    seen_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_calls.append(request.url.params.get("p", ""))
        return httpx.Response(200, text=mock_html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    item = CatalogItem(item_id="item_y", title="METAL BUILD デスティニー")
    new_hits, used_hits = YahooShoppingPricer(
        client=client, min_delay=0, max_delay=0
    ).fetch_both_conditions(item)
    assert len(seen_calls) == 1
    assert [x.price_jpy for x in new_hits] == [52800]
    assert [x.price_jpy for x in used_hits] == [31400]


def test_kakaku_pricer_cp932_encoding_and_used_filtering() -> None:
    url = build_kakaku_search_url("METAL BUILD デスティニー")
    assert "%83f%83X%83e" in url

    mock_html = """
    <html><head><meta charset="Shift_JIS"></head><body>
      <div class="p-resultItem_in p-item">
        <p class="p-item_name"><a href="https://kakaku.com/ksearch/jump/?1">METAL BUILD デスティニーガンダム</a></p>
        <p class="p-item_priceNum">67,633</p>
      </div>
      <div class="p-resultItem_in p-item">
        <p class="p-item_name"><a href="https://kakaku.com/ksearch/jump/?2">【中古】METAL BUILD デスティニーガンダム</a></p>
        <p class="p-item_priceNum">44,800</p>
      </div>
    </body></html>
    """.encode("cp932")

    assert [x.price_jpy for x in parse_kakaku_search_html(mock_html, condition="new")] == [67633]
    assert [x.price_jpy for x in parse_kakaku_search_html(mock_html, condition="used")] == [44800]

    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=mock_html))
    )
    item = CatalogItem(item_id="item_k", title="METAL BUILD デスティニー")
    assert (
        KakakuPricer(condition="new", client=client, min_delay=0, max_delay=0)
        .get_quote(item)
        .median_price_jpy
        == 67633
    )
    assert (
        KakakuPricer(condition="used", client=client, min_delay=0, max_delay=0)
        .get_quote(item)
        .median_price_jpy
        == 44800
    )


def test_yahoo_auctions_pricer_parses_next_data_and_istatus() -> None:
    mock_html = """
    <html><body>
      <script id="__NEXT_DATA__" type="application/json">
      {"props":{"pageProps":{"initialState":{"search":{"items":{"listing":{"items":[
        {"auctionId":"a1","title":"METAL BUILD デスティニー","price":42000,"isNoPrice":false},
        {"auctionId":"a2","title":"METAL BUILD デスティニー ジャンク","price":5000,"isNoPrice":false},
        {"auctionId":"a3","title":"METAL BUILD デスティニー","price":46000,"isNoPrice":false}
      ]}}}}}}}
      </script>
    </body></html>
    """
    parsed = parse_yahoo_auctions_html(mock_html, condition="used")
    assert [(x.price_jpy, x.url) for x in parsed] == [
        (42000, "https://page.auctions.yahoo.co.jp/jp/auction/a1"),
        (5000, "https://page.auctions.yahoo.co.jp/jp/auction/a2"),
        (46000, "https://page.auctions.yahoo.co.jp/jp/auction/a3"),
    ]

    seen_istatus: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_istatus.append(request.url.params.get("istatus", ""))
        return httpx.Response(200, text=mock_html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    item = CatalogItem(item_id="item_ya", title="METAL BUILD デスティニー")
    q_new = YahooAuctionsPricer(condition="new", client=client, min_delay=0, max_delay=0).get_quote(
        item
    )
    q_used = YahooAuctionsPricer(
        condition="used", client=client, min_delay=0, max_delay=0
    ).get_quote(item)
    assert seen_istatus == ["1", "2,3,4,5"]
    assert q_new.median_price_jpy == 44000
    assert q_used.median_price_jpy == 44000

    fallback_html = """
    <html><body>
      <div>METAL BUILD キュリオス に一致する商品はありません。キーワードの一部を利用した結果を表示しています。</div>
      <script id="__NEXT_DATA__" type="application/json">
      {"props":{"pageProps":{"initialState":{"search":{"items":{"listing":{"items":[
        {"auctionId":"a9","title":"METAL BUILD ケンプファー","price":34000,"isNoPrice":false}
      ]}}}}}}}
      </script>
    </body></html>
    """
    assert parse_yahoo_auctions_html(fallback_html, condition="used") == []


def test_jungle_pricer_parses_cards_skips_sold_out_and_uses_rank_filters() -> None:
    mock_html = """
    <ul class="ec-shelfGrid">
      <li class="ec-shelfGrid__item">
        <div class="ec-productItemRole__image"><a href="/sale/products/detail/10"></a></div>
        <h3 class="ec-productItemRole__title"><span>METAL BUILD デスティニー</span></h3>
        <span class="ec-price__price">￥38,500</span>
      </li>
      <li class="ec-shelfGrid__item">
        <div class="ec-productItemRole__image"><a href="/sale/products/detail/11"></a></div>
        <h3 class="ec-productItemRole__title"><span>METAL BUILD デスティニー</span></h3>
        <span class="ec-price__price">￥29,000</span>
        <p>売切</p>
      </li>
    </ul>
    """
    parsed = parse_jungle_search_html(mock_html, condition="used")
    assert len(parsed) == 1
    assert parsed[0].price_jpy == 38500
    assert parsed[0].url == "https://jungle-scs.co.jp/sale/products/detail/10"

    seen_ranks: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        rank = request.url.params.get("rank", "")
        seen_ranks.append(rank)
        if rank == "3":
            return httpx.Response(200, text="<ul></ul>")
        return httpx.Response(200, text=mock_html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    item = CatalogItem(item_id="item_j", title="METAL BUILD デスティニー")
    q_new = JunglePricer(condition="new", client=client, min_delay=0, max_delay=0).get_quote(item)
    q_used = JunglePricer(condition="used", client=client, min_delay=0, max_delay=0).get_quote(item)
    assert seen_ranks == ["0", "3", "5"]
    assert q_new.median_price_jpy == 38500
    assert q_used.median_price_jpy == 38500


def test_update_prices_pools_new_and_used_across_sources() -> None:
    from scripts.update_prices import fetch_item_quotes

    class StubPricer:
        def __init__(self, source: str, listings: list[PriceListing]) -> None:
            self.source_name = source
            self._listings = listings

        def fetch_listings(self, item: CatalogItem) -> list[PriceListing]:
            return list(self._listings)

    item = CatalogItem(item_id="item_pool", title="METAL BUILD デスティニー")
    domain_pricers = {
        "yahoo_shopping": {
            "new": StubPricer(
                "yahoo_shopping",
                [
                    PriceListing(
                        title="A", price_jpy=50000, source="yahoo_shopping", condition="new"
                    )
                ],
            ),
            "used": StubPricer(
                "yahoo_shopping",
                [
                    PriceListing(
                        title="A", price_jpy=30000, source="yahoo_shopping", condition="used"
                    )
                ],
            ),
        },
        "surugaya": {
            "new": StubPricer(
                "surugaya",
                [PriceListing(title="A", price_jpy=54000, source="surugaya", condition="new")],
            ),
            "used": StubPricer(
                "surugaya",
                [PriceListing(title="A", price_jpy=34000, source="surugaya", condition="used")],
            ),
        },
    }

    q_new, q_used = fetch_item_quotes(item, domain_pricers, ["new", "used"])  # type: ignore[arg-type]
    assert q_new is not None
    assert q_new.median_price_jpy == 52000
    assert q_new.sample_size == 2
    assert q_new.source == "surugaya,yahoo_shopping"
    assert q_used is not None
    assert q_used.median_price_jpy == 32000
    assert q_used.sample_size == 2
    assert q_used.source == "surugaya,yahoo_shopping"
