"""JSON feeds: a list of rate objects, possibly nested under a known key."""

from __future__ import annotations

import json

from ..models import Orientation, RawRate
from . import synonyms

_LIST_KEYS = ("rates", "data", "items", "result", "results", "pairs", "currencies")


class JsonStructureError(ValueError):
    pass


def parse(text: str, source_url: str) -> list[RawRate]:
    payload = json.loads(text)
    records = _find_records(payload)
    rates: list[RawRate] = []
    for index, record in enumerate(records):
        values = {str(key).lower(): value for key, value in record.items()}
        rates.append(_build_raw(values, source_url, f"$.rates[{index}]"))
    return rates


def _find_records(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in _LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                records = [item for item in value if isinstance(item, dict)]
                if records:
                    return records
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found = _find_records(value)
                if found:
                    return found
    return []


def _build_raw(values: dict, source_url: str, path: str) -> RawRate:
    rate_raw = _as_text(synonyms.pick(values, synonyms.RATE_FIELDS))
    orientation = Orientation.QUOTE_PER_BASE
    hint = (_as_text(values.get("orientation")) or "").lower()
    if hint in ("base_per_quote", "reversed", "reverse"):
        orientation = Orientation.BASE_PER_QUOTE

    return RawRate(
        source_url=source_url,
        base_id=_as_text(synonyms.pick(values, synonyms.BASE_FIELDS)),
        quote_id=_as_text(synonyms.pick(values, synonyms.QUOTE_FIELDS)),
        rate_raw=rate_raw,
        in_raw=_as_text(synonyms.pick(values, synonyms.IN_FIELDS)),
        out_raw=_as_text(synonyms.pick(values, synonyms.OUT_FIELDS)),
        orientation=orientation,
        side_raw=_as_text(synonyms.pick(values, synonyms.SIDE_FIELDS)),
        reserve_raw=_as_text(synonyms.pick(values, synonyms.RESERVE_FIELDS)),
        reserve_currency_id=_as_text(synonyms.pick(values, synonyms.RESERVE_CURRENCY_FIELDS)),
        min_raw=_as_text(synonyms.pick(values, synonyms.MIN_FIELDS)),
        max_raw=_as_text(synonyms.pick(values, synonyms.MAX_FIELDS)),
        fee_raw=_as_text(synonyms.pick(values, synonyms.FEE_FIELDS)),
        updated_raw=_as_text(synonyms.pick(values, synonyms.UPDATED_FIELDS)),
        path=path,
    )


def _as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        # Never let float repr creep into the Decimal path.
        return format(value, ".15g")
    text = str(value).strip()
    return text or None
