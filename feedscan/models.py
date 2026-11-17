"""Shared data types for feedscan.

No I/O, no network, no clock access in this module: every field is filled by
callers, which keeps the rest of the package testable without sockets.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping


class Status(str, enum.Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    HTML = "html"
    BLOCKED = "blocked"
    ERROR = "error"


class Format(str, enum.Enum):
    JSON = "json"
    OWN_SCHEMA = "own_schema"
    BESTCHANGE_COMPATIBLE = "bestchange_compatible"
    CSV = "csv"
    UNKNOWN = "unknown"


class Orientation(str, enum.Enum):
    """How the published value is oriented relative to (base, quote)."""

    QUOTE_PER_BASE = "quote_per_base"
    BASE_PER_QUOTE = "base_per_quote"


class ReserveCurrency(str, enum.Enum):
    BASE = "base"
    QUOTE = "quote"
    UNKNOWN = "unknown"


class Side(str, enum.Enum):
    CLIENT_BUYS_BASE = "client_buys_base"
    CLIENT_SELLS_BASE = "client_sells_base"
    UNKNOWN = "unknown"


class RateDerived(str, enum.Enum):
    DIRECT = "direct"
    INVERTED = "inverted"


class ResponseClass(str, enum.Enum):
    PAYLOAD = "payload"
    HTML = "html"
    BLOCKED = "blocked"
    NOT_FOUND = "not_found"
    TRANSPORT_ERROR = "transport_error"
    EMPTY = "empty"


class Purpose(str, enum.Enum):
    ROBOTS = "robots"
    HOMEPAGE = "homepage"
    CANDIDATE = "candidate"
    SITEMAP = "sitemap"


class CandidateOrigin(str, enum.Enum):
    HOMEPAGE_LINK = "homepage_link"
    WELL_KNOWN = "well_known"
    SITEMAP = "sitemap"


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    status: int | None
    headers: Mapping[str, str]
    body: bytes | None
    text: str | None
    encoding: str | None
    gzip: bool
    size_bytes: int
    compressed_bytes: int
    redirect_count: int
    warnings: tuple[str, ...] = ()
    error: str | None = None
    attempts: int = 1
    elapsed_ms: int = 0

    @property
    def content_type(self) -> str | None:
        value = self.headers.get("content-type")
        return value.split(";")[0].strip().lower() if value else None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300


@dataclass(frozen=True)
class Candidate:
    url: str
    origin: CandidateOrigin
    order: int = 0


@dataclass(frozen=True)
class ExchangerRow:
    name: str
    domain: str
    url: str
    source: str
    notes: str = ""


@dataclass(frozen=True)
class RawRate:
    """One swap row straight from a feed, before any normalisation."""

    source_url: str
    base_id: str | None = None
    quote_id: str | None = None
    rate_raw: str | None = None
    in_raw: str | None = None
    out_raw: str | None = None
    orientation: Orientation = Orientation.QUOTE_PER_BASE
    side_raw: str | None = None
    reserve_raw: str | None = None
    reserve_currency: ReserveCurrency = ReserveCurrency.UNKNOWN
    reserve_currency_id: str | None = None
    min_raw: str | None = None
    max_raw: str | None = None
    fee_raw: str | None = None
    updated_raw: str | None = None
    path: str = ""


@dataclass(frozen=True)
class RawData:
    sha256: str | None
    size_bytes: int
    format: str
    encoding: str | None
    http_status: int | None
    content_type: str | None
    gzip: bool

    def to_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "format": self.format,
            "encoding": self.encoding,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "gzip": self.gzip,
        }


@dataclass(frozen=True)
class Rate:
    exchanger_domain: str
    base: str
    quote: str
    rate: Decimal
    side: Side
    reserve: Decimal | None
    reserve_derived: bool
    min_amount: Decimal | None
    max_amount: Decimal | None
    fee_percent: Decimal | None
    updated_at: datetime | None
    fetched_at: datetime
    rate_derived: RateDerived
    source_url: str
    raw: RawData
    warnings: tuple[str, ...] = ()

    @property
    def direction(self) -> str:
        return f"{self.base}/{self.quote}"

    def to_dict(self) -> dict:
        return {
            "exchanger_domain": self.exchanger_domain,
            "base": self.base,
            "quote": self.quote,
            "rate": format_decimal(self.rate),
            "side": self.side.value,
            "reserve": format_decimal(self.reserve) if self.reserve is not None else None,
            "reserve_derived": self.reserve_derived,
            "min_amount": format_decimal(self.min_amount) if self.min_amount is not None else None,
            "max_amount": format_decimal(self.max_amount) if self.max_amount is not None else None,
            "fee_percent": format_decimal(self.fee_percent) if self.fee_percent is not None else None,
            "updated_at": format_datetime(self.updated_at),
            "fetched_at": format_datetime(self.fetched_at),
            "direction": self.direction,
            "rate_derived": self.rate_derived.value,
            "source_url": self.source_url,
            "raw": self.raw.to_dict(),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class Drop:
    reason: str
    detail: str
    raw_currency_id: str | None = None


@dataclass(frozen=True)
class NormalizedDocument:
    rates: tuple[Rate, ...]
    drops: tuple[Drop, ...]
    warnings: tuple[str, ...] = ()

    @property
    def dropped_by_reason(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for drop in self.drops:
            counts[drop.reason] = counts.get(drop.reason, 0) + 1
        return counts


@dataclass(frozen=True)
class CandidateAttempt:
    url: str
    origin: CandidateOrigin
    http_status: int | None
    response_class: ResponseClass | None
    reason_code: str | None
    reason_detail: str | None
    size_bytes: int
    duration_ms: int
    attempts: int = 1

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "origin": self.origin.value,
            "http_status": self.http_status,
            "response_class": self.response_class.value if self.response_class else None,
            "reason_code": self.reason_code,
            "size_bytes": self.size_bytes,
            "duration_ms": self.duration_ms,
            "http_requests": self.attempts,
        }


@dataclass
class DomainResult:
    domain: str
    name: str
    source: str
    status: Status = Status.NOT_FOUND
    reason_code: str | None = None
    reason_detail: str | None = None
    format: Format = Format.UNKNOWN
    format_evidence: str | None = None
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    encoding: str | None = None
    gzip: bool = False
    size_bytes: int = 0
    updated_at: datetime | None = None
    rates: tuple[Rate, ...] = ()
    mvp_directions_found: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)
    attempts: list[CandidateAttempt] = field(default_factory=list)
    checked_at: datetime | None = None
    duration_ms: int = 0
    dropped_by_reason: Mapping[str, int] = field(default_factory=dict)
    notes: str = ""

    @property
    def rate_count(self) -> int:
        return len(self.rates)

    def examples(self, limit: int = 2) -> list[dict]:
        return [rate.to_dict() for rate in select_examples(self.rates, limit)]


def select_examples(rates: tuple[Rate, ...], limit: int = 2) -> list[Rate]:
    """First ``limit`` rows with distinct (base, quote) in a stable order.

    Sorting is by Decimal, not by the string form, so the choice does not
    depend on the order of rows inside the feed (AC-12).
    """

    ordered = sorted(rates, key=lambda r: (r.base, r.quote, r.side.value, r.rate))
    picked: list[Rate] = []
    seen: set[tuple[str, str]] = set()
    for rate in ordered:
        key = (rate.base, rate.quote)
        if key in seen:
            continue
        seen.add(key)
        picked.append(rate)
        if len(picked) == limit:
            break
    return picked


def format_decimal(value: Decimal) -> str:
    """Render a Decimal without exponent and without trailing zeros."""

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return text


def format_datetime(value: datetime | None) -> str | None:
    """RFC 3339 UTC with a trailing ``Z``; naive values are read as UTC."""

    if value is None:
        return None
    if value.tzinfo is None:
        utc_value = value.replace(tzinfo=timezone.utc)
    else:
        utc_value = value.astimezone(timezone.utc)
    return utc_value.strftime("%Y-%m-%dT%H:%M:%SZ")
