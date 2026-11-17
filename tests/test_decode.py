"""Decoding rules: BOM, charset chain, misdeclared encodings (AC-21)."""

from __future__ import annotations

import codecs

from feedscan.decode import decode_body, normalize_charset


def test_plain_utf8_is_decoded(read_fixture):
    decoded = decode_body(read_fixture("feed_own.xml").encode("utf-8"), "utf-8")
    assert decoded.error is None
    assert decoded.encoding == "utf-8"
    assert "<from>USDT</from>" in decoded.text


def test_utf8_bom_is_stripped_and_not_leaked_into_body(read_fixture_bytes):
    raw = read_fixture_bytes("bom.csv")
    assert raw.startswith(codecs.BOM_UTF8)
    decoded = decode_body(raw, None)
    assert decoded.encoding == "utf-8"
    assert decoded.text.startswith("name,domain,url")


def test_cp1251_declared_and_cp1251_body_is_decoded_as_cp1251(read_fixture_bytes):
    decoded = decode_body(read_fixture_bytes("feed_cp1251.xml"), "windows-1251")
    assert decoded.encoding == "windows-1251"
    assert decoded.warnings == ()
    assert "Курс обмена" in decoded.text


def test_declared_cp1251_but_body_is_utf8_wins_utf8_with_warning(read_fixture_bytes):
    # Same Cyrillic text, but the bytes are UTF-8 while the server claims
    # windows-1251: decoding with cp1251 would produce mojibake.
    raw = read_fixture_bytes("feed_cp1251.xml").decode("windows-1251").encode("utf-8")
    decoded = decode_body(raw, "windows-1251")
    assert decoded.encoding == "utf-8"
    assert "encoding_mismatch" in decoded.warnings
    assert "Курс обмена" in decoded.text


def test_cp1251_body_declared_as_utf8_falls_back_with_warning(read_fixture_bytes):
    decoded = decode_body(read_fixture_bytes("feed_cp1251_mislabelled.xml"), "utf-8")
    assert decoded.error is None
    assert decoded.encoding == "windows-1251"
    assert "encoding_mismatch" in decoded.warnings


def test_utf16_bom_is_unsupported(read_fixture_bytes):
    decoded = decode_body(read_fixture_bytes("feed_utf16.xml"), None)
    assert decoded.text is None
    assert decoded.error == "unsupported_encoding"


def test_undecodable_bytes_are_reported():
    # 0x98 is undefined both in UTF-8 and in windows-1251, so the whole chain
    # fails and the caller gets an explicit error instead of mojibake.
    decoded = decode_body(b"\x98\x98\x98", "utf-8")
    assert decoded.text is None
    assert decoded.error == "undecodable"


def test_charset_aliases_are_normalised():
    assert normalize_charset("CP1251") == "windows-1251"
    assert normalize_charset(" utf8 ") == "utf-8"
    assert normalize_charset('"UTF-8"; foo=bar') == "utf-8"
    assert normalize_charset(None) is None


def test_unknown_declared_charset_is_skipped_without_crash(read_fixture):
    decoded = decode_body(read_fixture("feed_own.xml").encode("utf-8"), "x-unknown-charset")
    assert decoded.error is None
    assert decoded.encoding == "utf-8"
    assert "encoding_mismatch" in decoded.warnings
