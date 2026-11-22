"""Tests for Premium Bandai CSV ingestion into Catalog and Inventory models."""

from pathlib import Path

from hobby_db.crawlers.pbandai import export_orders_csv
from hobby_db.ingestors.pbandai_csv import ingest_pbandai_csv, map_pbandai_status
from hobby_db.models import PBandaiOrderRow


def test_map_pbandai_status() -> None:
    assert map_pbandai_status("キャンセル済み") == "Cancelled"
    assert map_pbandai_status("キャンセル") == "Cancelled"
    assert map_pbandai_status("発送済み") == "Owned"
    assert map_pbandai_status("配達完了") == "Owned"
    assert map_pbandai_status("配送完了") == "Owned"
    assert map_pbandai_status("配送完了 (おまとめ元: 00042)") == "Owned"
    assert map_pbandai_status("受注完了") == "Pre-ordered"
    assert map_pbandai_status("注文受付済") == "Pre-ordered"
    assert map_pbandai_status("") == "Pre-ordered"


def test_ingest_pbandai_csv_deduplicates_catalog_and_expands_quantity(tmp_path: Path) -> None:
    rows = [
        PBandaiOrderRow(
            order_id="ORD-100",
            order_date="2025-01-15",
            item_title="【1次：2025年6月発送】ＭＧ　１／１００　ガンダムＭｋ－Ｖ",
            unit_price_jpy=8800,
            quantity=2,
            shipping_jpy=660,
            order_status="受注完了",
            delivery_month="2025-06-01",
            image_url="",
        ),
        PBandaiOrderRow(
            order_id="ORD-200",
            order_date="2025-02-20",
            item_title="【2次：2025年9月発送】MG 1/100 ガンダムMk-V",
            unit_price_jpy=8800,
            quantity=1,
            shipping_jpy=660,
            order_status="発送済み",
            delivery_month="2025-09-01",
            image_url="https://p-bandai.jp/img/mkv.jpg",
        ),
    ]
    csv_path = tmp_path / "orders.csv"
    export_orders_csv(rows, csv_path)

    catalog, inventory = ingest_pbandai_csv(csv_path, expand_quantity=True)

    # Same product across 1次 and 2次 must collapse into 1 Catalog item with merged image_url
    assert len(catalog) == 1
    assert catalog[0].title == "MG 1/100 ガンダムMk-V"
    assert catalog[0].brand == "Bandai Spirits"
    assert catalog[0].series == "Gunpla"
    assert catalog[0].image_url == "https://p-bandai.jp/img/mkv.jpg"

    # ORD-100 (qty=2) expands to _u1 and _u2; ORD-200 (qty=1) uses base _1
    assert len(inventory) == 3
    assert [e.entry_id for e in inventory] == [
        "pbandai_ORD-100_1_u1",
        "pbandai_ORD-100_1_u2",
        "pbandai_ORD-200_1",
    ]
    assert inventory[0].shipping_jpy == 660
    assert inventory[1].shipping_jpy == 0
    assert inventory[0].status == "Pre-ordered"
    assert inventory[2].status == "Owned"


def test_ingest_pbandai_csv_without_quantity_expansion(tmp_path: Path) -> None:
    rows = [
        PBandaiOrderRow(
            order_id="ORD-300",
            order_date="2025-03-01",
            item_title="RG 1/144 アカツキガンダム",
            unit_price_jpy=5500,
            quantity=3,
            shipping_jpy=660,
            order_status="受注完了",
        )
    ]
    csv_path = tmp_path / "orders_no_expand.csv"
    export_orders_csv(rows, csv_path)

    _, inventory = ingest_pbandai_csv(csv_path, expand_quantity=False)
    assert len(inventory) == 1
    assert inventory[0].entry_id == "pbandai_ORD-300_1"
    assert inventory[0].notes == "Qty: 3"


def test_ingest_pbandai_csv_records_consolidation_and_extra_item_notes(tmp_path: Path) -> None:
    rows = [
        PBandaiOrderRow(
            order_id="00041",
            order_date="2023-03-14",
            item_title="S.H.Figuarts 孫悟飯ビースト",
            unit_price_jpy=3810,
            quantity=1,
            shipping_jpy=0,
            order_status="配送完了 (おまとめ元: 00042)",
        ),
        PBandaiOrderRow(
            order_id="00011",
            order_date="2019-06-24",
            item_title="【受注販売】METAL BUILD ストライクガンダム",
            unit_price_jpy=24437,
            quantity=1,
            shipping_jpy=0,
            order_status="配送完了 (他1品)",
        ),
    ]
    csv_path = tmp_path / "orders_notes.csv"
    export_orders_csv(rows, csv_path)

    _, inventory = ingest_pbandai_csv(csv_path)
    assert inventory[0].status == "Owned"
    assert inventory[0].notes == "Consolidated into order 00042"
    assert inventory[1].status == "Owned"
    assert inventory[1].notes == "Contains 他1品 (check order detail)"
