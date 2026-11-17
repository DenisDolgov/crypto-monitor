"""Number, side, date and direction rules (ADR-0001)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from feedscan.currencies import Currency, CurrencyRegistry
from feedscan.models import (
    Orientation,
    RawData,
    RawRate,
    RateDerived,
    ReserveCurrency,
    Side,
)
from feedscan.normalize import (
    normalize_document,
    parse_amount,
    parse_decimal,
    parse_side,
    parse_updated_at,
)

FETCHED_AT = datetime(2026, 11, 17, 12, 0, 0, tzinfo=timezone.utc)
RAW = RawData(
    sha256="0" * 64,
    size_bytes=100,
    format="own_schema",
    encoding="utf-8",
    http_status=200,
    content_type="application/xml",
    gzip=False,
)


def registry() -> CurrencyRegistry:
    return CurrencyRegistry(
        [
            Currency("USDT", "Tether", "crypto", ("USDTTRC", "USDT-TRC20")),
            Currency("BTC", "Bitcoin", "crypto", ()),
            Currency("ETH", "Ether", "crypto", ()),
            Currency("RUB", "Rouble", "fiat", ("SberbankRUB", "TinkoffRUB")),
            Currency("XAU", "Gold", "metal", ()),
        ]
    )


def raw(**overrides) -> RawRate:
    data = dict(
        source_url="https://a.test/rates.xml",
        base_id="USDT",
        quote_id="RUB",
        rate_raw="92.35",
        orientation=Orientation.QUOTE_PER_BASE,
    )
    data.update(overrides)
    return RawRate(**data)


def normalize(*raws: RawRate):
    return normalize_document(list(raws), registry(), exchanger_domain="a.test", fetched_at=FETCHED_AT, raw=RAW)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("92.35", Decimal("92.35")),
        (" 92.35 ", Decimal("92.35")),
        ("78,5", Decimal("78.5")),
        ("1 000.50", Decimal("1000.50")),
        ("1\u00a0000,50", Decimal("1000.50")),
        ("1'000.5", Decimal("1000.5")),
        ("1_000.5", Decimal("1000.5")),
        ("1,234,567", Decimal("1234567")),
        ("1.234.567", Decimal("1234567")),
    ],
)
def test_decimal_formats_are_accepted(text, expected):
    assert parse_decimal(text) == expected


@pytest.mark.parametrize("text", ["1,234.56", "1.234,56", "12,34,56", "0.000,1"])
def test_ambiguous_separators_are_rejected(text):
    value, reason = parse_decimal(text, with_reason=True)
    assert value is None
    assert reason in ("ambiguous_number", "non_numeric_value")


@pytest.mark.parametrize("text", ["", "n/a", "-", "—", None, "  "])
def test_missing_optional_values_are_none(text):
    assert parse_amount(text) is None


@pytest.mark.parametrize("text", ["abc", "1e5", "NaN", "Infinity", "inf"])
def test_non_numeric_values_are_rejected(text):
    value, reason = parse_decimal(text, with_reason=True)
    assert value is None
    assert reason == "non_numeric_value"


def test_rate_is_quote_per_base():
    document = normalize(raw())
    rate = document.rates[0]
    assert rate.rate == Decimal("92.35")
    assert rate.rate_derived is RateDerived.DIRECT
    assert rate.direction == "USDT/RUB"
    assert rate.exchanger_domain == "a.test"
    assert rate.source_url == "https://a.test/rates.xml"


def test_inverted_feed_is_inverted_in_decimal():
    document = normalize(raw(base_id="RUB", quote_id="USDT", rate_raw="0.0108", orientation=Orientation.BASE_PER_QUOTE))
    rate = document.rates[0]
    assert rate.rate_derived is RateDerived.INVERTED
    assert rate.rate == Decimal(1) / Decimal("0.0108")


def test_in_and_out_give_rate_and_support_quoted_amounts():
    document = normalize(raw(rate_raw=None, in_raw="100", out_raw="9235"))
    assert document.rates[0].rate == Decimal("92.35")


def test_zero_or_negative_rate_is_dropped():
    document = normalize(raw(rate_raw="0"), raw(rate_raw="-92.35"))
    assert document.rates == ()
    assert document.dropped_by_reason == {"invalid_rate": 2}


def test_base_equal_to_quote_is_dropped():
    document = normalize(raw(base_id="USDT", quote_id="USDT"))
    assert document.dropped_by_reason == {"invalid_direction": 1}


def test_unknown_currency_is_dropped_and_counted():
    document = normalize(raw(base_id="WOWCOIN"), raw(base_id="BTC", quote_id="SberbankRUB"))
    assert len(document.rates) == 1
    assert document.rates[0].quote == "RUB"
    assert document.dropped_by_reason == {"unmapped_currency:WOWCOIN": 1}
    assert document.drops[0].raw_currency_id == "WOWCOIN"


def test_missing_required_field_is_dropped():
    document = normalize(raw(rate_raw=None, in_raw=None, out_raw=None))
    assert document.dropped_by_reason == {"missing_required_field": 1}


def test_reserve_in_quote_is_kept_as_is():
    document = normalize(raw(reserve_raw="150000", reserve_currency=ReserveCurrency.QUOTE))
    rate = document.rates[0]
    assert rate.reserve == Decimal("150000")
    assert rate.reserve_derived is False


def test_reserve_in_base_is_converted_and_flagged():
    document = normalize(raw(reserve_raw="2", reserve_currency=ReserveCurrency.BASE))
    rate = document.rates[0]
    assert rate.reserve == Decimal("2") * Decimal("92.35")
    assert rate.reserve_derived is True


def test_reserve_in_third_currency_is_dropped_with_warning():
    document = normalize(
        raw(
            reserve_raw="1000",
            reserve_currency=ReserveCurrency.UNKNOWN,
            reserve_currency_id="USD",
        )
    )
    rate = document.rates[0]
    assert rate.reserve is None
    assert "reserve_currency_mismatch" in rate.warnings


def test_negative_reserve_drops_the_row():
    document = normalize(raw(reserve_raw="-5"))
    assert document.rates == ()
    assert document.dropped_by_reason == {"invalid_amount": 1}


def test_limits_and_feе_are_parsed_and_validated():
    document = normalize(raw(min_raw="1000", max_raw="500000", fee_raw="0.5"))
    rate = document.rates[0]
    assert (rate.min_amount, rate.max_amount) == (Decimal("1000"), Decimal("500000"))
    assert rate.fee_percent == Decimal("0.5")

    dropped = normalize(raw(min_raw="10", max_raw="1"))
    assert dropped.dropped_by_reason == {"invalid_limits": 1}

    dropped = normalize(raw(fee_raw="150"))
    assert dropped.dropped_by_reason == {"invalid_fee": 1}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("buy", Side.CLIENT_SELLS_BASE),
        ("bid", Side.CLIENT_SELLS_BASE),
        ("sell", Side.CLIENT_BUYS_BASE),
        ("ask", Side.CLIENT_BUYS_BASE),
        ("unknown-stuff", Side.UNKNOWN),
        (None, Side.UNKNOWN),
    ],
)
def test_side_mapping(value, expected):
    assert parse_side(value) is expected


def test_duplicate_direction_keeps_first_row():
    document = normalize(raw(rate_raw="92.35"), raw(rate_raw="93.00"))
    assert len(document.rates) == 1
    assert document.rates[0].rate == Decimal("92.35")
    assert document.dropped_by_reason == {"duplicate_rate": 1}


def test_same_pair_with_different_sides_is_not_a_duplicate():
    document = normalize(raw(rate_raw="92.35", side_raw="buy"), raw(rate_raw="93.00", side_raw="sell"))
    assert len(document.rates) == 2


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-11-17T08:00:00Z", datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)),
        ("2026-11-17T08:00:00+03:00", datetime(2026, 11, 17, 5, 0, tzinfo=timezone.utc)),
        ("2026-11-17 08:00:00", datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)),
        ("17.11.2026 08:00", datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)),
        ("1763366400", datetime(2025, 11, 17, 8, 0, tzinfo=timezone.utc)),
    ],
)
def test_updated_at_formats(text, expected):
    assert parse_updated_at(text) == expected


def test_invalid_updated_at_is_null_with_warning():
    document = normalize(raw(updated_raw="not a date"))
    rate = document.rates[0]
    assert rate.updated_at is None
    assert "invalid_updated_at" in rate.warnings


def test_future_updated_at_is_kept_with_warning():
    document = normalize(raw(updated_raw="2027-01-01T00:00:00Z"))
    rate = document.rates[0]
    assert rate.updated_at is not None
    assert "updated_at_in_future" in rate.warnings


def test_order_of_rates_follows_the_document():
    document = normalize(raw(base_id="BTC", rate_raw="7800000"), raw(base_id="USDT"))
    assert [rate.base for rate in document.rates] == ["BTC", "USDT"]


def test_rate_to_dict_keeps_precision_as_string():
    document = normalize(raw(rate_raw="92.3500"))
    payload = document.rates[0].to_dict()
    assert payload["rate"] == "92.35"
    assert payload["direction"] == "USDT/RUB"
    assert payload["raw"]["format"] == "own_schema"
