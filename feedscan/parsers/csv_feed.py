"""CSV feeds: a header row that speaks the synonym vocabulary."""

from __future__ import annotations

import csv
import io

from ..models import Orientation, RawRate
from . import synonyms

_KNOWN = set(
    synonyms.BASE_FIELDS
    + synonyms.QUOTE_FIELDS
    + synonyms.RATE_FIELDS
    + synonyms.IN_FIELDS
    + synonyms.OUT_FIELDS
    + synonyms.RESERVE_FIELDS
    + synonyms.MIN_FIELDS
    + synonyms.MAX_FIELDS
    + synonyms.FEE_FIELDS
    + synonyms.SIDE_FIELDS
    + synonyms.UPDATED_FIELDS
)


def sniff_header(text: str) -> list[str] | None:
    """Return lowercased header fields if the first line looks like a rate header."""

    first_line = next((line for line in (text or "").splitlines() if line.strip()), "")
    if not first_line:
        return None
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    header = [field.strip().strip('"').lower() for field in first_line.split(delimiter)]
    known = [field for field in header if field in _KNOWN]
    has_base = any(field in synonyms.BASE_FIELDS for field in header)
    has_quote = any(field in synonyms.QUOTE_FIELDS for field in header)
    if len(known) >= 2 and has_base and has_quote:
        return header
    return None


def parse(text: str, source_url: str) -> list[RawRate]:
    first_line = next((line for line in (text or "").splitlines() if line.strip()), "")
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(reader, None)
    if not header:
        return []
    fields = [field.strip().strip('"').lower() for field in header]

    rates: list[RawRate] = []
    for index, row in enumerate(reader):
        if not any(cell.strip() for cell in row):
            continue
        values = {
            field: (row[position] if position < len(row) else "")
            for position, field in enumerate(fields)
            if field
        }
        rates.append(_build_raw(values, source_url, f"row[{index + 1}]"))
    return rates


def _build_raw(values: dict[str, str], source_url: str, path: str) -> RawRate:
    return RawRate(
        source_url=source_url,
        base_id=synonyms.pick(values, synonyms.BASE_FIELDS),
        quote_id=synonyms.pick(values, synonyms.QUOTE_FIELDS),
        rate_raw=synonyms.pick(values, synonyms.RATE_FIELDS),
        in_raw=synonyms.pick(values, synonyms.IN_FIELDS),
        out_raw=synonyms.pick(values, synonyms.OUT_FIELDS),
        orientation=Orientation.QUOTE_PER_BASE,
        side_raw=synonyms.pick(values, synonyms.SIDE_FIELDS),
        reserve_raw=synonyms.pick(values, synonyms.RESERVE_FIELDS),
        reserve_currency_id=synonyms.pick(values, synonyms.RESERVE_CURRENCY_FIELDS),
        min_raw=synonyms.pick(values, synonyms.MIN_FIELDS),
        max_raw=synonyms.pick(values, synonyms.MAX_FIELDS),
        fee_raw=synonyms.pick(values, synonyms.FEE_FIELDS),
        updated_raw=synonyms.pick(values, synonyms.UPDATED_FIELDS),
        path=path,
    )
