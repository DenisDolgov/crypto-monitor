"""Parsing a document: format detection, parse, and the reasons for failure.

The parsers emit raw rows; any decision about numbers or currencies belongs to
``normalize``. A structurally unsuitable document (wrong root, broken XML, a
JSON object without rate records) never raises to the caller: it becomes a
``ParsedDocument`` with an error code from the closed list in ADR-0003.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable

from ..models import Format, RawRate
from . import csv_feed, json_feed, xml_bestchange, xml_own


@dataclass(frozen=True)
class ParsedDocument:
    raw_str: str = field(repr=False, default="")
    format: Format = Format.UNKNOWN
    evidence: str = ""
    raws: tuple[RawRate, ...] = ()
    error: str | None = None
    error_detail: str | None = None


def parse_document(text: str, source_url: str) -> ParsedDocument:
    stripped = (text or "").lstrip("\ufeff \t\r\n")
    if not stripped:
        return ParsedDocument(format=Format.UNKNOWN, error="no_rates_in_feed", error_detail="пустой документ")

    if stripped[0] in "{[":
        return _parse_json(stripped, text, source_url)
    if stripped[0] == "<" or stripped.startswith("<?xml"):
        return _parse_xml(stripped, source_url)
    return _parse_csv(text, source_url)


def _parse_json(stripped: str, text: str, source_url: str) -> ParsedDocument:
    try:
        json.loads(stripped)
    except json.JSONDecodeError as exc:
        return ParsedDocument(
            format=Format.UNKNOWN,
            error="invalid_json",
            error_detail=f"JSON error at line {exc.lineno} column {exc.colno}: {exc.msg}",
        )
    try:
        raws = json_feed.parse(stripped, source_url)
    except json_feed.JsonStructureError as exc:
        return ParsedDocument(format=Format.UNKNOWN, error="invalid_feed_structure", error_detail=str(exc))
    if not raws:
        return ParsedDocument(
            format=Format.UNKNOWN,
            error="no_rates_in_feed",
            error_detail="JSON прочитан, но записей с курсами не найдено",
        )
    return ParsedDocument(
        format=Format.JSON,
        evidence=_evidence(raws),
        raws=tuple(raws),
    )


def _parse_xml(stripped: str, source_url: str) -> ParsedDocument:
    try:
        root = ET.fromstring(stripped)
    except ET.ParseError as exc:
        return ParsedDocument(
            format=Format.UNKNOWN,
            error="invalid_xml",
            error_detail=f"XML parse error: {exc}",
        )

    if xml_bestchange.looks_like_bestchange(root):
        raws = xml_bestchange.parse(root, source_url)
        if raws:
            return ParsedDocument(
                format=Format.BESTCHANGE_COMPATIBLE,
                evidence="root <rates> without namespace, items with from/to/in/out",
                raws=tuple(raws),
            )

    if xml_own.looks_like_own_schema(root):
        try:
            raws = xml_own.parse(root, source_url)
        except ET.ParseError as exc:  # pragma: no cover - root already parsed
            return ParsedDocument(
                format=Format.UNKNOWN,
                error="invalid_feed_structure",
                error_detail=f"XML structure error: {exc}",
            )
        if raws:
            return ParsedDocument(
                format=Format.OWN_SCHEMA,
                evidence=_evidence(raws),
                raws=tuple(raws),
            )

    return ParsedDocument(
        format=Format.UNKNOWN,
        error="no_rates_in_feed",
        error_detail="XML well-formed, но извлекаемых курсов нет",
    )


def _parse_csv(text: str, source_url: str) -> ParsedDocument:
    header = csv_feed.sniff_header(text)
    if header is None:
        return ParsedDocument(
            format=Format.UNKNOWN,
            error="no_rates_in_feed",
            error_detail="заголовок CSV не распознан как описание курсов",
        )
    try:
        raws = csv_feed.parse(text, source_url)
    except ValueError as exc:  # pragma: no cover - csv is forgiving
        return ParsedDocument(format=Format.UNKNOWN, error="invalid_csv", error_detail=str(exc))
    if not raws:
        return ParsedDocument(
            format=Format.UNKNOWN,
            error="no_rates_in_feed",
            error_detail="CSV прочитан, но строк с курсами нет",
        )
    return ParsedDocument(
        format=Format.CSV,
        evidence="header: " + ", ".join(header[:8]),
        raws=tuple(raws),
    )


def _evidence(raws: list[RawRate]) -> str:
    first = raws[0].path if raws else ""
    return f"first record at {first}"[:200]
