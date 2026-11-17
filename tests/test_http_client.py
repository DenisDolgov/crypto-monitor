"""Polite HTTP client: retries, redirects, limits, decoding (AC-18…AC-27)."""

from __future__ import annotations

import gzip
import socket

import httpx
import pytest

from feedscan.config import RunConfig
from feedscan.http_client import HttpClient
from tests.conftest import FakeClock, FakeTransport, Resp, gzip_response


def make_client(transport, clock, *, config: RunConfig | None = None, domains=()) -> HttpClient:
    config = config or RunConfig(contact="ops@example.test", per_domain_interval=1.0)
    return HttpClient(config, transport=transport, clock=clock, domain_keys=domains)


def test_plain_xml_response_is_read(fixtures_dir, clock):
    transport = FakeTransport(
        {
            "https://a.test/rates.xml": httpx.Response(
                200,
                content=(fixtures_dir / "feed_own.xml").read_bytes(),
                headers={"content-type": "application/xml; charset=utf-8"},
            )
        },
        clock=clock,
    )
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.status == 200
    assert result.error is None
    assert result.text is not None and "<from>USDT</from>" in result.text
    assert result.encoding == "utf-8"
    assert result.gzip is False
    assert result.size_bytes == len((fixtures_dir / "feed_own.xml").read_bytes())
    assert result.attempts == 1


def test_404_is_not_retried(clock):
    transport = FakeTransport({"https://a.test/rates.xml": httpx.Response(404, text="nope")}, clock=clock)
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.status == 404
    assert result.attempts == 1


@pytest.mark.parametrize("status", [401, 403, 429, 451])
def test_blocked_statuses_are_not_retried(status, clock):
    transport = FakeTransport(
        {"https://a.test/rates.xml": httpx.Response(status, text="denied")}, clock=clock
    )
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.status == status
    assert result.attempts == 1


def test_5xx_is_retried_twice_with_backoff(clock):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="try later")
        return httpx.Response(200, text="<rates/>", headers={"content-type": "application/xml"})

    transport = FakeTransport({"https://a.test/rates.xml": handler}, clock=clock)
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.status == 200
    assert result.attempts == 3
    assert len(clock.sleeps) >= 2
    assert clock.sleeps[0] >= 1.0
    assert sum(clock.sleeps[1:]) or clock.sleeps[1] >= 1.0


def test_5xx_after_retries_keeps_status_and_attempt_count(clock):
    transport = FakeTransport(
        {"https://a.test/rates.xml": httpx.Response(500, text="boom")}, clock=clock
    )
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.status == 500
    assert result.error is None
    assert result.attempts == 3


def test_timeout_is_retried_and_reported(clock):
    def handler(request):
        raise httpx.TimeoutException("timed out", request=request)

    transport = FakeTransport({"https://a.test/rates.xml": handler}, clock=clock)
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "timeout"
    assert result.status is None
    assert result.attempts == 3


def test_dns_error_is_reported_without_retries(clock):
    # A name that does not resolve will not start resolving because we asked
    # again: the retry list (ADR-0003) covers timeouts, connection, TLS and
    # read errors, but not DNS.
    def handler(request):
        try:
            raise socket.gaierror(8, "nodename nor servname provided")
        except socket.gaierror as exc:
            raise httpx.ConnectError("dns failure", request=request) from exc

    transport = FakeTransport({"https://a.test/rates.xml": handler}, clock=clock)
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "dns_error"
    assert result.attempts == 1


def test_gzip_is_decompressed_and_sizes_are_reported(clock):
    payload = "<?xml version='1.0'?><rates><rate><from>USDT</from><to>RUB</to><rate>92</rate></rate></rates>"
    transport = FakeTransport({"https://a.test/rates.xml": gzip_response(payload)}, clock=clock)
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.gzip is True
    assert result.encoding == "utf-8"
    assert result.size_bytes == len(payload.encode("utf-8"))
    assert result.compressed_bytes < result.size_bytes
    assert "<rate>92</rate>" in result.text


def test_compressed_body_over_limit_is_rejected(clock):
    # Incompressible payload: the *compressed* size is what exceeds the limit.
    import os

    payload = gzip.compress(os.urandom(4096))
    config = RunConfig(contact="ops@example.test", per_domain_interval=0.0, max_bytes=1024, max_body_bytes=10 * 1024 * 1024)
    transport = FakeTransport(
        {
            "https://a.test/rates.xml": httpx.Response(
                200, content=payload, headers={"content-encoding": "gzip"}
            )
        },
        clock=clock,
    )
    result = make_client(transport, clock, config=config).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "too_large"
    assert result.body is None
    assert result.size_bytes == 0
    assert result.attempts == 1


def test_decompressed_body_over_limit_is_rejected(clock):
    bomb = "<rates>" + ("x" * 200000) + "</rates>"
    config = RunConfig(contact="ops@example.test", per_domain_interval=0.0, max_bytes=10 * 1024 * 1024, max_body_bytes=1024)
    transport = FakeTransport({"https://a.test/rates.xml": gzip_response(bomb)}, clock=clock)
    result = make_client(transport, clock, config=config).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "too_large"
    assert result.body is None


def test_unsupported_content_encoding_is_rejected(clock):
    transport = FakeTransport(
        {
            "https://a.test/rates.xml": httpx.Response(
                200, content=b"\x00", headers={"content-encoding": "br"}
            )
        },
        clock=clock,
    )
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "unsupported_encoding"
    assert result.attempts == 1


def test_invalid_gzip_is_rejected(clock):
    transport = FakeTransport(
        {"https://a.test/rates.xml": Resp(200, {"content-encoding": "gzip"}, b"not-gzip-at-all")},
        clock=clock,
    )
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "invalid_gzip"


def test_single_redirect_is_followed(clock):
    transport = FakeTransport(
        {
            "https://a.test/rates.xml": httpx.Response(302, headers={"location": "/export/rates.xml"}),
            "https://a.test/export/rates.xml": httpx.Response(
                200, text="<rates><item/></rates>", headers={"content-type": "application/xml"}
            ),
        },
        clock=clock,
    )
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.status == 200
    assert result.final_url == "https://a.test/export/rates.xml"
    assert result.redirect_count == 1
    assert result.attempts == 2


def test_redirect_loop_is_reported(clock):
    transport = FakeTransport(
        {
            "https://a.test/rates.xml": httpx.Response(301, headers={"location": "https://a.test/export/rates.xml"}),
            "https://a.test/export/rates.xml": httpx.Response(301, headers={"location": "https://a.test/rates.xml"}),
        },
        clock=clock,
    )
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "redirect_loop"


def test_too_many_redirects_is_reported(clock):
    routes = {
        "https://a.test/rates.xml": httpx.Response(302, headers={"location": "/r1"}),
        "https://a.test/r1": httpx.Response(302, headers={"location": "/r2"}),
        "https://a.test/r2": httpx.Response(302, headers={"location": "/r3"}),
        "https://a.test/r3": httpx.Response(302, headers={"location": "/r4"}),
        "https://a.test/r4": httpx.Response(200, text="<rates/>"),
    }
    result = make_client(FakeTransport(routes, clock=clock), clock).get(
        "https://a.test/rates.xml", purpose="candidate"
    )
    assert result.error == "too_many_redirects"


def test_cross_domain_redirect_warns(clock):
    transport = FakeTransport(
        {
            "https://a.test/rates.xml": httpx.Response(302, headers={"location": "https://cdn.test/rates.xml"}),
            "https://cdn.test/rates.xml": httpx.Response(
                200, text="<rates/>", headers={"content-type": "application/xml"}
            ),
        },
        clock=clock,
    )
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.status == 200
    assert "cross_domain_redirect" in result.warnings


def test_bad_redirect_scheme_is_rejected(clock):
    transport = FakeTransport(
        {"https://a.test/rates.xml": httpx.Response(302, headers={"location": "ftp://a.test/rates.xml"})},
        clock=clock,
    )
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "bad_redirect"


def test_user_agent_carries_contact(clock):
    transport = FakeTransport({"https://a.test/rates.xml": httpx.Response(200, text="<rates/>")}, clock=clock)
    make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    ua = transport.requests[0].headers.get("user-agent", "")
    assert ua.startswith("feedscan/")
    assert "ops@example.test" in ua
    assert "Mozilla" not in ua


def test_user_agent_without_contact_is_not_browser_like(clock):
    config = RunConfig(contact=None, per_domain_interval=0.0)
    transport = FakeTransport({"https://a.test/rates.xml": httpx.Response(200, text="<rates/>")}, clock=clock)
    HttpClient(config, transport=transport, clock=clock).get("https://a.test/rates.xml", purpose="candidate")
    ua = transport.requests[0].headers.get("user-agent", "")
    assert ua == "feedscan/0.1.0"
    assert "Mozilla" not in ua


def test_requests_to_one_domain_are_rate_limited_including_subdomains(clock):
    transport = FakeTransport(
        {
            "https://a.test/": httpx.Response(200, text="<html/>", headers={"content-type": "text/html"}),
            "https://www.a.test/": httpx.Response(200, text="<html/>", headers={"content-type": "text/html"}),
            "https://a.test/rates.xml": httpx.Response(200, text="<rates/>"),
        },
        clock=clock,
    )
    client = make_client(transport, clock, domains=("a.test",))
    client.get("https://a.test/", purpose="homepage")
    client.get("https://www.a.test/", purpose="homepage")
    client.get("https://a.test/rates.xml", purpose="candidate")
    times = [request.at for request in transport.requests]
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    assert all(gap >= 1.0 for gap in gaps), gaps


def test_different_domains_are_not_delayed(clock):
    transport = FakeTransport(
        {
            "https://a.test/rates.xml": httpx.Response(200, text="<rates/>"),
            "https://b.test/rates.xml": httpx.Response(200, text="<rates/>"),
        },
        clock=clock,
    )
    client = make_client(transport, clock, domains=("a.test", "b.test"))
    client.get("https://a.test/rates.xml", purpose="candidate")
    client.get("https://b.test/rates.xml", purpose="candidate")
    assert clock.sleeps == []


def test_body_is_not_stored_when_reading_aborts(clock):
    big = b"a" * 4096
    config = RunConfig(contact="ops@example.test", per_domain_interval=0.0, max_bytes=1024)
    transport = FakeTransport({"https://a.test/rates.xml": httpx.Response(200, content=big)}, clock=clock)
    result = make_client(transport, clock, config=config).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "too_large"
    assert result.body is None
    assert result.text is None


def test_connect_error_is_reported(clock):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    transport = FakeTransport({"https://a.test/rates.xml": handler}, clock=clock)
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "connect_error"


def test_tls_error_is_reported(clock):
    def handler(request):
        import ssl

        try:
            raise ssl.SSLError("handshake failure")
        except ssl.SSLError as exc:
            raise httpx.ConnectError("tls failure", request=request) from exc

    transport = FakeTransport({"https://a.test/rates.xml": handler}, clock=clock)
    result = make_client(transport, clock).get("https://a.test/rates.xml", purpose="candidate")
    assert result.error == "tls_error"
