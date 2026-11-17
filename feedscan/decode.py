"""Bytes to text: BOM handling, gzip is handled by the HTTP client.

The decoding chain is fixed by MYSP-2 §2.2 / ADR-0003: declared charset, then
strict UTF-8, then windows-1251 as the last resort (it accepts almost any byte
sequence, so it must never be tried earlier).
"""

from __future__ import annotations

from dataclasses import dataclass, field

UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_UTF32_BOMS = (b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")

_ALIASES = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "utf-8-sig": "utf-8",
    "windows-1251": "windows-1251",
    "cp1251": "windows-1251",
    "win-1251": "windows-1251",
    "cp-1251": "windows-1251",
}

# Latin-1 never fails either; it is not in the chain on purpose.


@dataclass(frozen=True)
class Decoded:
    text: str | None = None
    encoding: str | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None


def normalize_charset(declared: str | None) -> str | None:
    if not declared:
        return None
    value = declared.split(";")[0].strip().strip("\"'").strip().lower()
    if not value:
        return None
    return _ALIASES.get(value, value)


def decode_body(raw: bytes, declared: str | None) -> Decoded:
    if raw.startswith(UTF8_BOM):
        try:
            return Decoded(text=raw.decode("utf-8-sig"), encoding="utf-8")
        except UnicodeDecodeError:
            return Decoded(error="undecodable")

    for bom in _UTF32_BOMS:
        if raw.startswith(bom):
            return Decoded(error="unsupported_encoding")
    for bom in _UTF16_BOMS:
        if raw.startswith(bom):
            return Decoded(error="unsupported_encoding")

    declared_norm = normalize_charset(declared)

    if declared_norm == "windows-1251" and _strict_utf8_with_non_ascii(raw):
        return Decoded(
            text=raw.decode("utf-8"),
            encoding="utf-8",
            warnings=("encoding_mismatch",),
        )

    candidates: list[tuple[str, bytes]] = []
    for charset in (declared_norm, "utf-8", "windows-1251"):
        if not charset or charset in [name for name, _ in candidates]:
            continue
        candidates.append((charset, raw))

    for charset, payload in candidates:
        try:
            text = payload.decode(charset)
        except (UnicodeDecodeError, LookupError):
            continue
        warnings: tuple[str, ...] = ()
        if declared_norm and charset != declared_norm:
            warnings = ("encoding_mismatch",)
        return Decoded(text=text, encoding=charset, warnings=warnings)

    return Decoded(error="undecodable")


def _strict_utf8_with_non_ascii(raw: bytes) -> bool:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return any(ord(char) > 127 for char in text)
