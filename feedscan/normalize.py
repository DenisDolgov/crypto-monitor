"""Normalisation: raw feed rows to ``Rate`` records (ADR-0001).

Every numeric rule lives here: parsing numbers, ranges, inversion, reserve
conversion, dates, side mapping and de-duplication. Parsers stay dumb, the
registry stays data, and a rejected row is counted by reason instead of
disappearing.
"""

from __future__ import annotations

import decimal
import re
from datetime import datetime, timezone
from decimal import Decimal

from .currencies import CurrencyRegistry
from .models import (
    Drop,
    NormalizedDocument,
    Orientation,
    Rate,
    RateDerived,
    RawData,
    RawRate,
    ReserveCurrency,
    Side,
)

DECIMAL_CONTEXT = decimal.Context(prec=28)

_NUMBER_RE = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")
_GROUPED_COMMA_RE = re.compile(r"^\d{1,3}(,\d{3})+$")
_GROUPED_DOT_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")
_GROUP_CHARS = (" ", "\u00a0", "\u202f", "\u2009", "\u2019", "'", "_")
_EMPTY_VALUES = ("", "-", "—", "n/a", "na", "none", "null")
_SIDE_MAP = {
    "buy": Side.CLIENT_SELLS_BASE,
    "bid": Side.CLIENT_SELLS_BASE,
    "sell": Side.CLIENT_BUYS_BASE,
    "ask": Side.CLIENT_BUYS_BASE,
}


def parse_decimal(value, *, with_reason: bool = False):
    """Parse a published number into ``Decimal`` following ADR-0001."""

    result = _parse_decimal(value)
    if with_reason:
        return result
    return result[0]


def _parse_decimal(value) -> tuple[Decimal | None, str | None]:
    if value is None:
        return None, "non_numeric_value"
    text = str(value).strip()
    if not text:
        return None, "non_numeric_value"

    cleaned = _strip_group_separators(text)

    commas = cleaned.count(",")
    dots = cleaned.count(".")
    if commas and dots:
        return None, "ambiguous_number"
    if commas == 1:
        cleaned = cleaned.replace(",", ".")
    elif commas > 1:
        if _GROUPED_COMMA_RE.match(cleaned):
            cleaned = cleaned.replace(",", "")
        else:
            return None, "ambiguous_number"
    elif dots > 1:
        if _GROUPED_DOT_RE.match(cleaned):
            cleaned = cleaned.replace(".", "")
        else:
            return None, "ambiguous_number"

    if not _NUMBER_RE.match(cleaned):
        return None, "non_numeric_value"

    try:
        number = Decimal(cleaned)
    except decimal.InvalidOperation:
        return None, "non_numeric_value"
    if not number.is_finite():
        return None, "non_numeric_value"
    return number, None


def _strip_group_separators(text: str) -> str:
    out: list[str] = []
    for index, char in enumerate(text):
        if char in _GROUP_CHARS:
            previous = text[index - 1] if index > 0 else ""
            following = text[index + 1] if index + 1 < len(text) else ""
            if previous.isdigit() and following.isdigit():
                continue
        out.append(char)
    return "".join(out).strip()


def parse_amount(value) -> Decimal | None:
    """Optional amounts: absent, dash and n/a are ``None``, never zero."""

    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _EMPTY_VALUES:
        return None
    number, _ = _parse_decimal(text)
    return number


def parse_side(value: str | None) -> Side:
    if not value:
        return Side.UNKNOWN
    return _SIDE_MAP.get(str(value).strip().lower(), Side.UNKNOWN)


def parse_updated_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        parsed = None
    if parsed is None:
        for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_document(
    raws,
    registry: CurrencyRegistry,
    *,
    exchanger_domain: str,
    fetched_at: datetime,
    raw: RawData,
) -> NormalizedDocument:
    rates: list[Rate] = []
    drops: list[Drop] = []
    seen: set[tuple[str, str, str]] = set()

    for item in raws:
        rate, drop = _normalize_one(
            item,
            registry,
            exchanger_domain=exchanger_domain,
            fetched_at=fetched_at,
            raw=raw,
        )
        if drop is not None:
            drops.append(drop)
            continue
        assert rate is not None
        key = (rate.base, rate.quote, rate.side.value)
        if key in seen:
            drops.append(Drop(reason="duplicate_rate", detail=f"{rate.base}/{rate.quote} {rate.side.value}"))
            continue
        seen.add(key)
        rates.append(rate)

    return NormalizedDocument(rates=tuple(rates), drops=tuple(drops))


def _normalize_one(
    item: RawRate,
    registry: CurrencyRegistry,
    *,
    exchanger_domain: str,
    fetched_at: datetime,
    raw: RawData,
) -> tuple[Rate | None, Drop | None]:
    base = registry.resolve(item.base_id)
    if base is None:
        return None, Drop(
            reason=f"unmapped_currency:{item.base_id}",
            detail=f"base={item.base_id!r}",
            raw_currency_id=item.base_id,
        )
    quote = registry.resolve(item.quote_id)
    if quote is None:
        return None, Drop(
            reason=f"unmapped_currency:{item.quote_id}",
            detail=f"quote={item.quote_id!r}",
            raw_currency_id=item.quote_id,
        )
    if base == quote:
        return None, Drop(reason="invalid_direction", detail=f"{base}/{quote}")

    published, failure = _published_rate(item)
    if failure is not None:
        return None, Drop(reason=failure, detail=f"{base}/{quote}")

    if published is None or published <= 0:
        return None, Drop(reason="invalid_rate", detail=f"{base}/{quote}")

    if item.orientation is Orientation.BASE_PER_QUOTE:
        rate_value = DECIMAL_CONTEXT.divide(Decimal(1), published)
        derived = RateDerived.INVERTED
    else:
        rate_value = published
        derived = RateDerived.DIRECT
    if rate_value <= 0:
        return None, Drop(reason="invalid_rate", detail=f"{base}/{quote}")

    reserve, reserve_derived, reserve_warning, reserve_drop = _resolve_reserve(
        item, registry, base=base, quote=quote, rate=rate_value
    )
    if reserve_drop is not None:
        return None, Drop(reason=reserve_drop, detail=f"{base}/{quote} reserve")

    min_amount = parse_amount(item.min_raw)
    max_amount = parse_amount(item.max_raw)
    for amount in (min_amount, max_amount):
        if amount is not None and amount < 0:
            return None, Drop(reason="invalid_amount", detail=f"{base}/{quote}")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        return None, Drop(reason="invalid_limits", detail=f"{base}/{quote}")

    fee = parse_amount(item.fee_raw)
    if fee is not None and (fee < -100 or fee > 100):
        return None, Drop(reason="invalid_fee", detail=f"{base}/{quote}")

    warnings: list[str] = []
    if reserve_warning:
        warnings.append(reserve_warning)

    # An optional amount that is present but not numeric is not a reason to
    # drop the rate (ADR-0001), but it must not look like "not published"
    # either: real BestChange feeds write "150 USD" into min/max.
    for field_name, raw_value, parsed_value in (
        ("min", item.min_raw, min_amount),
        ("max", item.max_raw, max_amount),
        ("fee", item.fee_raw, fee),
        ("reserve", item.reserve_raw, reserve),
    ):
        if raw_value is None or str(raw_value).strip() == "":
            continue
        if parsed_value is None and f"invalid_amount:{field_name}" not in warnings:
            if field_name == "reserve" and reserve_warning:
                continue
            warnings.append(f"invalid_amount:{field_name}")

    updated_at = None
    if item.updated_raw is not None and str(item.updated_raw).strip():
        updated_at = parse_updated_at(item.updated_raw)
        if updated_at is None:
            warnings.append("invalid_updated_at")
    if updated_at is not None and updated_at > fetched_at:
        warnings.append("updated_at_in_future")

    return (
        Rate(
            exchanger_domain=exchanger_domain,
            base=base,
            quote=quote,
            rate=rate_value,
            side=parse_side(item.side_raw),
            reserve=reserve,
            reserve_derived=reserve_derived,
            min_amount=min_amount,
            max_amount=max_amount,
            fee_percent=fee,
            updated_at=updated_at,
            fetched_at=fetched_at,
            rate_derived=derived,
            source_url=item.source_url,
            raw=raw,
            warnings=tuple(warnings),
        ),
        None,
    )


def _published_rate(item: RawRate) -> tuple[Decimal | None, str | None]:
    if item.rate_raw not in (None, ""):
        value, reason = _parse_decimal(item.rate_raw)
        if value is None:
            return None, reason or "non_numeric_value"
        return value, None

    if item.in_raw not in (None, "") and item.out_raw not in (None, ""):
        given, given_reason = _parse_decimal(item.in_raw)
        received, received_reason = _parse_decimal(item.out_raw)
        if given is None:
            return None, given_reason or "non_numeric_value"
        if received is None:
            return None, received_reason or "non_numeric_value"
        if given == 0:
            return None, "invalid_rate"
        return DECIMAL_CONTEXT.divide(received, given), None

    return None, "missing_required_field"


def _resolve_reserve(
    item: RawRate,
    registry: CurrencyRegistry,
    *,
    base: str,
    quote: str,
    rate: Decimal,
) -> tuple[Decimal | None, bool, str | None, str | None]:
    if item.reserve_raw is None or str(item.reserve_raw).strip() == "":
        return None, False, None, None

    reserve = parse_amount(item.reserve_raw)
    if reserve is None:
        return None, False, None, None
    if reserve < 0:
        return None, False, None, "invalid_amount"

    currency = item.reserve_currency
    if item.reserve_currency_id:
        resolved = registry.resolve(item.reserve_currency_id)
        if resolved == base:
            currency = ReserveCurrency.BASE
        elif resolved == quote:
            currency = ReserveCurrency.QUOTE
        else:
            return None, False, "reserve_currency_mismatch", None

    if currency is ReserveCurrency.BASE:
        return DECIMAL_CONTEXT.multiply(reserve, rate), True, None, None
    return reserve, False, None, None
