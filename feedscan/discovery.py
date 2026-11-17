"""Feed candidate discovery (ADR-0003).

Pure functions: they only build candidate lists from documents already
fetched. Order is fixed and part of the report's reproducibility.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from .models import Candidate, CandidateOrigin

HOMEPAGE_LIMIT = 10
SITEMAP_LIMIT = 10
CANDIDATE_LIMIT = 25

WELL_KNOWN_PATHS = ("/rates.xml", "/export/rates.xml", "/xml/rates.xml", "/bestchange.xml")

_TAG_RE = re.compile(r"<(link|a)\b([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(\"[^\"]*\"|'[^']*')")
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)

_XML_TYPES = ("application/xml", "text/xml", "application/rss+xml")
_HREF_HINTS = ("xml", "rates", "feed", "export")
# Both "sitemap.xml" and sub-sitemaps ("post-sitemap.xml", "sitemap-pages.xml")
# are indexes of pages, not rate feeds: they never become candidates.
_SITEMAP_SKIP = re.compile(r"sitemap", re.IGNORECASE)


def homepage_candidates(html: str, base_url: str, domain: str) -> list[Candidate]:
    candidates: list[Candidate] = []

    for match in _TAG_RE.finditer(html or ""):
        tag_name = match.group(1).lower()
        attrs = _attrs(match.group(0))
        if tag_name == "link":
            rel = attrs.get("rel", "").lower()
            if "alternate" not in rel:
                continue
            if attrs.get("type", "").lower() not in _XML_TYPES:
                continue
            href = attrs.get("href")
            if href:
                _add_local(candidates, href, base_url, domain)
            continue

        href = attrs.get("href", "")
        lowered = href.lower()
        if not href or not any(hint in lowered for hint in _HREF_HINTS):
            continue
        _add_local(candidates, href, base_url, domain)

    unique = dedupe_candidates(candidates)
    return unique[:HOMEPAGE_LIMIT]


def well_known_candidates(domain: str) -> list[Candidate]:
    return [
        Candidate(url=f"https://{domain}{path}", origin=CandidateOrigin.WELL_KNOWN, order=index)
        for index, path in enumerate(WELL_KNOWN_PATHS)
    ]


def sitemap_candidates(xml_text: str, base_url: str, domain: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    for raw in _LOC_RE.findall(xml_text or ""):
        url = raw.strip()
        if not url:
            continue
        absolute = urljoin(base_url, url)
        parts = urlsplit(absolute)
        host = (parts.hostname or "").lower()
        if host != domain and not host.endswith("." + domain):
            continue
        if not parts.path.lower().endswith(".xml"):
            continue
        if _SITEMAP_SKIP.search(parts.path.rsplit("/", 1)[-1]):
            continue
        candidates.append(Candidate(url=normalize_url(absolute), origin=CandidateOrigin.SITEMAP))
    unique = dedupe_candidates(candidates)
    return unique[:SITEMAP_LIMIT]


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    result: list[Candidate] = []
    for candidate in candidates:
        normalized = normalize_url(candidate.url)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(
            Candidate(url=normalized, origin=candidate.origin, order=len(result))
        )
    return result


def candidate_cap(candidates: list[Candidate], limit: int = CANDIDATE_LIMIT) -> tuple[list[Candidate], bool]:
    if len(candidates) <= limit:
        return list(candidates), False
    return list(candidates[:limit]), True


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path or ""
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _add_local(candidates: list[Candidate], href: str, base_url: str, domain: str) -> None:
    absolute = urljoin(base_url, href)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        return
    host = (parts.hostname or "").lower()
    if host != domain and not host.endswith("." + domain):
        return
    candidates.append(Candidate(url=absolute, origin=CandidateOrigin.HOMEPAGE_LINK))


def _attrs(tag: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in _ATTR_RE.findall(tag):
        result[name.lower()] = value.strip().strip("\"'")
    return result
