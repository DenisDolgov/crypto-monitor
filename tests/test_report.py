"""Report shape, determinism and Markdown content (AC-06…AC-12)."""

from __future__ import annotations

import json
import re

from feedscan.config import RunConfig
from feedscan.models import (
    CandidateAttempt,
    CandidateOrigin,
    DomainResult,
    Format,
    Rate,
    RateDerived,
    RawData,
    ResponseClass,
    Side,
    Status,
)
from feedscan.report import build_report, render_markdown, write_reports
from datetime import datetime, timezone
from decimal import Decimal

GENERATED_AT = datetime(2026, 11, 17, 12, 30, 0, tzinfo=timezone.utc)
CHECKED_AT = datetime(2026, 11, 17, 12, 29, 0, tzinfo=timezone.utc)
RAW = RawData("a" * 64, 512, "own_schema", "utf-8", 200, "application/xml", False)


def make_rate(base="USDT", quote="RUB", value="92.35", side=Side.UNKNOWN) -> Rate:
    return Rate(
        exchanger_domain="a.test",
        base=base,
        quote=quote,
        rate=Decimal(value),
        side=side,
        reserve=Decimal("1000"),
        reserve_derived=False,
        min_amount=None,
        max_amount=None,
        fee_percent=None,
        updated_at=datetime(2026, 11, 17, 11, 0, tzinfo=timezone.utc),
        fetched_at=CHECKED_AT,
        rate_derived=RateDerived.DIRECT,
        source_url="https://a.test/feed.xml",
        raw=RAW,
    )


def ok_result() -> DomainResult:
    result = DomainResult(
        domain="a.test",
        name="Exchanger A",
        source="manual",
        status=Status.OK,
        reason_code="found",
        reason_detail="фид найден, курсов: 3",
        format=Format.OWN_SCHEMA,
        format_evidence="first record at /rates/rate[1]",
        final_url="https://a.test/feed.xml",
        http_status=200,
        content_type="application/xml",
        encoding="utf-8",
        size_bytes=512,
        updated_at=datetime(2026, 11, 17, 11, 0, tzinfo=timezone.utc),
        rates=(
            make_rate(),
            make_rate("BTC", "RUB", "7800000.5"),
            make_rate("USDT", "RUB", "92.40", Side.CLIENT_BUYS_BASE),
        ),
        mvp_directions_found=("USDT/RUB", "BTC/RUB"),
        checked_at=CHECKED_AT,
        duration_ms=1500,
    )
    result.attempts.append(
        CandidateAttempt(
            url="https://a.test/feed.xml",
            origin=CandidateOrigin.WELL_KNOWN,
            http_status=200,
            response_class=ResponseClass.PAYLOAD,
            reason_code="found",
            reason_detail="HTTP 200",
            size_bytes=512,
            duration_ms=120,
        )
    )
    return result


def blocked_result() -> DomainResult:
    result = DomainResult(
        domain="b.test",
        name="Exchanger B",
        source="manual",
        status=Status.BLOCKED,
        reason_code="http_403",
        reason_detail="HTTP 403",
        checked_at=CHECKED_AT,
        duration_ms=800,
    )
    result.attempts.append(
        CandidateAttempt(
            url="https://b.test/rates.xml",
            origin=CandidateOrigin.WELL_KNOWN,
            http_status=403,
            response_class=ResponseClass.BLOCKED,
            reason_code="http_403",
            reason_detail="HTTP 403",
            size_bytes=0,
            duration_ms=20,
        )
    )
    return result


def config() -> RunConfig:
    return RunConfig(contact="ops@example.test")


def test_json_has_required_top_level_and_domain_fields():
    report = build_report([ok_result(), blocked_result()], config(), GENERATED_AT)
    assert set(report) == {"generated_at", "config", "summary", "domains"}
    required = {
        "domain",
        "name",
        "source",
        "status",
        "reason_code",
        "reason_detail",
        "format",
        "format_evidence",
        "final_url",
        "http_status",
        "content_type",
        "encoding",
        "gzip",
        "size_bytes",
        "updated_at",
        "rate_count",
        "mvp_directions_found",
        "examples",
        "warnings",
        "attempts",
        "checked_at",
        "duration_ms",
    }
    assert required <= set(report["domains"][0])
    assert required <= set(report["domains"][1])


def test_summary_counts_every_status_including_zeros():
    report = build_report([ok_result()], config(), GENERATED_AT)
    counts = report["summary"]["status_counts"]
    assert set(counts) == {"ok", "not_found", "html", "blocked", "error"}
    assert counts["ok"] == 1
    assert counts["blocked"] == 0
    assert report["summary"]["feeds_ok"] == 1
    assert report["summary"]["feeds_with_mvp_directions"] == 1


def test_ok_examples_are_limited_and_distinct():
    report = build_report([ok_result()], config(), GENERATED_AT)
    domain = report["domains"][0]
    assert len(domain["examples"]) == 2
    by_base = {example["base"]: example for example in domain["examples"]}
    assert set(by_base) == {"USDT", "BTC"}
    # Ordering inside examples is (base, quote, side, rate): "client_buys_base"
    # sorts before "unknown", so the USDT row shown is the published buy rate.
    assert by_base["USDT"]["rate"] == "92.4"
    assert by_base["BTC"]["rate"] == "7800000.5"
    assert domain["examples"][0]["raw"]["format"] == "own_schema"


def test_blocked_domain_has_no_examples_but_has_reason():
    report = build_report([blocked_result()], config(), GENERATED_AT)
    domain = report["domains"][0]
    assert domain["examples"] == []
    assert domain["rate_count"] == 0
    assert domain["reason_detail"]


def test_domains_are_sorted_by_domain():
    report = build_report([blocked_result(), ok_result()], config(), GENERATED_AT)
    assert [item["domain"] for item in report["domains"]] == ["a.test", "b.test"]


def test_markdown_contains_all_sections():
    report = build_report([ok_result(), blocked_result()], config(), GENERATED_AT)
    markdown = render_markdown(report)
    for heading in (
        "# Инвентаризация фидов обменников",
        "## Параметры прогона",
        "## Сводка",
        "## Домены",
        "## Пригодные для MVP",
        "## Отказы",
        "## Выводы и следующий шаг",
    ):
        assert heading in markdown
    assert "`b.test`" in markdown
    assert "обход не выполнялся" in markdown
    assert "Следующий шаг" in markdown


def test_reports_have_no_absolute_paths_or_emails(tmp_path):
    report = build_report([ok_result(), blocked_result()], config(), GENERATED_AT)
    json_path, md_path = write_reports(report, tmp_path)
    json_text = json_path.read_text(encoding="utf-8")
    md_text = md_path.read_text(encoding="utf-8")

    for text in (json_text, md_text):
        assert "/Users/" not in text
        assert str(tmp_path) not in text
        assert "ops@example.test" in text  # the configured contact is allowed
        assert not re.search(r"[\w.]+@[\w.]+", text.replace("ops@example.test", ""))

    assert json.loads(json_text)["summary"]["domains_total"] == 2


def test_write_reports_is_atomic(tmp_path):
    report = build_report([ok_result()], config(), GENERATED_AT)
    write_reports(report, tmp_path)
    leftovers = [path.name for path in tmp_path.iterdir() if path.name.endswith(".tmp")]
    assert leftovers == []


def test_duration_and_timestamps_are_the_only_volatile_fields():
    first = build_report([ok_result()], config(), GENERATED_AT)
    second = build_report([ok_result()], config(), GENERATED_AT)

    def strip(report):
        clone = json.loads(json.dumps(report))
        clone["generated_at"] = None
        for domain in clone["domains"]:
            domain["checked_at"] = None
            domain["duration_ms"] = None
            for attempt in domain["attempts"]:
                attempt["duration_ms"] = None
        return clone

    assert strip(first) == strip(second)


def test_input_errors_and_dropped_rows_are_surfaced():
    result = ok_result()
    result.dropped_by_reason = {"unmapped_currency:USDTTRC": 2, "invalid_rate": 1}
    report = build_report([result], config(), GENERATED_AT)
    summary = report["summary"]
    assert summary["dropped_rate_rows"]["invalid_rate"] == 1
    assert summary["top_unmapped_currencies"] == [{"id": "USDTTRC", "count": 2}]


def test_conclusions_mention_next_step_for_empty_success():
    result = blocked_result()
    report = build_report([result], config(), GENERATED_AT)
    markdown = render_markdown(report)
    assert "Без пригодных фидов" in markdown
    assert "Ни один формат не определён" in markdown
    assert "Преобладающий формат" not in markdown
