"""Run configuration: defaults, command-line overrides, environment contact.

Defaults are normative: the acceptance criteria reference these numbers, and
every one of them can be overridden by a CLI flag (ADR-0002).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MiB = 1024 * 1024

DEFAULT_INPUT = Path("data/exchangers.csv")
DEFAULT_CURRENCIES = Path("data/currencies.csv")
DEFAULT_OUT_DIR = Path("reports")
DEFAULT_MVP_DIRECTIONS = ("USDT/RUB", "BTC/RUB")
CONTACT_ENV = "FEEDSCAN_CONTACT"


@dataclass(frozen=True)
class RunConfig:
    input_path: Path = DEFAULT_INPUT
    currencies_path: Path = DEFAULT_CURRENCIES
    out_dir: Path = DEFAULT_OUT_DIR
    connect_timeout: float = 5.0
    timeout: float = 15.0
    retries: int = 2
    per_domain_interval: float = 1.0
    max_bytes: int = 5 * MiB
    max_body_bytes: int = 5 * MiB
    max_redirects: int = 3
    limit: int | None = None
    contact: str | None = None
    verbose: bool = False
    mvp_directions: tuple[str, ...] = DEFAULT_MVP_DIRECTIONS
    user_agent: str = field(init=False, default="")

    def __post_init__(self) -> None:
        if self.retries < 0:
            raise ValueError("retries must be >= 0")
        if self.per_domain_interval < 0:
            raise ValueError("per-domain-interval must be >= 0")
        if self.max_bytes <= 0 or self.max_body_bytes <= 0:
            raise ValueError("size limits must be positive")
        if self.max_redirects < 0:
            raise ValueError("max-redirects must be >= 0")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("limit must be positive")
        contact = (self.contact or "").strip() or None
        object.__setattr__(self, "contact", contact)
        version = _version()
        object.__setattr__(
            self,
            "user_agent",
            f"feedscan/{version} (+{contact})" if contact else f"feedscan/{version}",
        )

    @property
    def max_requests_per_candidate(self) -> int:
        """Hard cap on HTTP requests for one candidate URL.

        The retry budget (``retries``) applies to network failures, while
        redirects have their own budget (``max_redirects``). The sum keeps a
        chain of three redirects inside the retry budget instead of allowing
        ``(retries + 1) * (max_redirects + 1)`` requests.
        """

        return self.retries + 1 + self.max_redirects

    def to_report_dict(self) -> dict:
        return {
            "version": _version(),
            "input": self.input_path.as_posix(),
            "currencies": self.currencies_path.as_posix(),
            "connect_timeout_s": self.connect_timeout,
            "timeout_s": self.timeout,
            "retries": self.retries,
            "per_domain_interval_s": self.per_domain_interval,
            "max_bytes": self.max_bytes,
            "max_body_bytes": self.max_body_bytes,
            "max_redirects": self.max_redirects,
            "ua_contact": self.contact,
            "mvp_directions": list(self.mvp_directions),
        }


def contact_from_env(environ: dict[str, str] | None = None) -> str | None:
    source = os.environ if environ is None else environ
    value = (source.get(CONTACT_ENV) or "").strip()
    return value or None


def _version() -> str:
    from . import __version__

    return __version__
