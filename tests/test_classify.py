"""Status classification and priority rules (AC-13, AC-14, AC-15)."""

from __future__ import annotations

import pytest

from feedscan.classify import (
    blocked_reason,
    classify_fetch,
    domain_status,
    is_html,
    title_of,
)
from feedscan.models import (
    CandidateAttempt,
    CandidateOrigin,
    FetchResult,
    ResponseClass,
    Status,
)


def fetch(**overrides) -> FetchResult:
    data = dict(
        requested_url="https://a.test/rates.xml",
        final_url="https://a.test/rates.xml",
        status=200,
        headers={"content-type": "application/xml"},
        body=b"<rates/>",
        text="<rates/>",
        encoding="utf-8",
        gzip=False,
        size_bytes=9,
        compressed_bytes=9,
        redirect_count=0,
    )
    data.update(overrides)
    return FetchResult(**data)


def attempt(url="https://a.test/rates.xml", cls=ResponseClass.PAYLOAD, reason="found", status=200, detail=None):
    return CandidateAttempt(
        url=url,
        origin=CandidateOrigin.WELL_KNOWN,
        http_status=status,
        response_class=cls,
        reason_code=reason,
        reason_detail=detail,
        size_bytes=10,
        duration_ms=1,
    )


def test_xml_payload_is_payload():
    assert classify_fetch(fetch()) == (ResponseClass.PAYLOAD, "found")


def test_html_page_is_html_not_blocked(fixtures_dir):
    text = (fixtures_dir / "html_page.html").read_text(encoding="utf-8")
    cls, reason = classify_fetch(fetch(text=text, headers={"content-type": "text/html"}, body=text.encode()))
    assert (cls, reason) == (ResponseClass.HTML, "html_page")


def test_cloudflare_challenge_is_blocked_with_its_own_reason(fixtures_dir):
    text = (fixtures_dir / "cloudflare_challenge.html").read_text(encoding="utf-8")
    cls, reason = classify_fetch(fetch(status=200, text=text, headers={"content-type": "text/html"}))
    assert cls == ResponseClass.BLOCKED
    assert reason == "challenge_cloudflare"


def test_captcha_page_is_blocked(fixtures_dir):
    text = (fixtures_dir / "captcha.html").read_text(encoding="utf-8")
    cls, reason = classify_fetch(fetch(status=200, text=text, headers={"content-type": "text/html"}))
    assert (cls, reason) == (ResponseClass.BLOCKED, "challenge_captcha")


@pytest.mark.parametrize(
    "status,reason",
    [(401, "http_401"), (403, "http_403"), (429, "rate_limited"), (451, "http_451")],
)
def test_access_statuses_are_blocked(status, reason):
    assert classify_fetch(fetch(status=status, text="denied")) == (ResponseClass.BLOCKED, reason)


def test_www_authenticate_means_login_required():
    result = fetch(status=401, headers={"www-authenticate": "Basic"}, text="auth")
    assert classify_fetch(result) == (ResponseClass.BLOCKED, "login_required")


def test_redirect_to_login_means_login_required():
    result = fetch(final_url="https://a.test/personal/rates", text="<html></html>", headers={"content-type": "text/html"})
    assert classify_fetch(result) == (ResponseClass.BLOCKED, "login_required")


def test_404_is_not_found():
    assert classify_fetch(fetch(status=404, text="nope")) == (ResponseClass.NOT_FOUND, "http_404")


def test_empty_body_is_empty_response():
    assert classify_fetch(fetch(text="", body=b"")) == (ResponseClass.EMPTY, "empty_response")
    assert classify_fetch(fetch(text="  \n\t ")) == (ResponseClass.EMPTY, "empty_response")


def test_transport_error_keeps_its_code():
    assert classify_fetch(fetch(status=None, text=None, body=None, error="timeout")) == (
        ResponseClass.TRANSPORT_ERROR,
        "timeout",
    )
    assert classify_fetch(fetch(error="too_large")) == (ResponseClass.TRANSPORT_ERROR, "too_large")


def test_five_hundred_is_error_not_blocked():
    assert classify_fetch(fetch(status=500, text="boom")) == (ResponseClass.TRANSPORT_ERROR, "http_5xx")


def test_undecodable_body_is_error():
    assert classify_fetch(fetch(text=None, body=b"\x98")) == (ResponseClass.TRANSPORT_ERROR, "undecodable")


def test_404_page_with_captcha_widget_is_not_a_refusal():
    text = (
        '<html><head><title>Page not found</title></head><body>'
        '<script src="https://www.google.com/recaptcha/api.js"></script>'
        "<div class=\"g-recaptcha\"></div>"
        "<p>Sorry, this page does not exist.</p></body></html>"
    )
    assert classify_fetch(fetch(status=404, text=text)) == (ResponseClass.NOT_FOUND, "http_404")


def test_incidental_captcha_widget_on_a_content_page_is_not_a_refusal():
    text = (
        '<html><head><title>Exchanger</title></head><body>'
        "<h1>Обмен валют</h1><p>Курсы и заявки.</p>"
        "<form action=\"/feedback\"><div class=\"g-recaptcha\"></div></form>"
        "</body></html>"
    )
    assert classify_fetch(fetch(text=text)) == (ResponseClass.HTML, "html_page")


def test_domain_status_priority_blocked_beats_html():
    status, reason, detail = domain_status(
        [
            attempt(cls=ResponseClass.NOT_FOUND, reason="http_404", status=404),
            attempt(cls=ResponseClass.HTML, reason="html_page"),
            attempt(cls=ResponseClass.BLOCKED, reason="http_403", status=403, detail='HTTP 403'),
        ]
    )
    assert (status, reason) == (Status.BLOCKED, "http_403")
    assert "403" in detail


def test_domain_status_priority_html_beats_error():
    status, reason, _ = domain_status(
        [
            attempt(cls=ResponseClass.TRANSPORT_ERROR, reason="timeout", status=None),
            attempt(cls=ResponseClass.HTML, reason="html_page"),
        ]
    )
    assert (status, reason) == (Status.HTML, "html_page")


def test_domain_status_priority_error_beats_not_found():
    status, reason, _ = domain_status(
        [
            attempt(cls=ResponseClass.NOT_FOUND, reason="http_404", status=404),
            attempt(cls=ResponseClass.TRANSPORT_ERROR, reason="timeout", status=None),
        ]
    )
    assert (status, reason) == (Status.ERROR, "timeout")


def test_domain_status_not_found_when_everything_is_404():
    status, reason, _ = domain_status(
        [attempt(cls=ResponseClass.NOT_FOUND, reason="http_404", status=404)]
    )
    assert (status, reason) == (Status.NOT_FOUND, "http_404")


def test_domain_status_prefers_explanatory_blocked_reason():
    status, reason, detail = domain_status(
        [
            attempt(cls=ResponseClass.BLOCKED, reason="http_403", status=403, detail="HTTP 403"),
            attempt(
                cls=ResponseClass.BLOCKED,
                reason="challenge_cloudflare",
                status=200,
                detail='HTTP 200, title="Just a moment"',
            ),
        ]
    )
    assert status == Status.BLOCKED
    assert reason == "challenge_cloudflare"
    assert "Just a moment" in detail


def test_domain_status_without_attempts_is_not_found():
    assert domain_status([]) == (Status.NOT_FOUND, "no_candidates", domain_status([])[2])


def test_robots_disallowed_maps_to_not_found():
    status, reason, _ = domain_status(
        [attempt(cls=ResponseClass.NOT_FOUND, reason="robots_disallowed", status=None)]
    )
    assert (status, reason) == (Status.NOT_FOUND, "robots_disallowed")


def test_html_detection_by_content_type_and_prefix():
    assert is_html("", "text/html")
    assert is_html("  <!DOCTYPE html><html></html>", None)
    assert not is_html("<?xml version='1.0'?><rates/>", "application/xml")


def test_title_and_blocked_markers_ignore_absent_input():
    assert title_of("<html><head><title> Hello   World </title></head></html>") == "Hello World"
    assert title_of("no title here") is None
    assert blocked_reason("") is None
    assert blocked_reason("<rates><item/></rates>") is None
