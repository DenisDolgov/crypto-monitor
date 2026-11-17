"""Command-line entry point: input, run, reports, exit codes (ADR-0002)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import (
    CONTACT_ENV,
    DEFAULT_CURRENCIES,
    DEFAULT_INPUT,
    DEFAULT_OUT_DIR,
    RunConfig,
    contact_from_env,
)
from .currencies import RegistryError, CurrencyRegistry
from .http_client import HttpClient, SystemClock
from .input_csv import InputError, read_exchangers
from .inventory import Deps, run
from .report import build_report, write_reports
from .robots import RobotsGate

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_INPUT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feedscan",
        description="Инвентаризация публичных машиночитаемых фидов обменников.",
    )
    parser.add_argument("--version", action="version", version=f"feedscan {__version__}")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="CSV со списком обменников")
    parser.add_argument(
        "--currencies",
        type=Path,
        default=DEFAULT_CURRENCIES,
        help="CSV-реестр валют",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="каталог отчётов")
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--per-domain-interval", type=float, default=1.0)
    parser.add_argument("--max-bytes", type=int, default=5 * 1024 * 1024)
    parser.add_argument("--max-body-bytes", type=int, default=5 * 1024 * 1024)
    parser.add_argument("--max-redirects", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None, help="проверить только первые N доменов")
    parser.add_argument(
        "--contact",
        default=None,
        help=f"контакт для User-Agent (по умолчанию env {CONTACT_ENV})",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    contact = args.contact or contact_from_env()
    if not contact:
        print(
            f"warning: {CONTACT_ENV} не задан — User-Agent без контакта "
            f"(браузерный User-Agent не подставляется)",
            file=sys.stderr,
        )

    try:
        config = RunConfig(
            input_path=args.input,
            currencies_path=args.currencies,
            out_dir=args.out_dir,
            connect_timeout=args.connect_timeout,
            timeout=args.timeout,
            retries=args.retries,
            per_domain_interval=args.per_domain_interval,
            max_bytes=args.max_bytes,
            max_body_bytes=args.max_body_bytes,
            max_redirects=args.max_redirects,
            limit=args.limit,
            contact=contact,
            verbose=args.verbose,
        )
    except ValueError as exc:
        print(f"error: неверные параметры запуска: {exc}", file=sys.stderr)
        return EXIT_INPUT

    clock = SystemClock()

    try:
        input_data = read_exchangers(config.input_path)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT

    try:
        registry = CurrencyRegistry.load(config.currencies_path)
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT

    for warning in input_data.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if input_data.errors:
        print(
            f"warning: пропущено строк входа: {len(input_data.errors)}",
            file=sys.stderr,
        )
        if args.verbose:
            for error in input_data.errors:
                print(f"  {error}", file=sys.stderr)

    http = HttpClient(config, clock=clock, domain_keys=[row.domain for row in input_data.rows])
    try:
        deps = Deps(
            http=http,
            robots=RobotsGate(http, config),
            registry=registry,
            config=config,
            clock=clock,
        )
        results = run(input_data.rows, config, deps)
    finally:
        http.close()

    report = build_report(results, config, clock.now())
    report["summary"]["input_errors"] = input_data.input_errors

    try:
        write_reports(report, config.out_dir)
    except OSError as exc:
        print(f"error: не удалось записать отчёт: {exc}", file=sys.stderr)
        return EXIT_FATAL

    summary = report["summary"]
    print(
        f"готово: доменов {summary['domains_total']}, "
        f"ok {summary['feeds_ok']}, "
        f"blocked {summary['status_counts']['blocked']}, "
        f"html {summary['status_counts']['html']}, "
        f"not_found {summary['status_counts']['not_found']}, "
        f"error {summary['status_counts']['error']}; "
        f"отчёты: {config.out_dir / 'feeds.json'}, {config.out_dir / 'feeds.md'}",
        file=sys.stderr,
    )
    return EXIT_OK
