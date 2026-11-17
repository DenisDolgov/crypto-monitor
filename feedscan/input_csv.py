"""Reading ``data/exchangers.csv`` (MYSP-2 §5.1).

Bad rows are skipped and counted; a missing mandatory column or an unreadable
file is a fatal input error (CLI exit code 2).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .models import ExchangerRow

REQUIRED_COLUMNS = ("name", "domain", "url", "source", "notes")
KNOWN_SOURCES = ("bestchange", "manual")
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


class InputError(ValueError):
    """Fatal problem with the input CSV (CLI exit code 2)."""


@dataclass(frozen=True)
class CsvInput:
    rows: tuple[ExchangerRow, ...]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default=())

    @property
    def input_errors(self) -> int:
        return len(self.errors)


def read_exchangers(path: Path) -> CsvInput:
    if not path.exists():
        raise InputError(f"input file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise InputError(f"cannot read input file {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise InputError(f"{path}: input is not valid UTF-8: {exc}") from exc

    reader = csv.DictReader(text.splitlines())
    fieldnames = [name.strip() for name in (reader.fieldnames or [])]
    missing = [name for name in REQUIRED_COLUMNS if name not in fieldnames]
    if missing:
        raise InputError(f"{path}: missing required column(s): {', '.join(missing)}")

    extra_columns = [name for name in fieldnames if name and name not in REQUIRED_COLUMNS]
    warnings = [f"extra_column:{name}" for name in extra_columns]

    rows: list[ExchangerRow] = []
    errors: list[str] = []
    seen_domains: set[str] = set()
    duplicates: set[str] = set()

    for line_no, raw in enumerate(reader, start=2):
        values = {(key or "").strip(): (value or "").strip() for key, value in raw.items()}
        try:
            row = _build_row(values)
        except ValueError as exc:
            errors.append(f"line {line_no}: {exc}")
            continue
        if row.domain in seen_domains:
            if row.domain not in duplicates:
                duplicates.add(row.domain)
                warnings.append(f"duplicate_domain:{row.domain}")
            continue
        seen_domains.add(row.domain)
        rows.append(row)

    return CsvInput(rows=tuple(rows), errors=tuple(errors), warnings=tuple(warnings))


def _build_row(values: dict[str, str]) -> ExchangerRow:
    name = values.get("name", "")
    domain = values.get("domain", "").lower()
    url = values.get("url", "")
    source = values.get("source", "").lower()
    notes = values.get("notes", "")

    if not name:
        raise ValueError("empty name")
    if not domain:
        raise ValueError("empty domain")
    if not DOMAIN_RE.match(domain):
        raise ValueError(f"invalid domain {domain!r}")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"invalid url {url!r}")
    if source not in KNOWN_SOURCES:
        raise ValueError(f"unknown source {source!r} (expected {', '.join(KNOWN_SOURCES)})")
    return ExchangerRow(name=name, domain=domain, url=url, source=source, notes=notes)
