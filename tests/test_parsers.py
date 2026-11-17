"""Feed format detection and parsing (AC-14, AC-16, AC-17)."""

from __future__ import annotations

import pytest

from feedscan.formats import detect
from feedscan.models import Format, Orientation, ReserveCurrency, Side
from feedscan.parsers import parse_document

DOMAIN_URL = "https://exchanger-a.test/rates.xml"


def test_detects_json(read_fixture):
    fmt, evidence = detect(read_fixture("feed.json"))
    assert fmt is Format.JSON
    assert "JSON" in evidence


def test_detects_bestchange_compatible_xml(read_fixture):
    fmt, evidence = detect(read_fixture("feed_bestchange.xml"))
    assert fmt is Format.BESTCHANGE_COMPATIBLE
    assert "item" in evidence


def test_detects_own_xml_schema(read_fixture):
    fmt, evidence = detect(read_fixture("feed_own.xml"))
    assert fmt is Format.OWN_SCHEMA
    assert "rate" in evidence


def test_detects_csv(read_fixture):
    fmt, evidence = detect(read_fixture("feed.csv"))
    assert fmt is Format.CSV
    assert "base" in evidence


def test_unknown_payload_is_unknown(read_fixture):
    fmt, _ = detect(read_fixture("homepage_plain.html"))
    assert fmt is Format.UNKNOWN


def test_parses_own_xml(read_fixture):
    document = parse_document(read_fixture("feed_own.xml"), DOMAIN_URL)
    assert document.format is Format.OWN_SCHEMA
    assert document.error is None
    assert len(document.raws) == 3
    first = document.raws[0]
    assert (first.base_id, first.quote_id, first.rate_raw) == ("USDT", "RUB", "92.35")
    assert first.reserve_raw == "150000.00"
    assert first.min_raw == "1000"
    assert first.max_raw == "500000"
    assert first.fee_raw == "0.5"
    assert first.updated_raw == "2026-11-17T07:55:00Z"


def test_parses_bestchange_xml(read_fixture):
    document = parse_document(read_fixture("feed_bestchange.xml"), DOMAIN_URL)
    assert document.format is Format.BESTCHANGE_COMPATIBLE
    assert document.error is None
    assert len(document.raws) == 2
    first = document.raws[0]
    assert first.base_id == "BTC"
    assert first.quote_id == "SberbankRUB"
    assert (first.in_raw, first.out_raw) == ("1", "7750000")
    assert first.orientation is Orientation.QUOTE_PER_BASE
    assert first.reserve_currency is ReserveCurrency.QUOTE
    assert first.min_raw == "0.001"
    assert first.max_raw == "5"


def test_parses_json(read_fixture):
    document = parse_document(read_fixture("feed.json"), DOMAIN_URL)
    assert document.format is Format.JSON
    assert document.error is None
    assert len(document.raws) == 2
    assert document.raws[0].base_id == "USDT"
    assert document.raws[0].rate_raw == "92.3500"
    assert document.raws[1].updated_raw == "2026-11-17T07:44:00Z"


def test_parses_csv_with_semicolon_and_decimal_comma(read_fixture):
    document = parse_document(read_fixture("feed.csv"), DOMAIN_URL)
    assert document.format is Format.CSV
    assert document.error is None
    assert len(document.raws) == 2
    assert document.raws[0].base_id == "USDT"
    assert document.raws[0].rate_raw == "92,35"
    assert document.raws[0].min_raw == "1000"


def test_broken_xml_is_reported(read_fixture):
    document = parse_document(read_fixture("feed_broken.xml"), DOMAIN_URL)
    assert document.error == "invalid_xml"
    assert document.raws == ()
    assert document.format is Format.UNKNOWN


def test_wellformed_xml_without_rates_is_error_and_unknown_format(read_fixture):
    document = parse_document(read_fixture("feed_no_rates.xml"), DOMAIN_URL)
    assert document.error == "no_rates_in_feed"
    assert document.format is Format.UNKNOWN
    assert document.raws == ()


def test_broken_json_is_reported():
    document = parse_document('{"rates": [{"base": "USDT",', DOMAIN_URL)
    assert document.error == "invalid_json"


def test_json_without_rates_is_error():
    document = parse_document('{"meta": {"title": "no rates here"}}', DOMAIN_URL)
    assert document.error == "no_rates_in_feed"
    assert document.format is Format.UNKNOWN


def test_csv_without_recognisable_header_is_error():
    document = parse_document("foo,bar\n1,2\n", DOMAIN_URL)
    assert document.error == "no_rates_in_feed"
    assert document.format is Format.UNKNOWN


def test_json_items_use_shared_synonyms():
    text = (
        '{"data": [{"currency_from": "BTC", "currency_to": "RUB", '
        '"price": "7800000", "stock": "2", "limit_min": "0.001", "limit_max": "3"}]}'
    )
    document = parse_document(text, DOMAIN_URL)
    assert document.format is Format.JSON
    raw = document.raws[0]
    assert (raw.base_id, raw.quote_id, raw.rate_raw) == ("BTC", "RUB", "7800000")
    assert raw.reserve_raw == "2"
    assert (raw.min_raw, raw.max_raw) == ("0.001", "3")


def test_in_out_without_rate_is_supported():
    text = "<rates><item><from>USDT</from><to>RUB</to><in>100</in><out>9200</out></item></rates>"
    document = parse_document(text, DOMAIN_URL)
    raw = document.raws[0]
    assert raw.rate_raw is None
    assert (raw.in_raw, raw.out_raw) == ("100", "9200")


def test_json_inverted_orientation_is_flagged():
    text = '[{"from": "RUB", "to": "USDT", "rate": "0.0108", "orientation": "base_per_quote"}]'
    document = parse_document(text, DOMAIN_URL)
    assert document.raws[0].orientation is Orientation.BASE_PER_QUOTE


def test_side_raw_is_taken_from_feed():
    text = (
        '<rates><rate><from>USDT</from><to>RUB</to><rate>92</rate>'
        '<side>sell</side></rate></rates>'
    )
    document = parse_document(text, DOMAIN_URL)
    assert document.raws[0].side_raw == "sell"


def test_text_without_any_structure_is_no_rates():
    document = parse_document("просто текст без фида", DOMAIN_URL)
    assert document.error == "no_rates_in_feed"
    assert document.format is Format.UNKNOWN


def test_parse_document_records_format_evidence(read_fixture):
    document = parse_document(read_fixture("feed_bestchange.xml"), DOMAIN_URL)
    assert document.evidence
    assert len(document.evidence) <= 200
