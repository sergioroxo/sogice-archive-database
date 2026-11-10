import json

import httpx
import pytest

from archive.fetch import (USER_AGENT, Fetcher, NotPublic, PublicNetworkBackend, capture_time,
                           check_public_url)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


def make_fetcher(routes, *, robots=None, delay=0.0, **kwargs):
    """routes: url -> httpx.Response or callable(request). robots: host -> Response."""
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if request.url.path == "/robots.txt":
            host = request.url.host
            if robots and host in robots:
                return robots[host]
            return httpx.Response(404)
        reply = routes.get(url)
        if reply is None:
            return httpx.Response(404, text="no route")
        return reply(request) if callable(reply) else reply

    fake = FakeClock()
    fetcher = Fetcher(transport=httpx.MockTransport(handler), delay=delay, host_delays={},
                      sleep=fake.sleep, clock=fake.clock, **kwargs)
    return fetcher, seen, fake


HTML = "<html><body><p>" + "Real article text. " * 50 + "</p></body></html>"


def test_ok_page_and_user_agent():
    agents = []

    def page(request):
        agents.append(request.headers["user-agent"])
        return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)

    fetcher, _, _ = make_fetcher({"https://example.org/a": page})
    result = fetcher.get("https://example.org/a")
    assert result.ok and result.body.startswith(b"<html>")
    assert result.http_status == 200 and result.content_type == "text/html"
    assert agents == [USER_AGENT]


def test_redirects_are_followed_and_rechecked():
    routes = {
        "https://example.org/old": httpx.Response(301, headers={"location": "/new"}),
        "https://example.org/new": httpx.Response(200, headers={"content-type": "text/html"}, text=HTML),
        "https://example.org/evil": httpx.Response(302, headers={"location": "http://127.0.0.1/admin"}),
    }
    fetcher, _, _ = make_fetcher(routes)
    result = fetcher.get("https://example.org/old")
    assert result.ok and result.final_url == "https://example.org/new"
    assert result.redirects == [{"from": "https://example.org/old", "to": "https://example.org/new",
                                 "status": 301}]
    evil = fetcher.get("https://example.org/evil")
    assert evil.outcome == "network_error" and "refused" in evil.error and not evil.transient


@pytest.mark.parametrize("response, outcome, transient", [
    (httpx.Response(404), "not_found", False),
    (httpx.Response(410), "gone", False),
    (httpx.Response(403), "blocked", False),
    (httpx.Response(503), "http_error", True),
    (httpx.Response(429, headers={"retry-after": "7"}), "blocked", True),
    (httpx.Response(418), "http_error", False),
    (httpx.Response(200, headers={"content-type": "text/html"}, text="   "), "empty", False),
])
def test_status_outcomes(response, outcome, transient):
    fetcher, _, _ = make_fetcher({"https://example.org/x": response})
    result = fetcher.get("https://example.org/x")
    assert (result.outcome, result.transient) == (outcome, transient)
    if response.status_code == 429:
        assert result.retry_after == 7.0


def test_challenge_pages_are_blocked_but_long_pages_are_not():
    routes = {
        "https://example.org/cf": httpx.Response(403, headers={"server": "cloudflare"}, text="x"),
        "https://example.org/js": httpx.Response(
            200, headers={"content-type": "text/html"},
            text="<title>Just a moment...</title><p>Checking your browser</p>"),
        "https://example.org/long": httpx.Response(
            200, headers={"content-type": "text/html"},
            text="<p>verify you are human</p>" + "content " * 10_000),
    }
    fetcher, _, _ = make_fetcher(routes)
    cf = fetcher.get("https://example.org/cf")
    assert cf.outcome == "blocked" and cf.challenge == "cloudflare_http:403"
    js = fetcher.get("https://example.org/js")
    assert js.outcome == "blocked" and js.challenge == "blocker_text"
    assert fetcher.get("https://example.org/long").ok


def test_media_and_size_limits():
    routes = {
        "https://example.org/v.mp4": httpx.Response(200, headers={"content-type": "video/mp4"},
                                                    content=b"\x00" * 100),
        "https://example.org/big": httpx.Response(200, headers={"content-type": "application/pdf",
                                                                "content-length": "999999"},
                                                  content=b"%PDF-" + b"0" * 999994),
        "https://example.org/stream": httpx.Response(200, headers={"content-type": "text/html"},
                                                     content=b"a" * 5000),
    }
    fetcher, _, _ = make_fetcher(routes, max_bytes=1000)
    media = fetcher.get("https://example.org/v.mp4")
    assert media.outcome == "unsupported_type" and media.body is None
    assert fetcher.get("https://example.org/big").outcome == "too_large"
    assert fetcher.get("https://example.org/stream").outcome == "too_large"


def test_robots_rules_and_cache():
    routes = {"https://example.org/private/p": httpx.Response(200, text=HTML),
              "https://example.org/public": httpx.Response(200, headers={"content-type": "text/html"},
                                                            text=HTML),
              "https://down.org/p": httpx.Response(200, text=HTML)}
    robots = {"example.org": httpx.Response(200, text="User-agent: *\nDisallow: /private/\n"),
              "down.org": httpx.Response(500)}
    fetcher, seen, _ = make_fetcher(routes, robots=robots)
    assert fetcher.get("https://example.org/private/p").outcome == "robots_disallowed"
    assert fetcher.get("https://example.org/public").ok
    assert seen.count("https://example.org/robots.txt") == 1
    assert fetcher.get("https://down.org/p").outcome == "robots_disallowed"   # 5xx robots
    # robots.txt is not consulted for Wayback routes
    fetcher2, seen2, _ = make_fetcher({"https://web.archive.org/web/2009id_/http://a.org/":
                                       httpx.Response(200, headers={"content-type": "text/html"},
                                                      text=HTML)})
    assert fetcher2.get("https://web.archive.org/web/2009id_/http://a.org/", route="wayback").ok
    assert not any(u.endswith("robots.txt") for u in seen2)


def test_wayback_route_stays_on_archive():
    routes = {"https://web.archive.org/web/2009id_/http://a.org/":
              httpx.Response(302, headers={"location": "https://a.org/"})}
    fetcher, _, _ = make_fetcher(routes)
    result = fetcher.get("https://web.archive.org/web/2009id_/http://a.org/", route="wayback")
    assert result.outcome == "http_error" and "off the archive" in result.error


def test_pacing_per_host():
    page = httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)
    fetcher, _, fake = make_fetcher({"https://example.org/1": page, "https://example.org/2": page,
                                     "https://other.org/1": page}, delay=3.0, respect_robots=False)
    fetcher.get("https://example.org/1")
    fetcher.get("https://other.org/1")       # different host: no wait
    fetcher.get("https://example.org/2")     # same host: waits the rest of 3 s
    assert fake.slept == [3.0]


def test_wayback_captures_from_cdx():
    rows = [["timestamp", "original", "mimetype", "statuscode"],
            ["20080101000000", "http://a.org/", "text/html", "200"],
            ["20150101000000", "http://www.a.org/", "text/html", "200"]]

    def cdx(request):
        assert request.url.params["url"] == "https://a.org/p"
        assert request.url.params["filter"] == "statuscode:200"
        return httpx.Response(200, headers={"content-type": "application/json"},
                              text=json.dumps(rows))

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(cdx)
    captures, lookup = fetcher.wayback_captures("https://a.org/p#frag")
    assert lookup.ok
    assert [c.timestamp for c in captures] == ["20150101000000", "20080101000000"]
    assert captures[0].raw_url == "https://web.archive.org/web/20150101000000id_/http://www.a.org/"


@pytest.mark.parametrize("url", [
    "http://localhost/x", "http://printer.local/", "http://10.0.0.1/", "http://[::1]/",
    "https://example.org:8443/", "https://example.org/f?token=abc", "https://e.org/f?X-Amz-Signature=1",
])
def test_check_public_url_refuses(url):
    with pytest.raises(NotPublic):
        check_public_url(url)


def test_check_public_url_accepts():
    assert check_public_url("https://example.org/a?id=1") == "https://example.org/a?id=1"


def test_backend_refuses_private_resolution():
    with pytest.raises(NotPublic):
        PublicNetworkBackend().connect_tcp("localhost", 80, timeout=2)


def test_capture_time():
    assert capture_time("https://web.archive.org/web/20081218053759id_/http://a.org/") == (
        "2008-12-18T05:37:59Z", "exact")
    assert capture_time("https://a.org/") == (None, None)


EXODUS_STUB = ('<!DOCTYPE html><html><head><script>window.onload=function(){window.location.href='
               '"/lander"}</script></head></html>')


@pytest.mark.parametrize("body, ctype, expected", [
    (EXODUS_STUB, "text/html", "parked"),
    ("<html><body><h1>This domain is for sale!</h1>" + "x " * 500 + "</body></html>", "text/html", "parked"),
    ("<html><head><script>render()</script></head><body><div id=app></div></body></html>", "text/html", "empty"),
    ("<html><body><p>" + "Real text. " * 20 + "</p></body></html>", "text/html", None),
    ("plain text file", "text/plain", None),
    ("%PDF-1.4 for sale", "application/pdf", None),
])
def test_classify_stub(body, ctype, expected):
    from archive.fetch import classify_stub
    assert classify_stub(body.encode(), ctype) == expected


def test_parked_and_stub_pages_are_not_ok():
    routes = {
        "https://exodus.org/": httpx.Response(200, headers={"content-type": "text/html"}, text=EXODUS_STUB),
        "https://spa.org/": httpx.Response(200, headers={"content-type": "text/html"},
                                           text="<html><body><div id=root></div></body></html>"),
    }
    fetcher, _, _ = make_fetcher(routes)
    parked = fetcher.get("https://exodus.org/")
    assert parked.outcome == "gone" and parked.error == "domain-parking page" and parked.body is None
    assert fetcher.get("https://spa.org/").outcome == "empty"


def test_large_challenge_page_with_inline_scripts():
    from archive.fetch import classify_challenge
    page = ("<html><head><script>" + "var a=1;" * 20000 + "</script></head><body>"
            "<p>Checking your browser before accessing pmc.ncbi.nlm.nih.gov ...</p></body></html>").encode()
    assert len(page) > 60_000
    assert classify_challenge(200, {}, page) == "blocker_text"
    article = ("<html><body><p>Checking your browser is a phrase in this article. " + "Text. " * 2000
               + "</p></body></html>").encode()
    assert classify_challenge(200, {}, article * 6) is None


def test_small_challenge_page_message_after_scripts():
    from archive.fetch import classify_challenge
    page = ("<html><head><script>" + "x();" * 4000 + "</script></head><body>Checking your browser before "
            "accessing pmc.ncbi.nlm.nih.gov ...</body></html>").encode()
    assert 12_000 < len(page) < 60_000
    assert classify_challenge(200, {}, page) == "blocker_text"


def test_turnstile_bot_check_page_is_a_challenge():
    """MirrorWeb archives (e.g. webarchive.nrscotland.gov.uk) answer with a Turnstile check page."""
    from archive.fetch import classify_challenge
    page = ('<!doctype html><html><head><title>Verifying your browser — MirrorWeb Archive</title>'
            '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script></head>'
            '<body><h1>Verifying your browser</h1><form method="POST" action="/__mw_turnstile">'
            '<div class="cf-turnstile" data-sitekey="x"></div></form></body></html>').encode()
    assert classify_challenge(200, {}, page) == "blocker_text"
    vimeo = ('<html><body><h1>Verify to continue</h1><p>Checking if the site connection is secure</p>'
             '<p>vimeo.com needs to review the security of your connection before proceeding.</p></body></html>').encode()
    assert classify_challenge(200, {}, vimeo) == "blocker_text"
    # A real article that only loads the Turnstile script (carm.org) is not a challenge.
    article = ('<html><head><script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script></head>'
               '<body><div class="cf-turnstile"></div><h1>LGBTQ+</h1>' + "<p>Article text. </p>" * 50
               + "</body></html>").encode()
    assert classify_challenge(200, {}, article) is None
