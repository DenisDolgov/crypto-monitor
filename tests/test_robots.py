"""robots.txt gate: allow/deny, crawl-delay, refusal handling (ADR-0003)."""

from __future__ import annotations

from feedscan.config import RunConfig
from feedscan.http_client import HttpClient
from feedscan.robots import RobotsDecision, RobotsGate
from tests.conftest import FakeTransport, Resp


def make_gate(routes, clock, *, config: RunConfig | None = None):
    config = config or RunConfig(contact="ops@example.test", per_domain_interval=0.0)
    transport = FakeTransport(routes, clock=clock)
    client = HttpClient(config, transport=transport, clock=clock)
    return RobotsGate(client, config), transport


def robots(body: str, status: int = 200) -> Resp:
    return Resp(status, {"content-type": "text/plain"}, body.encode("utf-8"))


def test_missing_robots_allows_everything(clock):
    gate, _ = make_gate({"https://a.test/robots.txt": robots("", status=404)}, clock)
    assert gate.check("https://a.test/rates.xml") is RobotsDecision.ALLOWED


def test_disallow_root_blocks_paths(clock):
    gate, _ = make_gate({"https://a.test/robots.txt": robots("User-agent: *\nDisallow: /\n")}, clock)
    assert gate.check("https://a.test/rates.xml") is RobotsDecision.DISALLOWED


def test_longer_allow_wins_over_disallow(clock):
    body = "User-agent: *\nDisallow: /\nAllow: /rates.xml\n"
    gate, _ = make_gate({"https://a.test/robots.txt": robots(body)}, clock)
    assert gate.check("https://a.test/rates.xml") is RobotsDecision.ALLOWED
    assert gate.check("https://a.test/export/rates.xml") is RobotsDecision.DISALLOWED


def test_agent_specific_group_wins_over_star(clock):
    body = (
        "User-agent: *\nDisallow: /\n\n"
        "User-agent: feedscan\nDisallow: /private\n"
    )
    gate, _ = make_gate({"https://a.test/robots.txt": robots(body)}, clock)
    assert gate.check("https://a.test/rates.xml") is RobotsDecision.ALLOWED
    assert gate.check("https://a.test/private/x.xml") is RobotsDecision.DISALLOWED


def test_wildcard_and_end_anchor(clock):
    body = "User-agent: *\nDisallow: /*.xml$\n"
    gate, _ = make_gate({"https://a.test/robots.txt": robots(body)}, clock)
    assert gate.check("https://a.test/rates.xml") is RobotsDecision.DISALLOWED
    assert gate.check("https://a.test/rates.xml?x=1") is RobotsDecision.ALLOWED


def test_refusal_on_robots_blocks_the_domain(clock):
    for status in (401, 403, 429):
        gate, _ = make_gate({"https://a.test/robots.txt": robots("denied", status=status)}, clock)
        assert gate.check("https://a.test/rates.xml") is RobotsDecision.BLOCKED


def test_challenge_on_robots_blocks_the_domain(clock):
    body = "<html><head><title>Just a moment...</title></head><body></body></html>"
    gate, _ = make_gate({"https://a.test/robots.txt": robots(body)}, clock)
    assert gate.check("https://a.test/rates.xml") is RobotsDecision.BLOCKED


def test_server_error_on_robots_is_unavailable(clock):
    gate, _ = make_gate({"https://a.test/robots.txt": robots("oops", status=500)}, clock)
    assert gate.check("https://a.test/rates.xml") is RobotsDecision.UNAVAILABLE


def test_robots_is_fetched_once_per_origin(clock):
    gate, transport = make_gate(
        {"https://a.test/robots.txt": robots("User-agent: *\nDisallow:\n")}, clock
    )
    gate.check("https://a.test/rates.xml")
    gate.check("https://a.test/export/rates.xml")
    assert transport.count("https://a.test/robots.txt") == 1


def test_crawl_delay_is_exposed(clock):
    body = "User-agent: *\nCrawl-delay: 5\nDisallow:\n"
    gate, _ = make_gate({"https://a.test/robots.txt": robots(body)}, clock)
    assert gate.crawl_delay("https://a.test/rates.xml") == 5.0
    assert gate.check("https://a.test/rates.xml") is RobotsDecision.ALLOWED


def test_oversized_robots_is_truncated_and_used(clock):
    body = "User-agent: *\nDisallow: /private\n" + ("# padding\n" * 120_000)
    gate, _ = make_gate({"https://a.test/robots.txt": robots(body)}, clock)
    assert gate.check("https://a.test/private/x") is RobotsDecision.DISALLOWED
