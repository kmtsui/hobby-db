"""Tests for title normalization, Japanese date/price parsing, and deterministic IDs."""

from datetime import UTC, datetime

from hobby_db.normalize import (
    clean_jan_code,
    infer_series_and_brand,
    make_entry_id,
    make_item_id,
    make_log_id,
    normalize_title,
    parse_delivery_month,
    parse_jp_date,
    parse_jpy_int,
)


def test_normalize_title_strips_pbandai_tags_and_fullwidth() -> None:
    raw = "【２次：2025年8月発送】【抽選販売】ＭＧ　１／１００　フリーダムガンダム　Ｖｅｒ．Ｋａ"
    assert normalize_title(raw) == "MG 1/100 フリーダムガンダム Ver.Ka"


def test_normalize_title_preserves_katakana_prolonged_mark_and_editions() -> None:
    raw = "【再販】METAL BUILD ストライクフリーダムガンダム (Ver.Ka) (2次：2025年9月発送)"
    assert normalize_title(raw) == "METAL BUILD ストライクフリーダムガンダム (Ver.Ka)"


def test_normalize_title_handles_unicode_hyphens() -> None:
    raw = "HG 1/144 RX\u221278\u20132 ガンダム［クリアカラー］"
    assert normalize_title(raw) == "HG 1/144 RX-78-2 ガンダム[クリアカラー]"


def test_normalize_title_strips_fulfillment_note_suffix() -> None:
    assert (
        normalize_title(
            "【開催記念商品】METAL BUILD ストライクフリーダムガンダム SOUL BLUE Ver. ※会場受け取り"
        )
        == "METAL BUILD ストライクフリーダムガンダム SOUL BLUE Ver."
    )
    assert (
        normalize_title(
            "【開催記念商品】METAL BUILD ダブルオーライザー デザイナーズブルー Ver. ※会場受け取り"
        )
        == "METAL BUILD ダブルオーライザー デザイナーズブルー Ver."
    )


def test_make_item_id_prefers_jan_else_stable_hash() -> None:
    assert make_item_id("Some Gunpla", "４５７３－１０２６－５００００") == "4573102650000"
    id1 = make_item_id("【1次：2025年5月発送】ＭＧ　ジム・スナイパーII")
    id2 = make_item_id("【2次：2025年8月発送】MG ジム・スナイパーII")
    assert id1 == id2
    assert id1.startswith("item_")
    assert len(id1) == 17


def test_make_entry_id_and_log_id() -> None:
    assert make_entry_id("pbandai", "20250101-999", 1) == "pbandai_20250101-999_1"
    assert make_entry_id("pbandai", "20250101-999", 2, unit_index=2) == "pbandai_20250101-999_2_u2"
    dt = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    assert make_log_id("4573102650000", dt) == "4573102650000_20261122"


def test_parse_jpy_int_and_dates() -> None:
    assert parse_jpy_int("¥12,100") == 12100
    assert parse_jpy_int("１２，１００円（税込）") == 12100
    assert parse_jpy_int("￥21,600  ～ ￥27,000 ") == 21600
    assert parse_jpy_int("20%OFF ￥32,800 ￥41,000") == 32800
    assert parse_jpy_int("8,600円 (元値 9,500円)") == 8600
    assert parse_jpy_int("数量：2") == 2
    assert parse_jpy_int("") is None
    assert parse_jpy_int(None) is None
    assert clean_jan_code("4573-1026-50000") == "4573102650000"

    assert parse_jp_date("2025年8月3日") == "2025-08-03"
    assert parse_jp_date("2025/08/03") == "2025-08-03"
    assert parse_jp_date("invalid") == ""

    assert parse_delivery_month("2025年10月発送") == "2025-10-01"
    assert parse_delivery_month("2025-10") == "2025-10-01"


def test_infer_series_and_brand() -> None:
    assert infer_series_and_brand("METAL BUILD ゼータガンダム") == (
        "Tamashii Nations",
        "METAL BUILD",
    )
    assert infer_series_and_brand("MG 1/100 ゼータガンダム Ver.Ka") == ("Bandai Spirits", "Gunpla")
    assert infer_series_and_brand("S.H.Figuarts 仮面ライダーファイズ") == (
        "Tamashii Nations",
        "S.H.Figuarts",
    )
