"""Response classification (MYSP-2 §2, ADR-0003, ADR-0004).

Two pure functions live here: ``classify_fetch`` answers "what did we get?"
and ``domain_status`` combines per-candidate observations into one domain
status using the fixed priority ``blocked > html > error > not_found``.
"""

from __future__ import annotations

import re
from typing import Sequence
from urllib.parse import urlparse

from .models import CandidateAttempt, FetchResult, ResponseClass, Status

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HTML_PREFIXES = ("<!doctype html", "<html", "<?xml version=\"1.0\" encoding=\"windows-1251\"?>\n<!doctype html")

# Ordered marker tables. Cloudflare markers come first: a Cloudflare challenge
# page also contains generic JS-challenge markers, and "what blocked us" is
# more useful than "there was a script".
_CLOUDFLARE_MARKERS = (
    "just a moment",
    "attention required",
    "cf-mitigated",
    "ddos protection by cloudflare",
    "challenge-platform",
)
_JS_MARKERS = (
    "checking your browser before accessing",
    "cf-browser-verification",
    "_cf_chl_opt",
    "enable javascript and cookies to continue",
)
_CAPTCHA_MARKERS = ("g-recaptcha", "h-captcha", "hcaptcha", "captcha")
# A captcha widget in a site footer is not a refusal. Only a page that asks a
# human to prove they are human counts as an anti-bot wall.
_HUMAN_CHECK_PHRASES = (
    "not a robot",
    "не робот",
    "verify you are human",
    "human verification",
    "unusual traffic",
    "подтвердите, что вы",
)
_DENIED_TITLE_MARKERS = ("access denied", "доступ ограничен", "are you a robot")

_LOGIN_SEGMENTS = ("login", "signin", "sign-in", "auth", "personal", "cabinet")

_BLOCKED_PRIORITY = (
    "login_required",
    "challenge_cloudflare",
    "challenge_js",
    "challenge_captcha",
    "http_401",
    "http_403",
    "http_451",
    "rate_limited",
    "access_denied_title",
)

_ERROR_PRIORITY = (
    "timeout",
    "dns_error",
    "tls_error",
    "connect_error",
    "read_error",
    "http_5xx",
    "http_4xx",
    "too_large",
    "too_many_redirects",
    "redirect_loop",
    "bad_redirect",
    "unsupported_encoding",
    "invalid_gzip",
    "undecodable",
    "empty_response",
    "robots_unavailable",
    "invalid_feed_structure",
    "invalid_xml",
    "invalid_json",
    "invalid_csv",
    "no_rates_in_feed",
)


def title_of(text: str) -> str | None:
    match = _TITLE_RE.search(text or "")
    if not match:
        return None
    title = " ".join(match.group(1).split())
    return title[:120] or None


def blocked_reason(text: str, status: int | None = None) -> str | None:
    """Return a ``blocked`` reason code if the body is an anti-bot refusal.

    ``404``/``410`` are excluded on purpose: error pages of ordinary sites embed
    analytics, captcha widgets and Cloudflare scripts, which says nothing about
    the server refusing us. The URL simply does not exist.
    """

    lowered = (text or "").lower()
    if not lowered:
        return None
    if status in (404, 410):
        return None
    title = (title_of(text) or "").lower()

    cloudflare = any(marker in title for marker in _CLOUDFLARE_MARKERS[:2]) or any(
        marker in lowered for marker in _CLOUDFLARE_MARKERS[2:]
    )
    if not cloudflare and status in (403, 503) and "challenge-platform" in lowered:
        cloudflare = True
    if cloudflare:
        return "challenge_cloudflare"
    if any(marker in lowered for marker in _JS_MARKERS):
        return "challenge_js"
    if any(marker in lowered for marker in _CAPTCHA_MARKERS) and any(
        phrase in lowered for phrase in _HUMAN_CHECK_PHRASES
    ):
        return "challenge_captcha"
    if any(marker in title for marker in _DENIED_TITLE_MARKERS):
        return "access_denied_title"
    return None


def looks_blocked_text(text: str, status: int | None = None) -> bool:
    return blocked_reason(text, status) is not None


def blocked_evidence(text: str, status: int | None = None) -> str | None:
    """The marker that triggered ``blocked``, for ``reason_detail``."""

    lowered = (text or "").lower()
    if not lowered or status in (404, 410):
        return None
    title = (title_of(text) or "").lower()
    for marker in _CLOUDFLARE_MARKERS:
        if marker in title or marker in lowered:
            return marker
    for marker in _JS_MARKERS:
        if marker in lowered:
            return marker
    for marker in _CAPTCHA_MARKERS:
        if marker in lowered:
            return marker
    for marker in _DENIED_TITLE_MARKERS:
        if marker in title:
            return marker
    return None


def is_html(text: str | None, content_type: str | None) -> bool:
    if content_type in ("text/html", "application/xhtml+xml"):
        return True
    head = (text or "").lstrip()[:200].lower()
    return head.startswith(("<!doctype html", "<html"))


def classify_fetch(result: FetchResult) -> tuple[ResponseClass, str]:
    if result.error is not None:
        return ResponseClass.TRANSPORT_ERROR, _error_code(result.error)

    status = result.status
    text = result.text
    header_auth = (result.headers.get("www-authenticate") or "").strip()

    if header_auth:
        return ResponseClass.BLOCKED, "login_required"

    if status in (404, 410):
        return ResponseClass.NOT_FOUND, f"http_{status}"

    marker = blocked_reason(text or "", status)
    if marker is not None:
        return ResponseClass.BLOCKED, marker

    if _ends_at_login(result.final_url):
        return ResponseClass.BLOCKED, "login_required"

    if status == 401:
        return ResponseClass.BLOCKED, "http_401"
    if status == 403:
        return ResponseClass.BLOCKED, "http_403"
    if status == 429:
        return ResponseClass.BLOCKED, "rate_limited"
    if status == 451:
        return ResponseClass.BLOCKED, "http_451"
    if status is not None and 400 <= status < 500:
        return ResponseClass.TRANSPORT_ERROR, "http_4xx"
    if status is not None and 500 <= status < 600:
        return ResponseClass.TRANSPORT_ERROR, "http_5xx"

    if text is None:
        return ResponseClass.TRANSPORT_ERROR, "undecodable"
    if not text.strip():
        return ResponseClass.EMPTY, "empty_response"
    if is_html(text, result.content_type):
        return ResponseClass.HTML, "html_page"
    return ResponseClass.PAYLOAD, "found"


def domain_status(attempts: Sequence[CandidateAttempt]) -> tuple[Status, str, str]:
    if not attempts:
        return Status.NOT_FOUND, "no_candidates", "не нашлось ни одного кандидата на фид"

    blocked = [item for item in attempts if item.response_class == ResponseClass.BLOCKED]
    if blocked:
        chosen = _pick(blocked, _BLOCKED_PRIORITY)
        return Status.BLOCKED, chosen.reason_code or "http_403", _detail(chosen)

    html = [item for item in attempts if item.response_class == ResponseClass.HTML]
    if html:
        chosen = html[0]
        return Status.HTML, "html_page", _detail(chosen)

    errored = [
        item
        for item in attempts
        if item.response_class in (ResponseClass.TRANSPORT_ERROR, ResponseClass.EMPTY)
    ]
    if errored:
        chosen = _pick(errored, _ERROR_PRIORITY)
        return Status.ERROR, chosen.reason_code or "read_error", _detail(chosen)

    not_found = [
        item
        for item in attempts
        if item.response_class == ResponseClass.NOT_FOUND
    ]
    if not_found:
        chosen = _pick(not_found, ("robots_disallowed", "http_404", "http_410", "no_candidates"))
        return Status.NOT_FOUND, chosen.reason_code or "http_404", _detail(chosen)

    return Status.NOT_FOUND, "no_candidates", "не нашлось ни одного кандидата на фид"


def _pick(items: Sequence[CandidateAttempt], priority: Sequence[str]) -> CandidateAttempt:
    for reason in priority:
        for item in items:
            if item.reason_code == reason:
                return item
    return items[0]


def _detail(attempt: CandidateAttempt) -> str:
    if attempt.reason_detail:
        return attempt.reason_detail[:200]
    if attempt.http_status is not None:
        return f"HTTP {attempt.http_status}"
    return attempt.reason_code or "unknown"


def _ends_at_login(url: str) -> bool:
    segments = [segment for segment in urlparse(url).path.lower().split("/") if segment]
    return any(segment in _LOGIN_SEGMENTS for segment in segments)


def _error_code(code: str) -> str:
    mapping = {
        "too_large": "too_large",
        "too_many_redirects": "too_many_redirects",
        "redirect_loop": "redirect_loop",
        "bad_redirect": "bad_redirect",
        "unsupported_encoding": "unsupported_encoding",
        "invalid_gzip": "invalid_gzip",
        "undecodable": "undecodable",
        "timeout": "timeout",
        "dns_error": "dns_error",
        "tls_error": "tls_error",
        "connect_error": "connect_error",
        "read_error": "read_error",
    }
    return mapping.get(code, code)
