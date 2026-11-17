"""Candidate discovery: homepage links, well-known paths, sitemap (ADR-0003)."""

from __future__ import annotations

from feedscan.discovery import (
    candidate_cap,
    dedupe_candidates,
    homepage_candidates,
    normalize_url,
    sitemap_candidates,
    well_known_candidates,
)
from feedscan.models import CandidateOrigin

DOMAIN = "exchanger-a.test"


def test_homepage_links_are_extracted_in_document_order(read_fixture):
    html = read_fixture("homepage_with_feed_link.html")
    candidates = homepage_candidates(html, f"https://{DOMAIN}/", DOMAIN)
    urls = [candidate.url for candidate in candidates]
    assert urls == [
        f"https://{DOMAIN}/feed.xml",
        f"https://{DOMAIN}/api/rates.xml",
    ]
    assert all(candidate.origin is CandidateOrigin.HOMEPAGE_LINK for candidate in candidates)


def test_offsite_links_are_dropped(read_fixture):
    html = read_fixture("homepage_with_feed_link.html")
    urls = [c.url for c in homepage_candidates(html, f"https://{DOMAIN}/", DOMAIN)]
    assert not any("elsewhere.example" in url for url in urls)


def test_homepage_without_feed_links_yields_nothing(read_fixture):
    html = read_fixture("homepage_plain.html")
    assert homepage_candidates(html, f"https://{DOMAIN}/", DOMAIN) == []


def test_subdomain_links_are_allowed():
    html = '<a href="https://cdn.exchanger-a.test/rates.xml">feed</a>'
    urls = [c.url for c in homepage_candidates(html, f"https://{DOMAIN}/", DOMAIN)]
    assert urls == ["https://cdn.exchanger-a.test/rates.xml"]


def test_homepage_candidate_limit_is_ten():
    html = "".join(f'<a href="/feed-{index}.xml">feed</a>' for index in range(15))
    assert len(homepage_candidates(html, f"https://{DOMAIN}/", DOMAIN)) == 10


def test_well_known_paths_are_fixed_and_ordered():
    candidates = well_known_candidates(DOMAIN)
    assert [candidate.url for candidate in candidates] == [
        f"https://{DOMAIN}/rates.xml",
        f"https://{DOMAIN}/export/rates.xml",
        f"https://{DOMAIN}/xml/rates.xml",
        f"https://{DOMAIN}/bestchange.xml",
    ]
    assert {candidate.origin for candidate in candidates} == {CandidateOrigin.WELL_KNOWN}


def test_sitemap_yields_only_local_xml_files(read_fixture):
    xml = read_fixture("sitemap.xml")
    candidates = sitemap_candidates(xml, f"https://{DOMAIN}/", DOMAIN)
    assert [candidate.url for candidate in candidates] == [f"https://{DOMAIN}/export/rates.xml"]
    assert candidates[0].origin is CandidateOrigin.SITEMAP


def test_sitemap_index_is_not_returned_as_candidate():
    xml = (
        "<urlset>"
        "<url><loc>https://a.test/sitemap-rates.xml</loc></url>"
        "<url><loc>https://a.test/feed/rates.xml</loc></url>"
        "</urlset>"
    )
    urls = [candidate.url for candidate in sitemap_candidates(xml, "https://a.test/", "a.test")]
    assert urls == ["https://a.test/feed/rates.xml"]


def test_sitemap_ignores_broken_xml():
    assert sitemap_candidates("<urlset><url>", f"https://{DOMAIN}/", DOMAIN) == []


def test_sitemap_subindexes_are_never_candidates():
    # A sitemap index lists sub-sitemaps of pages; treating them as feeds burns
    # the candidate budget and turns "no feed" into a misleading error.
    xml = (
        "<sitemapindex>"
        "<sitemap><loc>https://a.test/post-sitemap.xml</loc></sitemap>"
        "<sitemap><loc>https://a.test/page-sitemap.xml</loc></sitemap>"
        "<sitemap><loc>https://a.test/sitemap-rates.xml</loc></sitemap>"
        "</sitemapindex>"
    )
    assert sitemap_candidates(xml, "https://a.test/", "a.test") == []


def test_duplicates_are_removed_and_order_kept():
    candidates = well_known_candidates(DOMAIN)
    doubled = candidates + [candidates[0]]
    result = dedupe_candidates(doubled)
    assert [candidate.url for candidate in result] == [candidate.url for candidate in candidates]
    assert [candidate.order for candidate in result] == list(range(len(result)))


def test_candidate_cap_truncates_and_reports():
    candidates = well_known_candidates(DOMAIN) + well_known_candidates("b.test")
    capped, hit_cap = candidate_cap(candidates, limit=5)
    assert len(capped) == 5
    assert hit_cap is True

    capped, hit_cap = candidate_cap(candidates, limit=25)
    assert len(capped) == 8
    assert hit_cap is False


def test_url_normalisation():
    assert normalize_url("HTTPS://A.TEST:443/rates.xml#top") == "https://a.test/rates.xml"
    assert normalize_url("http://a.test:80/rates.xml?x=1") == "http://a.test/rates.xml?x=1"
