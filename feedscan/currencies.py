"""Currency registry: codes, names, types and feed aliases (ADR-0001).

The registry is data, not code: adding a currency is a CSV edit reviewed like
any other data change. Unknown identifiers are never guessed by prefix.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

CODE_RE = re.compile(r"^[A-Z0-9]{2,10}$")
ALLOWED_TYPES = ("fiat", "crypto", "metal")
_REQUIRED_COLUMNS = ("code", "name", "type", "aliases")


class RegistryError(ValueError):
    """Configuration error in ``data/currencies.csv`` (CLI exit code 2)."""


@dataclass(frozen=True)
class Currency:
    code: str
    name: str
    type: str
    aliases: tuple[str, ...] = ()


class CurrencyRegistry:
    def __init__(self, currencies: list[Currency]) -> None:
        self._by_code = {currency.code: currency for currency in currencies}
        self._by_alias: dict[str, str] = {}
        for currency in currencies:
            for alias in currency.aliases:
                key = _alias_key(alias)
                existing = self._by_alias.get(key)
                if existing is not None and existing != currency.code:
                    raise RegistryError(
                        f"ambiguous alias {alias!r}: {existing} and {currency.code}"
                    )
                self._by_alias[key] = currency.code

    def __len__(self) -> int:
        return len(self._by_code)

    def get(self, code: str) -> Currency | None:
        return self._by_code.get(code)

    def resolve(self, raw_id: str | None) -> str | None:
        """Map a feed identifier to a registry code, or ``None`` if unknown."""

        if raw_id is None:
            return None
        key = _alias_key(raw_id)
        if not key:
            return None
        if key in self._by_code:
            return key
        return self._by_alias.get(key)

    @classmethod
    def load(cls, path: Path) -> "CurrencyRegistry":
        if not path.exists():
            raise RegistryError(f"currency registry not found: {path}")
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = [name.strip() for name in (reader.fieldnames or [])]
                missing = [name for name in _REQUIRED_COLUMNS if name not in fieldnames]
                if missing:
                    raise RegistryError(
                        f"{path}: missing required column(s): {', '.join(missing)}"
                    )
                currencies: list[Currency] = []
                seen: set[str] = set()
                for line_no, row in enumerate(reader, start=2):
                    values = {(key or "").strip(): (value or "").strip() for key, value in row.items()}
                    code = values.get("code", "")
                    if not code:
                        continue
                    if not CODE_RE.match(code):
                        raise RegistryError(f"{path}:{line_no}: invalid currency code {code!r}")
                    if code in seen:
                        raise RegistryError(f"{path}:{line_no}: duplicate currency code {code}")
                    seen.add(code)
                    currency_type = values.get("type", "").lower()
                    if currency_type not in ALLOWED_TYPES:
                        raise RegistryError(
                            f"{path}:{line_no}: invalid type {currency_type!r} "
                            f"(expected one of {', '.join(ALLOWED_TYPES)})"
                        )
                    aliases = tuple(
                        alias.strip()
                        for alias in values.get("aliases", "").split("|")
                        if alias.strip()
                    )
                    currencies.append(
                        Currency(
                            code=code,
                            name=values.get("name", "") or code,
                            type=currency_type,
                            aliases=aliases,
                        )
                    )
        except (OSError, csv.Error) as exc:
            raise RegistryError(f"{path}: cannot read currency registry: {exc}") from exc
        if not currencies:
            raise RegistryError(f"{path}: currency registry is empty")
        return cls(currencies)


def _alias_key(value: str) -> str:
    return "".join(value.split()).upper()
