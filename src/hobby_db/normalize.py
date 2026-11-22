"""Title cleaning, Japanese date/price parsing, and deterministic ID generation."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime

# Unicode dash/minus variants converted to ASCII '-' (note: katakana prolonged sound mark U+30FC is preserved)
_DASH_TRANS = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
    }
)

_LENTICULAR_BRACKET_RE = re.compile(r"【[^】]*】")
_PAREN_SALE_TAG_RE = re.compile(r"\((?:\d+次[:：][^)]*|[^)]*(?:発送|抽選販売|再販|受注生産))\)")
_WHITESPACE_RE = re.compile(r"\s+")
_JAN_DIGITS_RE = re.compile(r"\D+")

_JP_FULL_DATE_RE = re.compile(r"(\d{4})\s*[年/\-]\s*(\d{1,2})\s*[月/\-]\s*(\d{1,2})\s*日?")
_JP_MONTH_RE = re.compile(r"(\d{4})\s*[年/\-]\s*(\d{1,2})\s*月?")


def normalize_title(raw_title: str) -> str:
    """Clean a product title for deduplication and market search queries.

    Converts full-width ASCII/digits/spaces to half-width via NFKC (while keeping
    Japanese katakana and prolonged sound marks intact), normalizes Unicode hyphens,
    strips sale/shipping tags like ``【2次：2025年8月発送】`` and ``【抽選販売】``,
    and collapses redundant whitespace.
    """
    if not raw_title:
        return ""

    text = unicodedata.normalize("NFKC", raw_title)
    text = text.translate(_DASH_TRANS)
    text = _LENTICULAR_BRACKET_RE.sub(" ", text)
    text = _PAREN_SALE_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def clean_jan_code(jan_code: str | None) -> str:
    """Normalize a JAN/EAN barcode string to digits only."""
    if not jan_code:
        return ""
    normalized = unicodedata.normalize("NFKC", jan_code).strip()
    return _JAN_DIGITS_RE.sub("", normalized)


def make_item_id(title: str, jan_code: str | None = None) -> str:
    """Return a deterministic item_id from JAN code or normalized title hash."""
    cleaned_jan = clean_jan_code(jan_code)
    if cleaned_jan:
        return cleaned_jan

    norm = normalize_title(title).lower()
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]
    return f"item_{digest}"


def make_entry_id(
    shop_prefix: str,
    order_id: str,
    line_index: int,
    unit_index: int | None = None,
) -> str:
    """Build a deterministic Inventory entry_id."""
    prefix = shop_prefix.strip().lower()
    order_clean = unicodedata.normalize("NFKC", order_id).strip()
    base = f"{prefix}_{order_clean}_{line_index}"
    if unit_index is None:
        return base
    return f"{base}_u{unit_index}"


def make_log_id(item_id: str, recorded_date: date | datetime) -> str:
    """Build a deterministic daily PriceHistory log_id: ``{item_id}_{YYYYMMDD}``."""
    return f"{item_id}_{recorded_date.strftime('%Y%m%d')}"


def parse_jpy_int(raw: str | int | float | None) -> int | None:
    """Parse a Japanese yen string or number into an integer."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return round(raw)

    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    return int(digits)


def parse_jp_date(raw: str | None) -> str:
    """Parse Japanese or ISO-like dates into ``YYYY-MM-DD`` or empty string."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw).strip()
    match = _JP_FULL_DATE_RE.search(text)
    if not match:
        return ""
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def parse_delivery_month(raw: str | None) -> str:
    """Parse delivery month strings like ``2025年8月発送`` into ``YYYY-MM-01``."""
    if not raw:
        return ""
    full_date = parse_jp_date(raw)
    if full_date:
        return full_date

    text = unicodedata.normalize("NFKC", raw).strip()
    match = _JP_MONTH_RE.search(text)
    if not match:
        return ""
    year, month = int(match.group(1)), int(match.group(2))
    try:
        return date(year, month, 1).isoformat()
    except ValueError:
        return ""


_SERIES_RULES: tuple[tuple[str, str, str], ...] = (
    ("METAL BUILD", "Tamashii Nations", "METAL BUILD"),
    ("METAL ROBOT魂", "Tamashii Nations", "METAL ROBOT魂"),
    ("GUNDAM FIX FIGURATION", "Tamashii Nations", "GFFMC"),
    ("METAL COMPOSITE", "Tamashii Nations", "GFFMC"),
    ("ROBOT魂", "Tamashii Nations", "ROBOT魂"),
    ("S.H.Figuarts", "Tamashii Nations", "S.H.Figuarts"),
    ("S.H.MonsterArts", "Tamashii Nations", "S.H.MonsterArts"),
    ("真骨彫", "Tamashii Nations", "S.H.Figuarts"),
    ("超合金", "Tamashii Nations", "Chogokin"),
    ("PG ", "Bandai Spirits", "Gunpla"),
    ("MG ", "Bandai Spirits", "Gunpla"),
    ("RG ", "Bandai Spirits", "Gunpla"),
    ("HG ", "Bandai Spirits", "Gunpla"),
    ("HGUC", "Bandai Spirits", "Gunpla"),
    ("FM ", "Bandai Spirits", "Gunpla"),
    ("RE/100", "Bandai Spirits", "Gunpla"),
    ("EG ", "Bandai Spirits", "Gunpla"),
    ("ガンプラ", "Bandai Spirits", "Gunpla"),
    ("ガンダム", "Bandai Spirits", "Gundam"),
    ("30MS", "Bandai Spirits", "30 MINUTES SISTERS"),
    ("30MM", "Bandai Spirits", "30 MINUTES MISSIONS"),
    ("フィギュアライズ", "Bandai Spirits", "Figure-rise"),
    ("Figure-rise", "Bandai Spirits", "Figure-rise"),
    ("仮面ライダー", "Bandai Spirits", "Kamen Rider"),
    ("ウルトラマン", "Bandai Spirits", "Ultraman"),
    ("ドラゴンボール", "Bandai Spirits", "Dragon Ball"),
    ("ワンピース", "Bandai Spirits", "One Piece"),
    ("エヴァンゲリオン", "Bandai Spirits", "Evangelion"),
)


def infer_series_and_brand(
    title: str,
    default_brand: str = "Premium Bandai",
) -> tuple[str, str]:
    """Infer ``(brand, series)`` from a normalized product title."""
    norm = normalize_title(title)
    lower = norm.lower()
    for keyword, brand, series in _SERIES_RULES:
        if keyword.lower() in lower:
            return brand, series
    return default_brand, ""
