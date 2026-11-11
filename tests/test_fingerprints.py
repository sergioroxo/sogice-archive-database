import json

import pytest

from archive import db, fingerprints
from archive.passages import sha256_text
from archive.urls import source_id_for

NOW = "2026-11-11T00:00:00Z"

MINISTRY = """<html><head><title>Healing Ministry | Home</title>
<meta property="og:site_name" content="Healing Ministry">
<meta property="fb:app_id" content="111122223333444">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","name":"Healing Ministry Inc.","sameAs":["https:\\/\\/www.facebook.com\\/healingministry",
 "https://twitter.com/healingmin"]},
 {"@type":"Person","name":"An Author","sameAs":["https://twitter.com/someauthor"]}]}</script>
<script>ga('create', 'UA-12345678-29', 'auto');</script>
</head><body><p>Welcome.</p>
<a class="last" href="https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&amp;hosted_button_id=PPAAAA1111BBBB"><img src="donate.gif"></a>
<footer>&copy; 2015 Healing Ministry. Registered charity no. 1234567. Contact info@healingministry.org</footer>
<!-- <script>ga('create', 'UA-1111111-1')</script> -->
</body></html>"""

FAMILY = """<html><head><title>Famille</title>
<script>ga('create', 'UA-12345678-14', 'auto');</script></head>
<body><p>Write to info@healingministry.org for help.</p></body></html>"""

WATCHDOG = """<html><head><title>Report</title><script>gtag('config', 'G-ABCDEF1234');</script></head>
<body><p>The ministry asks for money: PayPal
<a href="https://www.paypal.com/donate/?cmd=_s-xclick&amp;hosted_button_id=PPAAAA1111BBBB">https://www.paypal.com/donate/?cmd=_s-xclick&amp;hosted_button_id=PPAAAA1111BBBB</a></p>
</body></html>"""

PLATFORM = """<html><head><script>gtag('config', 'G-PLATFORM12');</script></head><body>Courses</body></html>"""


def test_site_of():
    assert fingerprints.site_of("https://www.example-ministry.fr/page") == "example-ministry.fr"
    assert fingerprints.site_of("http://news.bbc.co.uk/x") == "bbc.co.uk"
    assert fingerprints.site_of("https://someone.wordpress.com/post") == "someone.wordpress.com"
    assert fingerprints.site_of("https://wordpress.com/") == "wordpress.com"
    assert fingerprints.site_of("https://web.archive.org/web/2015/http://ministry-one.org/a") == "ministry-one.org"
    assert fingerprints.site_of("https://unimelb.edu.au/x") == "unimelb.edu.au"


def test_scan_page_finds_and_places_each_identifier():
    found = fingerprints.scan_page(MINISTRY)
    by = {(f["kind"], f["value"], f["place"]): f for f in found}
    for f in found:
        assert MINISTRY[f["char_start"]:f["char_end"]] == f["snippet"]          # located in the page code
    assert ("google_analytics_account", "UA-12345678", "script") in by          # the property becomes the account
    assert ("google_analytics_account", "UA-1111111", "comment") in by          # switched-off code says so
    assert ("facebook_app", "111122223333444", "meta") in by
    button = by[("paypal_button", "PPAAAA1111BBBB", "link")]
    assert button["shown"] == 0                                                 # an image button: not shown
    assert by[("uk_charity_number", "1234567", "text")]["shown"] == 1
    assert ("email", "info@healingministry.org", "text") in by
    assert ("structured_name", "Healing Ministry Inc.", "data") in by
    assert ("declared_profile", "facebook.com/healingministry", "data") in by   # JSON-escaped slashes
    assert ("declared_profile", "twitter.com/healingmin", "data") in by
    assert not any(f["value"] == "twitter.com/someauthor" for f in found)       # an author's profile is not the site's
    assert ("site_name", "Healing Ministry", "meta") in by
    assert any(f["kind"] == "copyright" and f["value"].startswith("Healing Ministry") for f in found)
    assert any(f["kind"] == "title" and f["value"] == "Healing Ministry | Home" for f in found)


def test_host_ids_are_the_platforms_and_servers_are_recorded():
    page = ('<meta name="google-adsense-platform-account" content="ca-host-pub-2644536267352236">'
            '<script src="https://cdn2.editmysite.com/js/site.js"></script><img src="//www.provider-cdn.com/a.png">')
    found = fingerprints.scan_page(page)
    assert not [f for f in found if f["kind"] == "adsense_publisher"]
    assert {f["value"] for f in found if f["kind"] == "asset_host"} == {"editmysite.com", "provider-cdn.com"}


def test_a_page_that_shows_a_donation_link_as_text_is_marked():
    button = next(f for f in fingerprints.scan_page(WATCHDOG) if f["kind"] == "paypal_button")
    assert (button["place"], button["shown"]) == ("link", 1)


def add_page(conn, base, url, html, *, route="live_fetch", captured=None):
    sid = source_id_for(url)
    conn.execute("INSERT OR IGNORE INTO source(id, url, title, created_at) VALUES (?,?,?,?)", (sid, url, None, NOW))
    vid = f"ver_{sid}"
    rel = f"objects/{vid}.html"
    (base / "objects").mkdir(parents=True, exist_ok=True)
    if html is not None:
        (base / rel).write_text(html, encoding="utf-8")
    conn.execute("INSERT INTO source_version(id, source_id, sha256, path, mime_type, acquisition_route, byte_size,"
                 " captured_at, created_at) VALUES (?,?,?,?, 'text/html', ?, 1, ?, ?)",
                 (vid, sid, sha256_text(vid), rel, route, captured, NOW))
    return sid


@pytest.fixture
def archive(tmp_path):
    base = tmp_path / "data"
    conn = db.connect(tmp_path / "a.db")
    db.init_db(conn)
    with conn:
        add_page(conn, base, "http://ministry-one.org/articles/one",
                 MINISTRY, route="wayback", captured="2015-06-03T00:00:00Z")
        add_page(conn, base, "https://www.family-site.com/", FAMILY, captured="2019-09-29T00:00:00Z")
        add_page(conn, base, "https://watchdog-report.org/reports/players", WATCHDOG)
        add_page(conn, base, "https://teachable.com/", PLATFORM)
        add_page(conn, base, "https://course-seller.com/courses", PLATFORM)
        add_page(conn, base, "https://quarantined.example.org/", None)       # the object is gone
    yield conn, base
    conn.close()


def test_scan_stores_located_fingerprints_once(archive):
    conn, base = archive
    counts = fingerprints.scan(conn, data_dir=base)
    assert (counts["pages"], counts["missing"]) == (5, 1)
    row = conn.execute("SELECT * FROM fingerprint WHERE kind = 'google_analytics_account' AND site = 'ministry-one.org'"
                       " AND place = 'script'").fetchone()
    page = (base / conn.execute("SELECT path FROM source_version WHERE id = ?", (row["version_id"],)).fetchone()[0])
    assert page.read_text(encoding="utf-8")[row["char_start"]:row["char_end"]] == row["snippet"] == "UA-12345678-29"
    assert fingerprints.scan(conn, data_dir=base)["pages"] == 0                 # nothing new to scan
    again = fingerprints.scan(conn, data_dir=base, again=True)
    assert again["fingerprints"] == counts["fingerprints"]
    assert conn.execute("SELECT count(*) FROM fingerprint").fetchone()[0] == counts["fingerprints"]
    run = json.loads(conn.execute("SELECT inputs FROM run WHERE id = ?", (counts["run_id"],)).fetchone()[0])
    assert run["scanner"] == fingerprints.SCANNER and run["counts"]["pages"] == 5


def test_clusters_rank_strength_and_set_platforms_apart(archive):
    conn, base = archive
    fingerprints.scan(conn, data_dir=base)
    found = {(c["kind"], c["value"]): c for c in fingerprints.clusters(conn)}
    paypal = found[("paypal_button", "PPAAAA1111BBBB")]
    assert paypal["strength"] == "strong" and not paypal["platform"]
    flags = {e["site"]: e["shown_only"] for e in paypal["sites"]}
    assert flags == {"watchdog-report.org": True, "ministry-one.org": False}  # the watchdog quotes it
    analytics = found[("google_analytics_account", "UA-12345678")]
    assert [e["site"] for e in analytics["sites"]] == ["family-site.com", "ministry-one.org"]
    assert analytics["sites"][1]["years"] == "2015" and analytics["sites"][1]["name"] == "Healing Ministry"
    assert found[("google_analytics_4", "G-PLATFORM12")]["platform"]           # seen on teachable.com itself
    assert found[("email", "info@healingministry.org")]["strength"] == "weak"
    order = [c["strength"] for c in fingerprints.clusters(conn) if not c["platform"]]
    assert order == sorted(order, key=fingerprints.STRENGTH_ORDER.index)


def provider_archive(tmp_path, others):
    """Three ministry sites on one provider's servers share a Facebook app; ``others`` more use that provider."""
    base = tmp_path / "data"
    conn = db.connect(tmp_path / "b.db")
    db.init_db(conn)
    page = '<script src="https://www.provider-cdn.com/x.js"></script><meta property="fb:app_id" content="111122223333444">'
    with conn:
        for url in ("https://www.ministry-a.org/", "https://www.network-b.org/", "https://person-c.org/"):
            add_page(conn, base, url, page)
        for n in range(others):
            add_page(conn, base, f"https://church{n}.org/", '<script src="https://www.provider-cdn.com/x.js"></script>')
        add_page(conn, base, "https://some-school.teachable.com/p/x", PLATFORM)
        add_page(conn, base, "https://course-seller.com/p/y", PLATFORM)
    fingerprints.scan(conn, data_dir=base)
    found = {c["value"]: c for c in fingerprints.clusters(conn)}
    conn.close()
    return found


def test_an_identifier_that_follows_a_providers_server_is_set_apart(tmp_path):
    found = provider_archive(tmp_path, others=0)
    assert found["111122223333444"]["platform_reason"].startswith("3 of the 3 sites that load files from provider-cdn.com")
    assert found["G-PLATFORM12"]["platform_reason"] == "seen on a site hosted by teachable.com"


def test_an_identifier_most_of_a_providers_sites_lack_stays_evidence(tmp_path):
    found = provider_archive(tmp_path, others=2)      # 3 of 5 sites on that provider carry it
    assert not found["111122223333444"]["platform"]


def test_report_and_queueing_the_sites(archive, tmp_path):
    conn, base = archive
    fingerprints.scan(conn, data_dir=base)
    report = fingerprints.write_report(conn, tmp_path / "reports").read_text(encoding="utf-8")
    assert "PayPal donation button `PPAAAA1111BBBB`" in report
    assert "shown as text: this page may be quoting it" in report
    assert "## Platforms and providers: probably not the organisations' own (1)" in report
    queued = fingerprints.queue_sites(conn, origin="fingerprints-test")
    assert set(queued["sites"]) == {"ministry-one.org", "family-site.com", "watchdog-report.org"}
    assert queued["sites"]["ministry-one.org"]["urls"] == [
        "http://ministry-one.org/", "https://web.archive.org/web/20150603000000/http://ministry-one.org/"]
    assert queued["sites"]["family-site.com"]["urls"] == ["https://www.family-site.com/"]
    targets = conn.execute("SELECT route, url FROM source_target ORDER BY url").fetchall()
    assert ("wayback", "https://web.archive.org/web/20150603000000/http://ministry-one.org/") in \
        [tuple(t) for t in targets]
    before = conn.execute("SELECT count(*) FROM source_target").fetchone()[0]
    fingerprints.queue_sites(conn, origin="fingerprints-test", skip={"watchdog-report.org"})
    assert conn.execute("SELECT count(*) FROM source_target").fetchone()[0] == before
