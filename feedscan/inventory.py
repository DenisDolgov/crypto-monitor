"""Per-domain orchestration and the whole run (ADR-0003).

One domain at a time: robots → homepage links → well-known paths → lazy
sitemap. The first valid feed finishes the domain with ``ok``; otherwise the
observations are reduced to one status by the fixed priority.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlsplit

from .classify import blocked_evidence, classify_fetch, domain_status, title_of
from .config import RunConfig
from .currencies import CurrencyRegistry
from .discovery import (
    CANDIDATE_LIMIT,
    candidate_cap,
    dedupe_candidates,
    homepage_candidates,
    normalize_url,
    sitemap_candidates,
    well_known_candidates,
)
from .http_client import HttpClient
from .models import (
    Candidate,
    CandidateAttempt,
    CandidateOrigin,
    DomainResult,
    ExchangerRow,
    FetchResult,
    Format,
    Purpose,
    RawData,
    ResponseClass,
    Status,
)
from .normalize import normalize_document
from .parsers import parse_document
from .robots import RobotsDecision, RobotsGate

LOGGER = logging.getLogger("feedscan")


@dataclass
class Deps:
    http: HttpClient
    robots: RobotsGate
    registry: CurrencyRegistry
    config: RunConfig
    clock: object

    def now(self) -> datetime:
        return self.clock.now()


def check_domain(row: ExchangerRow, deps: Deps) -> DomainResult:
    config = deps.config
    started = deps.clock.monotonic()
    result = DomainResult(
        domain=row.domain,
        name=row.name,
        source=row.source,
        notes=row.notes,
        checked_at=deps.now(),
    )
    base_url = f"https://{row.domain}/"

    robots = deps.robots.check(base_url)
    if robots is RobotsDecision.BLOCKED:
        detail = deps.robots.failure(base_url) or "HTTP 403"
        result.status = Status.BLOCKED
        result.reason_code = "http_403"
        result.reason_detail = f"robots.txt: отказ ({detail}), обход не выполнялся"
        return _finish(result, deps, started)
    if robots is RobotsDecision.UNAVAILABLE:
        detail = deps.robots.failure(base_url) or "нет ответа"
        result.status = Status.ERROR
        result.reason_code = "robots_unavailable"
        result.reason_detail = (
            f"robots.txt недоступен ({detail}); проверка домена не выполнена"
        )
        return _finish(result, deps, started)

    crawl_delay = deps.robots.crawl_delay(base_url)
    if crawl_delay is not None and crawl_delay > config.per_domain_interval:
        deps.http.set_domain_interval(row.domain, crawl_delay)
        result.warnings.append(f"crawl_delay:{crawl_delay:g}")

    homepage = None
    if robots is RobotsDecision.DISALLOWED:
        result.attempts.append(
            CandidateAttempt(
                url=base_url,
                origin=CandidateOrigin.HOMEPAGE_LINK,
                http_status=None,
                response_class=ResponseClass.NOT_FOUND,
                reason_code="robots_disallowed",
                reason_detail="главная запрещена robots.txt",
                size_bytes=0,
                duration_ms=0,
            )
        )
    else:
        homepage = deps.http.get(base_url, purpose=Purpose.HOMEPAGE)
        for warning in homepage.warnings:
            if warning not in result.warnings:
                result.warnings.append(warning)

    candidates: list[Candidate] = []
    if homepage is not None and homepage.status is not None and homepage.status < 400 and homepage.text:
        candidates.extend(
            homepage_candidates(homepage.text, homepage.final_url or base_url, row.domain)
        )
    elif homepage is not None and homepage.error is not None:
        result.warnings.append(f"homepage:{homepage.error}")

    candidates.extend(well_known_candidates(row.domain))
    candidates = dedupe_candidates(candidates)
    candidates, capped = candidate_cap(candidates, CANDIDATE_LIMIT)
    if capped:
        result.warnings.append("candidate_cap_reached")

    if _check_candidates(candidates, result, deps):
        return _finish(result, deps, started)

    sitemap_url = f"https://{row.domain}/sitemap.xml"
    if deps.robots.check(sitemap_url) is not RobotsDecision.DISALLOWED:
        sitemap = deps.http.get(sitemap_url, purpose=Purpose.SITEMAP)
        if sitemap.ok and sitemap.text:
            extra = sitemap_candidates(sitemap.text, sitemap.final_url or sitemap_url, row.domain)
            tried = {normalize_url(candidate.url) for candidate in candidates}
            extra = [candidate for candidate in extra if normalize_url(candidate.url) not in tried]
            extra, capped = candidate_cap(extra, max(CANDIDATE_LIMIT - len(candidates), 0))
            if capped:
                result.warnings.append("candidate_cap_reached")
            if extra and _check_candidates(extra, result, deps):
                return _finish(result, deps, started)
        elif sitemap.error is not None:
            result.warnings.append(f"sitemap:{sitemap.error}")

    status, reason_code, reason_detail = domain_status(result.attempts)
    result.status = status
    result.reason_code = reason_code
    result.reason_detail = reason_detail
    return _finish(result, deps, started)


def _check_candidates(
    candidates: list[Candidate],
    result: DomainResult,
    deps: Deps,
) -> bool:
    for candidate in candidates:
        decision = deps.robots.check(candidate.url)
        if decision is RobotsDecision.BLOCKED:
            result.attempts.append(
                CandidateAttempt(
                    url=candidate.url,
                    origin=candidate.origin,
                    http_status=None,
                    response_class=ResponseClass.BLOCKED,
                    reason_code="http_403",
                    reason_detail="robots.txt: отказ в доступе",
                    size_bytes=0,
                    duration_ms=0,
                )
            )
            continue
        if decision is RobotsDecision.DISALLOWED:
            result.attempts.append(
                CandidateAttempt(
                    url=candidate.url,
                    origin=candidate.origin,
                    http_status=None,
                    response_class=ResponseClass.NOT_FOUND,
                    reason_code="robots_disallowed",
                    reason_detail="путь запрещён robots.txt",
                    size_bytes=0,
                    duration_ms=0,
                )
            )
            continue

        fetch = deps.http.get(candidate.url, purpose=Purpose.CANDIDATE)
        attempt = _attempt_from(fetch, candidate)
        result.attempts.append(attempt)
        for warning in fetch.warnings:
            if warning not in result.warnings:
                result.warnings.append(warning)

        if attempt.response_class is not ResponseClass.PAYLOAD:
            continue

        parsed = parse_document(fetch.text or "", fetch.final_url)
        if parsed.error is not None:
            result.attempts[-1] = replace(
                attempt,
                reason_code=parsed.error,
                reason_detail=parsed.error_detail,
                response_class=ResponseClass.TRANSPORT_ERROR,
            )
            continue

        fetch_time = deps.now()
        raw = RawData(
            sha256=hashlib.sha256(fetch.body).hexdigest() if fetch.body else None,
            size_bytes=fetch.size_bytes,
            format=parsed.format.value,
            encoding=fetch.encoding,
            http_status=fetch.status,
            content_type=fetch.content_type,
            gzip=fetch.gzip,
        )
        document = normalize_document(
            parsed.raws,
            deps.registry,
            exchanger_domain=result.domain,
            fetched_at=fetch_time,
            raw=raw,
        )
        if not document.rates:
            result.attempts[-1] = replace(
                attempt,
                reason_code="no_rates_in_feed",
                reason_detail="курсы не извлеклись после нормализации",
                response_class=ResponseClass.TRANSPORT_ERROR,
            )
            result.dropped_by_reason = dict(document.dropped_by_reason)
            continue

        _apply_ok(result, fetch, parsed, document, deps)
        return True
    return False


def _attempt_from(fetch: FetchResult, candidate: Candidate) -> CandidateAttempt:
    response_class, reason_code = classify_fetch(fetch)
    detail = _reason_detail(fetch, reason_code)
    return CandidateAttempt(
        url=fetch.requested_url,
        origin=candidate.origin,
        http_status=fetch.status,
        response_class=response_class,
        reason_code=reason_code,
        reason_detail=detail,
        size_bytes=fetch.size_bytes,
        duration_ms=fetch.elapsed_ms,
        attempts=fetch.attempts,
    )


def _reason_detail(fetch: FetchResult, reason_code: str) -> str:
    if fetch.error is not None:
        return f"{reason_code} after {fetch.attempts} request(s)"
    parts = [f"HTTP {fetch.status}"]
    if reason_code.startswith("http_"):
        return ", ".join(parts)
    if reason_code.startswith("challenge_") or reason_code == "access_denied_title":
        marker = blocked_evidence(fetch.text or "", fetch.status)
        if marker:
            parts.append(f'marker="{marker}"')
    title = title_of(fetch.text or "")
    if title:
        parts.append(f'title="{title}"')
    if fetch.final_url != fetch.requested_url:
        parts.append(f"final_url={fetch.final_url}")
    return ", ".join(parts)[:200]


def _apply_ok(result, fetch, parsed, document, deps: Deps) -> None:
    rates = document.rates
    result.status = Status.OK
    result.reason_code = "found"
    result.reason_detail = f"фид найден, курсов: {len(rates)}"
    result.format = parsed.format
    result.format_evidence = parsed.evidence[:200] if parsed.evidence else None
    result.final_url = fetch.final_url
    result.http_status = fetch.status
    result.content_type = fetch.content_type
    result.encoding = fetch.encoding
    result.gzip = fetch.gzip
    result.size_bytes = fetch.size_bytes
    result.rates = rates
    result.dropped_by_reason = dict(document.dropped_by_reason)

    updated = [rate.updated_at for rate in rates if rate.updated_at is not None]
    result.updated_at = max(updated) if updated else None

    directions: set[str] = set()
    for rate in rates:
        directions.add(rate.direction)
        directions.add(f"{rate.quote}/{rate.base}")
    result.mvp_directions_found = tuple(
        direction for direction in deps.config.mvp_directions if direction in directions
    )


def _finish(result: DomainResult, deps: Deps, started: float) -> DomainResult:
    result.duration_ms = int(round((deps.clock.monotonic() - started) * 1000))
    return result


def run(rows, config: RunConfig, deps: Deps) -> tuple[DomainResult, ...]:
    ordered = sorted(rows, key=lambda row: row.domain)
    if config.limit is not None:
        ordered = ordered[: config.limit]

    results: list[DomainResult] = []
    for row in ordered:
        LOGGER.info("checking %s", row.domain)
        try:
            result = check_domain(row, deps)
        except Exception as exc:  # pragma: no cover - defensive: one domain never kills the run
            LOGGER.exception("unexpected failure for %s", row.domain)
            result = DomainResult(
                domain=row.domain,
                name=row.name,
                source=row.source,
                notes=row.notes,
                status=Status.ERROR,
                reason_code="read_error",
                reason_detail=f"внутренняя ошибка проверки: {type(exc).__name__}",
                checked_at=deps.now(),
            )
        LOGGER.info("%s -> %s (%s)", result.domain, result.status.value, result.reason_code)
        results.append(result)
    return tuple(results)


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
