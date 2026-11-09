import pytest

from archive.urls import (UrlError, check_http_url, host_of, identity_key, parse_wayback,
                          source_id_for, wayback_raw_url)


@pytest.mark.parametrize("a, b", [
    ("https://www.Example.org/page/", "http://example.org/page"),
    ("https://example.org", "https://example.org/"),
    ("https://example.org:443/a", "http://example.org:80/a"),
    ("https://example.org/a#section", "https://example.org/a"),
    ("https://example.org/a?utm_source=x&id=3&fbclid=y", "https://example.org/a?id=3"),
])
def test_same_identity(a, b):
    assert identity_key(a) == identity_key(b)
    assert source_id_for(a) == source_id_for(b)


@pytest.mark.parametrize("a, b", [
    ("https://example.org/A", "https://example.org/a"),          # path case kept
    ("https://example.org/a?id=3", "https://example.org/a?id=4"),
    ("https://example.org:8080/a", "https://example.org/a"),
    ("https://sub.example.org/a", "https://example.org/a"),
])
def test_different_identity(a, b):
    assert identity_key(a) != identity_key(b)


@pytest.mark.parametrize("url", ["ftp://x.org/f", "mailto:a@b.org", "https:///nohost",
                                 "https://user:pw@example.org/", "not a url", ""])
def test_rejects_bad_urls(url):
    with pytest.raises(UrlError):
        check_http_url(url)


def test_host_of():
    assert host_of("https://WWW.Exodus.to/x") == "exodus.to"


def test_parse_full_capture():
    ref = parse_wayback("https://web.archive.org/web/20081218053759/http://www.narth.com/")
    assert ref.original_url == "http://www.narth.com/"
    assert ref.timestamp == "20081218053759"
    assert ref.kind == "capture"
    assert ref.captured_date == "2008-12-18T05:37:59Z"
    assert ref.captured_certainty == "exact"
    assert wayback_raw_url(ref) == "https://web.archive.org/web/20081218053759id_/http://www.narth.com/"


def test_parse_modifier_and_partial_stamp():
    ref = parse_wayback("http://web.archive.org/web/200812if_/https://a.org/x?y=1")
    assert ref.modifier == "if_" and ref.timestamp == "200812"
    assert ref.original_url == "https://a.org/x?y=1"
    assert ref.captured_date == "2008-12" and ref.captured_certainty == "approximate"


def test_parse_odd_forms():
    short = parse_wayback("https://web.archive.org/web/2/http://www.oneby1.org/")
    assert short.timestamp == "2" and short.captured_date is None
    sitemap = parse_wayback("https://web.archive.org/web/sitemap/https://loveinaction.org/")
    assert sitemap.kind == "sitemap" and sitemap.original_url == "https://loveinaction.org/"
    with pytest.raises(UrlError):
        wayback_raw_url(sitemap)
    bare = parse_wayback("https://web.archive.org/web/20010101000000/www.a.org/p")
    assert bare.original_url == "http://www.a.org/p"
    one_slash = parse_wayback("https://web.archive.org/web/20010101000000/http:/a.org/p")
    assert one_slash.original_url == "http://a.org/p"


def test_not_wayback():
    assert parse_wayback("https://example.org/web/2008/http://x.org") is None
    with pytest.raises(UrlError):
        parse_wayback("https://web.archive.org/web/abc/http://x.org/")
