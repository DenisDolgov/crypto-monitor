"""Field synonyms shared by all parsers (MYSP-2 §3).

One table, one place to fix: XML, JSON and CSV feeds use the same vocabulary,
so duplicating the lists per parser would let them drift apart.
"""

from __future__ import annotations

BASE_FIELDS = (
    "from",
    "base",
    "currency_from",
    "currencyfrom",
    "in_currency",
    "source_currency",
    "give_currency",
    "source",
)

QUOTE_FIELDS = (
    "to",
    "quote",
    "currency_to",
    "currencyto",
    "out_currency",
    "target_currency",
    "get_currency",
    "target",
)

RATE_FIELDS = ("rate", "price", "value", "ratio", "course")

IN_FIELDS = ("in", "give", "amount_from")
OUT_FIELDS = ("out", "get", "amount_to")

SIDE_FIELDS = ("side", "operation", "type", "deal")

RESERVE_FIELDS = ("reserve", "amount", "stock", "balance")
RESERVE_CURRENCY_FIELDS = ("reserve_currency", "amount_currency", "stock_currency")

MIN_FIELDS = ("minamount", "min", "limit_min", "min_amount", "minamountfrom")
MAX_FIELDS = ("maxamount", "max", "limit_max", "max_amount")

FEE_FIELDS = ("fee", "fee_percent", "commission", "tofee")

UPDATED_FIELDS = ("updated", "updated_at", "last_update", "lastupdate", "date", "timestamp")


def pick(values: dict[str, str], fields: tuple[str, ...]) -> str | None:
    """Return the first non-empty value among ``fields`` (keys already lowercased)."""

    for field in fields:
        value = values.get(field)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None
