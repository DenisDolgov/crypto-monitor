"""Own XML schemas: no namespace, no BestChange markers.

Records are found by shape, not by tag name: any element whose children carry
a base, a quote and either a rate or an in/out pair is a rate row. Tags are
looked up through the shared synonym table.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..models import Orientation, RawRate, ReserveCurrency
from . import synonyms

BASE_MARKERS = set(synonyms.BASE_FIELDS)
QUOTE_MARKERS = set(synonyms.QUOTE_FIELDS)
RATE_MARKERS = set(synonyms.RATE_FIELDS)
IN_MARKERS = set(synonyms.IN_FIELDS)
OUT_MARKERS = set(synonyms.OUT_FIELDS)


def looks_like_own_schema(root: ET.Element) -> bool:
    return _first_record(root) is not None


def parse(root: ET.Element, source_url: str) -> list[RawRate]:
    rates: list[RawRate] = []
    for element, path in _iter_records(root):
        values = {(child.tag.split("}")[-1].lower()): (child.text or "") for child in element}
        rates.append(_build_raw(values, source_url, path))
    return rates


def _iter_records(root: ET.Element):
    for element in root.iter():
        values = {child.tag.split("}")[-1].lower() for child in element}
        if not values:
            continue
        if values & BASE_MARKERS and values & QUOTE_MARKERS and (
            values & RATE_MARKERS or (values & IN_MARKERS and values & OUT_MARKERS)
        ):
            yield element, _path_of(root, element)


def _first_record(root: ET.Element) -> ET.Element | None:
    for element, _ in _iter_records(root):
        return element
    return None


def _build_raw(values: dict[str, str], source_url: str, path: str) -> RawRate:
    has_rate = synonyms.pick(values, synonyms.RATE_FIELDS) is not None
    orientation = Orientation.QUOTE_PER_BASE
    orientation_hint = (values.get("orientation") or "").strip().lower()
    if orientation_hint in ("base_per_quote", "reversed", "reverse"):
        orientation = Orientation.BASE_PER_QUOTE

    reserve_currency = ReserveCurrency.UNKNOWN
    reserve_currency_id = synonyms.pick(values, synonyms.RESERVE_CURRENCY_FIELDS)

    return RawRate(
        source_url=source_url,
        base_id=synonyms.pick(values, synonyms.BASE_FIELDS),
        quote_id=synonyms.pick(values, synonyms.QUOTE_FIELDS),
        rate_raw=synonyms.pick(values, synonyms.RATE_FIELDS) if has_rate else None,
        in_raw=synonyms.pick(values, synonyms.IN_FIELDS),
        out_raw=synonyms.pick(values, synonyms.OUT_FIELDS),
        orientation=orientation,
        side_raw=synonyms.pick(values, synonyms.SIDE_FIELDS),
        reserve_raw=synonyms.pick(values, synonyms.RESERVE_FIELDS),
        reserve_currency=reserve_currency,
        reserve_currency_id=reserve_currency_id,
        min_raw=synonyms.pick(values, synonyms.MIN_FIELDS),
        max_raw=synonyms.pick(values, synonyms.MAX_FIELDS),
        fee_raw=synonyms.pick(values, synonyms.FEE_FIELDS),
        updated_raw=synonyms.pick(values, synonyms.UPDATED_FIELDS),
        path=path,
    )


def _path_of(root: ET.Element, target: ET.Element) -> str:
    """Short path like ``/rates/rate[1]`` for ``format_evidence``."""

    parent_map = {child: parent for parent in root.iter() for child in parent}
    parts: list[str] = []
    current: ET.Element | None = target
    while current is not None:
        tag = current.tag.split("}")[-1]
        parent = parent_map.get(current)
        if parent is not None:
            same_tag = [child for child in parent if child.tag == current.tag]
            if len(same_tag) > 1:
                tag = f"{tag}[{same_tag.index(current) + 1}]"
        parts.append(tag)
        current = parent
    return "/" + "/".join(reversed(parts))
