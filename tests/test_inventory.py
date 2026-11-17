"""Domain orchestration on fake transports (AC-08, AC-13…AC-24)."""

from __future__ import annotations

import httpx
import pytest

from feedscan.config import RunConfig
from feedscan.currencies import Currency, CurrencyRegistry
from feedscan.http_client import HttpClient
from feedscan.inventory import Deps, check_domain, run
from feedscan.models import ExchangerRow, Status
from feedscan.robots import RobotsGate
from tests.conftest import FakeTransport, Resp, gzip_response, html_response

DOMAIN = "a.test"
BASE = f"https://{DOMAIN}/"


def registry() -> CurrencyRegistry:
    return CurrencyRegistry(
        [
            Currency("USDT", "Tether", "crypto", ("USDTTRC",)),
            Currency("BTC", "Bitcoin", "crypto", ()),
            Currency("ETH", "Ether", "crypto", ()),
            Currency("RUB", "Rouble", "fiat", ("SberbankRUB",)),
        ]
    )


def make_deps(routes, clock, *, config=None, domains=(DOMAIN,)):
    config = config or RunConfig(contact="ops@example.test", per_domain_interval=1.0, retries=0)
    transport = FakeTransport(routes, clock=clock)
    http = HttpClient(config, transport=transport, clock=clock, domain_keys=domains)
    return (
        Deps(
            http=http,
            robots=RobotsGate(http, config),
            registry=registry(),
            config=config,
            clock=clock,
        ),
        transport,
    )


def row(domain=DOMAIN, name="Exchanger A") -> ExchangerRow:
    return ExchangerRow(
        name=name, domain=domain, url=f"https://{domain}/", source="manual", notes="fixture"
    )


def feed_route(fixtures_dir, name="feed_own.xml", url=None):
    body = (fixtures_dir / name).read_bytes()
    return {url or f"{BASE}feed.xml": Resp(200, {"content-type": "application/xml; charset=utf-8"}, body)}


def test_ok_domain_with_homepage_link(fixtures_dir, clock):
    routes = {
        BASE: html_response(f'<a href="/feed.xml">feed</a>'),
        **feed_route(fixtures_dir),
    }
    deps, transport = make_deps(routes, clock)
    result = check_domain(row(), deps)

    assert result.status is Status.OK
    assert result.reason_code == "found"
    assert result.format.value == "own_schema"
    assert result.rate_count == 3
    assert result.mvp_directions_found == ("USDT/RUB", "BTC/RUB")
    assert result.final_url == f"{BASE}feed.xml"
    assert result.http_status == 200
    assert result.encoding == "utf-8"
    assert result.examples(2)
    # robots, homepage, feed
    assert transport.count(f"{BASE}feed.xml") == 1
    raw = result.rates[0].raw
    assert len(raw.sha256 or "") == 64
    assert raw.format == "own_schema"
    assert raw.http_status == 200


def test_first_valid_feed_stops_the_domain(fixtures_dir, clock):
    routes = {
        BASE: html_response(f'<a href="/feed.xml">feed</a>'),
        **feed_route(fixtures_dir),
    }
    deps, transport = make_deps(routes, clock)
    check_domain(row(), deps)
    assert transport.count(f"{BASE}rates.xml") == 0
    assert transport.count(f"{BASE}sitemap.xml") == 0


def test_404_everywhere_is_not_found(fixtures_dir, clock):
    deps, _ = make_deps({BASE: html_response("<html></html>")}, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.NOT_FOUND
    assert result.reason_code == "http_404"
    assert result.format.value == "unknown"


def test_html_pages_are_html_status(fixtures_dir, clock):
    html = (fixtures_dir / "html_page.html").read_text(encoding="utf-8")
    routes = {BASE: html_response(html)}
    for candidate in ("rates.xml", "export/rates.xml", "xml/rates.xml", "bestchange.xml"):
        routes[f"{BASE}{candidate}"] = html_response(html)
    deps, _ = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.HTML
    assert result.reason_code == "html_page"


def test_403_is_blocked_and_requests_the_url_once(fixtures_dir, clock):
    routes = {
        BASE: html_response("<html></html>"),
        f"{BASE}rates.xml": Resp(403, {"content-type": "text/plain"}, b"denied"),
    }
    deps, transport = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.BLOCKED
    assert result.reason_code == "http_403"
    assert transport.count(f"{BASE}rates.xml") == 1


def test_cloudflare_challenge_is_blocked(fixtures_dir, clock):
    challenge = (fixtures_dir / "cloudflare_challenge.html").read_text(encoding="utf-8")
    routes = {
        BASE: html_response(challenge),
        f"{BASE}rates.xml": html_response(challenge),
    }
    deps, transport = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.BLOCKED
    assert result.reason_code == "challenge_cloudflare"
    assert transport.count(f"{BASE}rates.xml") == 1


def test_redirect_to_feed_is_ok(fixtures_dir, clock):
    body = (fixtures_dir / "feed_own.xml").read_bytes()
    routes = {
        BASE: html_response('<a href="/rates.xml">rates</a>'),
        f"{BASE}rates.xml": Resp(302, {"location": "/export/rates.xml"}, b""),
        f"{BASE}export/rates.xml": Resp(200, {"content-type": "application/xml"}, body),
    }
    deps, _ = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.OK
    assert result.final_url == f"{BASE}export/rates.xml"


def test_xml_without_rates_is_error_and_format_unknown(fixtures_dir, clock):
    empty = (fixtures_dir / "feed_no_rates.xml").read_bytes()
    routes = {
        BASE: html_response('<a href="/feed.xml">feed</a>'),
        f"{BASE}feed.xml": Resp(200, {"content-type": "application/xml"}, empty),
    }
    deps, _ = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.ERROR
    assert result.reason_code == "no_rates_in_feed"
    assert result.format.value == "unknown"


def test_broken_xml_is_error(fixtures_dir, clock):
    broken = (fixtures_dir / "feed_broken.xml").read_bytes()
    routes = {
        BASE: html_response('<a href="/feed.xml">feed</a>'),
        f"{BASE}feed.xml": Resp(200, {"content-type": "application/xml"}, broken),
    }
    deps, _ = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.ERROR
    assert result.reason_code == "invalid_xml"


def test_cp1251_feed_is_decoded(read_fixture_bytes, clock):
    body = read_fixture_bytes("feed_cp1251.xml")
    routes = {
        BASE: html_response('<a href="/feed.xml">feed</a>'),
        f"{BASE}feed.xml": Resp(200, {"content-type": "application/xml; charset=windows-1251"}, body),
    }
    deps, _ = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.OK
    assert result.encoding == "windows-1251"


def test_gzip_feed_is_reported(read_fixture, clock):
    text = read_fixture("feed_own.xml")
    routes = {
        BASE: html_response('<a href="/feed.xml">feed</a>'),
        f"{BASE}feed.xml": gzip_response(text),
    }
    deps, _ = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.OK
    assert result.gzip is True
    assert result.size_bytes == len(text.encode("utf-8"))


def test_timeout_keeps_run_alive(fixtures_dir, clock):
    def handler(request):
        raise httpx.TimeoutException("timed out", request=request)

    routes = {BASE: html_response("<html></html>"), f"{BASE}rates.xml": handler}
    deps, _ = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.ERROR
    assert result.reason_code == "timeout"


def test_sitemap_is_used_lazily(fixtures_dir, clock):
    sitemap = (
        "<urlset><url><loc>https://a.test/feeds/all.xml</loc></url></urlset>"
    )
    body = (fixtures_dir / "feed_own.xml").read_bytes()
    routes = {
        BASE: html_response("<html></html>"),
        f"{BASE}sitemap.xml": Resp(200, {"content-type": "application/xml"}, sitemap.encode()),
        f"{BASE}feeds/all.xml": Resp(200, {"content-type": "application/xml"}, body),
    }
    deps, transport = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.OK
    assert result.final_url == f"{BASE}feeds/all.xml"
    assert transport.count(f"{BASE}sitemap.xml") == 1


def test_sitemap_is_not_fetched_when_a_path_works(fixtures_dir, clock):
    routes = {BASE: html_response("<html></html>"), **feed_route(fixtures_dir, url=f"{BASE}rates.xml")}
    deps, transport = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.OK
    assert transport.count(f"{BASE}sitemap.xml") == 0


def test_robots_disallow_is_not_found(clock):
    routes = {f"{BASE}robots.txt": Resp(200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /\n")}
    deps, transport = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.NOT_FOUND
    assert result.reason_code == "robots_disallowed"
    assert transport.count(BASE) == 0


def test_robots_refusal_blocks_the_domain(clock):
    routes = {f"{BASE}robots.txt": Resp(403, {"content-type": "text/plain"}, b"no")}
    deps, _ = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.BLOCKED


def test_robots_unavailable_is_error(clock):
    routes = {f"{BASE}robots.txt": Resp(500, {"content-type": "text/plain"}, b"oops")}
    deps, _ = make_deps(routes, clock)
    result = check_domain(row(), deps)
    assert result.status is Status.ERROR
    assert result.reason_code == "robots_unavailable"


def test_run_processes_every_domain_and_sorts_them(fixtures_dir, clock):
    routes = {
        "https://b.test/": html_response("<html></html>"),
        **feed_route(fixtures_dir, url="https://a.test/rates.xml"),
        "https://a.test/": html_response("<html></html>"),
    }
    deps, _ = make_deps(routes, clock, domains=("a.test", "b.test"))
    rows = [row("b.test", "B"), row("a.test", "A")]
    results = run(rows, deps.config, deps)
    assert [item.domain for item in results] == ["a.test", "b.test"]
    assert results[0].status is Status.OK
    assert results[1].status is Status.NOT_FOUND


def test_run_limit_is_applied_after_sorting(fixtures_dir, clock):
    deps, _ = make_deps({}, clock, config=RunConfig(contact=None, per_domain_interval=0.0, retries=0, limit=1))
    rows = [row("b.test", "B"), row("a.test", "A")]
    results = run(rows, deps.config, deps)
    assert [item.domain for item in results] == ["a.test"]
