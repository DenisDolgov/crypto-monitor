"""BestChange-compatible XML: ``<rates><item>`` with from/to/in/out.

No namespace and text currency identifiers (``USDTTRC``, ``SberbankRUB``) are
the markers; the currency registry decides what those identifiers mean.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..models import Orientation, RawRate, ReserveCurrency

ITEM_TAGS = ("item", "rate")


def looks_like_bestchange(root: ET.Element) -> bool:
    if _local(root.tag) != "rates":
        return False
    if _has_namespace(root):
        return False
    for child in root:
        if _local(child.tag).lower() not in ITEM_TAGS:
            continue
        fields = {_local(grand.tag).lower() for grand in child}
        # MYSP-2 §3: BestChange-compatible items carry from, to, in and out.
        if {"from", "to", "in", "out"} <= fields:
            return True
    return False


def parse(root: ET.Element, source_url: str) -> list[RawRate]:
    rates: list[RawRate] = []
    for index, child in enumerate(root):
        if _local(child.tag).lower() not in ITEM_TAGS:
            continue
        values = {_local(grand.tag).lower(): (grand.text or "") for grand in child}
        rates.append(
            RawRate(
                source_url=source_url,
                base_id=_clean(values.get("from")),
                quote_id=_clean(values.get("to")),
                rate_raw=_clean(values.get("rate")),
                in_raw=_clean(values.get("in")),
                out_raw=_clean(values.get("out")),
                orientation=Orientation.QUOTE_PER_BASE,
                side_raw=_clean(values.get("side") or values.get("type")),
                reserve_raw=_clean(values.get("amount") or values.get("reserve")),
                reserve_currency=ReserveCurrency.QUOTE,
                reserve_currency_id=None,
                min_raw=_clean(values.get("minamount") or values.get("min")),
                max_raw=_clean(values.get("maxamount") or values.get("max")),
                # ``tofee`` is an absolute commission in the receive currency,
                # not a percentage: mapping it to ``fee_percent`` would lie.
                fee_raw=_clean(values.get("fee")),
                updated_raw=_clean(values.get("updated") or values.get("last_update")),
                path=f"/rates/item[{index + 1}]",
            )
        )
    return rates


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _has_namespace(element: ET.Element) -> bool:
    return any(isinstance(child.tag, str) and child.tag.startswith("{") for child in element)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
