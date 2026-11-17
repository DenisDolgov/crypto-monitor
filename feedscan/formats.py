"""Format detection by content (MYSP-2 §3).

Detection is deliberately cheap and side-effect free: it answers "what is
this?" for reporting, while ``parsers.parse_document`` does the real work and
can still report ``invalid_xml`` / ``no_rates_in_feed``.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from .models import Format
from .parsers import csv_feed, xml_bestchange, xml_own


def detect(text: str) -> tuple[Format, str]:
    stripped = (text or "").lstrip("\ufeff \t\r\n")
    if not stripped:
        return Format.UNKNOWN, "empty document"

    if stripped[0] in "{[":
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return Format.UNKNOWN, "invalid JSON"
        if _has_records(payload):
            return Format.JSON, "JSON document with rate records"
        return Format.UNKNOWN, "JSON without rate records"

    if stripped[0] == "<" or stripped.startswith("<?xml"):
        try:
            root = ET.fromstring(stripped)
        except ET.ParseError as exc:
            return Format.UNKNOWN, f"invalid XML: {exc}"
        if xml_bestchange.looks_like_bestchange(root):
            return Format.BESTCHANGE_COMPATIBLE, "root <rates>, items with from/to/in/out"
        record = _first_own_record(root)
        if record is not None:
            return Format.OWN_SCHEMA, f"record at {record}"
        return Format.UNKNOWN, "XML without rate records"

    header = csv_feed.sniff_header(stripped)
    if header is not None:
        return Format.CSV, "header: " + ", ".join(header[:8])
    return Format.UNKNOWN, "unknown payload"


def _first_own_record(root: ET.Element) -> str | None:
    from .parsers.xml_own import _iter_records

    for _, path in _iter_records(root):
        return path
    return None


def _has_records(payload) -> bool:
    from .parsers.json_feed import _find_records

    return bool(_find_records(payload))
