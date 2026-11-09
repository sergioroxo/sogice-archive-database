import json

import httpx
import pytest

from archive import db
from archive.acquire import Acquirer
from archive.depot import Depot
from archive.doi import DoiResolver, doi_candidates, jats_to_text, rebuild_inverted_index
from tests.test_fetch import make_fetcher

NOW = "2026-11-09T12:00:00Z"
DOI = "10.1080/00918360903543196"
URL = f"https://www.tandfonline.com/doi/full/{DOI}"
CROSSREF_REPLY = {"status": "ok", "message": {
    "DOI": DOI.upper(), "type": "journal-article",
    "title": ["Influence of Family, Religion, and Social Conformity on Client Participation"],
    "container-title": ["Journal of Homosexuality"], "publisher": "Informa UK Limited",
    "issued": {"date-parts": [[2010, 2, 26]]},
    "author": [{"given": "Elaine M.", "family": "Maccio"}]}}
OPENALEX_REPLY = {
    "id": "https://openalex.org/W1", "title": "Influence of Family", "publication_date": "2010-02-26",
    "abstract_inverted_index": {"This": [0], "study": [1], "examined": [2], "therapy.": [3]},
    "open_access": {"is_oa": True},
    "best_oa_location": {"is_oa": True, "pdf_url": "https://publisher.org/oa.pdf", "version": "publishedVersion",
                         "source": {"type": "journal"}},
    "locations": [{"is_oa": True, "pdf_url": "https://repo.edu/aam.pdf", "version": "acceptedVersion",
                   "source": {"type": "repository"}}]}


@pytest.mark.parametrize("url, expected", [
    (URL, [DOI]),
    ("https://journals.sagepub.com/doi/abs/10.1177/1066480702238473", ["10.1177/1066480702238473"]),
    ("https://link.springer.com/article/10.1007/s10508-012-9966-y", ["10.1007/s10508-012-9966-y"]),
    ("https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0202330", ["10.1371/journal.pone.0202330"]),
    ("https://www.taylorfrancis.com/chapters/edit/10.4324/9780203101827-5/standards-weapons-exclusion",
     ["10.4324/9780203101827-5/standards-weapons-exclusion", "10.4324/9780203101827-5"]),
    ("https://www.degruyterbrill.com/document/doi/10.7312/chec12850-007/html", ["10.7312/chec12850-007"]),
    ("https://www.ovid.com/journals/propy/pdf/10.1037/0735-7028.33.3.249~changing-sexual", ["10.1037/0735-7028.33.3.249"]),
    ("https://doi.org/10.1023%2FB%3AASEB.0000014621.83301.29", ["10.1023/b:aseb.0000014621.83301.29"]),
    ("https://example.org/no-doi-here", []),
])
def test_doi_candidates(url, expected):
    assert doi_candidates(url) == expected


def test_abstract_text_helpers():
    jats = "<jats:title>Abstract</jats:title><jats:p>First &amp; best.</jats:p><jats:p>Second.</jats:p>"
    assert jats_to_text(jats) == "First & best.\n\nSecond."
    assert rebuild_inverted_index({"therapy.": [3], "This": [0], "study": [1], "examined": [2]}) == \
        "This study examined therapy."
    assert rebuild_inverted_index(None) == ""


def api_handler(extra=None):
    def handler(request):
        url = str(request.url)
        if url == f"https://api.crossref.org/works/{DOI}":
            return httpx.Response(200, json=CROSSREF_REPLY)
        if url == f"https://api.openalex.org/works/doi:{DOI}":
            return httpx.Response(200, json=OPENALEX_REPLY)
        if extra:
            reply = extra(request)
            if reply is not None:
                return reply
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(404)
    return handler


def test_resolver_merges_crossref_and_openalex():
    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(api_handler())
    record, results = DoiResolver(fetcher).lookup(URL)
    assert record.doi == DOI and record.container == "Journal of Homosexuality"
    assert record.date == "2010-02-26" and record.authors == ["Elaine M. Maccio"]
    assert record.abstract_source == "openalex" and record.abstract == "This study examined therapy."
    assert record.abstract_text().startswith("Influence of Family, Religion")
    assert [u for u, _, _ in record.oa_urls] == ["https://repo.edu/aam.pdf", "https://publisher.org/oa.pdf"]
    assert all(r.ok for r in results)


def test_resolver_tries_shorter_doi_after_404():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if str(request.url) == "https://api.crossref.org/works/10.4324/9780203101827-5":
            return httpx.Response(200, json={"message": {"DOI": "10.4324/9780203101827-5", "title": ["Ch"]}})
        return httpx.Response(404)

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(handler)
    record, _ = DoiResolver(fetcher).lookup("https://www.taylorfrancis.com/chapters/edit/10.4324/9780203101827-5/slug")
    assert record.doi == "10.4324/9780203101827-5" and record.title == "Ch"
    assert seen[0].endswith("/10.4324/9780203101827-5/slug")


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "archive.db")
    db.init_db(connection)
    with connection:
        connection.execute("INSERT INTO source(id, url, title, created_at) VALUES ('s1', ?, 'Page guess', ?)", (URL, NOW))
        connection.execute("INSERT INTO source_target(id, source_id, route, url, kind_hint, created_at)"
                           " VALUES ('t1', 's1', 'live_fetch', ?, 'webpage', ?)", (URL, NOW))
    yield connection
    connection.close()


def target(conn):
    return conn.execute("SELECT * FROM source_target WHERE id='t1'").fetchone()


def blocked_publisher(request):
    if request.url.host == "www.tandfonline.com" and request.url.path != "/robots.txt":
        return httpx.Response(403, headers={"server": "cloudflare"}, text="Just a moment...")
    if request.url.host == "web.archive.org":
        return httpx.Response(200, text="[]")
    return None


def test_blocked_article_gets_abstract_and_open_access_copy(conn, tmp_path):
    pdf = b"%PDF-1.4 accepted manuscript"

    def extra(request):
        if str(request.url) == "https://repo.edu/aam.pdf":
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=pdf)
        return blocked_publisher(request)

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(api_handler(extra))
    depot = Depot(tmp_path)
    result = Acquirer(conn, fetcher, depot).acquire(target(conn))
    assert result.status == "stored" and result.route == "doi"
    assert result.outcomes == ["blocked", "doi_abstract", "stored"]
    versions = {json.dumps(json.loads(r["quality_flags"])): r for r in conn.execute("SELECT * FROM source_version")}
    assert set(versions) == {'["abstract_openalex", "doi_abstract"]', '["oa_acceptedversion", "open_access_copy"]'}
    abstract = versions['["abstract_openalex", "doi_abstract"]']
    assert depot.read(abstract["path"]).decode().endswith("This study examined therapy.")
    source = conn.execute("SELECT * FROM source WHERE id='s1'").fetchone()
    assert source["title"].startswith("Influence of Family, Religion")     # registered title wins
    assert (source["publisher"], source["published_date"], source["published_date_certainty"]) == (
        "Journal of Homosexuality", "2010-02-26", "exact")
    detail = json.loads(conn.execute("SELECT detail FROM fetch_attempt WHERE version_id = ?",
                                     (abstract["id"],)).fetchone()[0])
    assert detail["doi"] == DOI and detail["fallback_for"] == "blocked"
    assert json.loads(depot.read(detail["crossref_path"]))["publisher"] == "Informa UK Limited"


def test_closed_article_keeps_abstract_after_one_wayback_try(conn, tmp_path):
    closed = {**OPENALEX_REPLY, "open_access": {"is_oa": False}, "best_oa_location": None, "locations": []}
    cdx = [["timestamp", "original", "mimetype", "statuscode"],
           ["20200101000000", URL, "text/html", "200"], ["20190101000000", URL, "text/html", "200"]]
    tried = []

    def handler(request):
        url = str(request.url)
        if url.startswith("https://api.openalex.org/"):
            return httpx.Response(200, json=closed)
        if url.startswith("https://api.crossref.org/"):
            return httpx.Response(200, json=CROSSREF_REPLY)
        if url.startswith("https://web.archive.org/cdx/"):
            return httpx.Response(200, text=json.dumps(cdx))
        if url.startswith("https://web.archive.org/web/"):
            tried.append(url)
            return httpx.Response(302, headers={"location": url})          # redirect loop
        return blocked_publisher(request) or httpx.Response(404)

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(handler)
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn))
    assert result.status == "stored" and result.route == "doi"
    assert result.outcomes == ["blocked", "doi_abstract", "http_error"]
    assert len(set(tried)) == 1                                           # one capture only


def test_open_live_article_only_takes_metadata(conn, tmp_path):
    page = "<html><body><article><p>" + "Full open article text. " * 40 + "</p></article></body></html>"

    def extra(request):
        if str(request.url) == URL:
            return httpx.Response(200, headers={"content-type": "text/html"}, text=page)
        return None

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(api_handler(extra))
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn))
    assert result.route == "live_fetch" and result.outcomes == ["stored", "doi_metadata"]
    assert conn.execute("SELECT COUNT(*) FROM source_version").fetchone()[0] == 1
    detail = json.loads(conn.execute("SELECT detail FROM fetch_attempt").fetchone()[0])
    assert detail["doi"] == DOI and "openalex_path" in detail
    assert conn.execute("SELECT published_date FROM source").fetchone()[0] == "2010-02-26"


def test_markup_and_moved_pmc():
    from archive.doi import clean_markup
    from archive.urls import current_url
    assert clean_markup("Telling a poor <i>man</i> he can\n become rich") == "Telling a poor man he can become rich"
    assert clean_markup("Gay &amp; Lesbian") == "Gay & Lesbian"
    assert current_url("https://www.ncbi.nlm.nih.gov/pmc/articles/1311224") == \
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC1311224/"
    assert current_url("http://ncbi.nlm.nih.gov/pmc/articles/PMC42/pdf/x.pdf") == \
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC42/pdf/x.pdf"
    assert current_url("https://example.org/a") == "https://example.org/a"


def test_citation_pdf_url():
    from archive.acquire import citation_pdf_url
    page = b'<html><head><meta name="citation_pdf_url" content="/articles/PMC1/pdf/a.pdf"></head></html>'
    assert citation_pdf_url(page, "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/") == \
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/pdf/a.pdf"
    swapped = b"<meta content='https://j.org/x.pdf?a=1&amp;b=2' name='citation_pdf_url' />"
    assert citation_pdf_url(swapped, "https://j.org/") == "https://j.org/x.pdf?a=1&b=2"
    assert citation_pdf_url(b"<html></html>", "https://j.org/") is None


def test_short_landing_page_pdf_is_followed(conn, tmp_path):
    landing = ('<html><head><meta name="citation_pdf_url" content="https://j.org/paper.pdf"></head>'
               "<body><article><p>" + "Abstract of the paper. " * 10 + "</p></article></body></html>")
    long_page = landing.replace("Abstract of the paper. " * 10, "Full text. " * 400)
    pdf = b"%PDF-1.4 the scanned article"

    def handler(request):
        url = str(request.url)
        if request.url.path == "/robots.txt" or url.startswith("https://api."):
            return httpx.Response(404)
        if url == URL:
            return httpx.Response(200, headers={"content-type": "text/html"}, text=landing)
        if url == "https://j.org/paper.pdf":
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=pdf)
        if url == "https://long.org/a":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=long_page)
        return httpx.Response(404)

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(handler)
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn))
    assert result.outcomes[:2] == ["stored", "stored"]
    flags = sorted(r[0] for r in conn.execute("SELECT quality_flags FROM source_version"))
    assert flags == ['["citation_pdf"]', '[]']
    # a long page is not followed
    with conn:
        conn.execute("INSERT INTO source_target(id, source_id, route, url, kind_hint, created_at)"
                     " VALUES ('t2', 's1', 'live_fetch', 'https://long.org/a', 'webpage', ?)", (NOW,))
    other = conn.execute("SELECT * FROM source_target WHERE id='t2'").fetchone()
    assert Acquirer(conn, fetcher, Depot(tmp_path)).acquire(other).outcomes == ["stored"]


def test_declared_pdf_must_be_a_pdf(conn, tmp_path):
    landing = ('<html><head><meta name="citation_pdf_url" content="https://j.org/paper.pdf"></head>'
               "<body><article><p>" + "Abstract of the paper. " * 10 + "</p></article></body></html>")
    holding = "<html><head><title>Please wait</title></head><body><h1>One moment</h1><p>Loading the file for you now.</p></body></html>"

    def handler(request):
        url = str(request.url)
        if request.url.path == "/robots.txt" or url.startswith("https://api."):
            return httpx.Response(404)
        if url == URL:
            return httpx.Response(200, headers={"content-type": "text/html"}, text=landing)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=holding)

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(handler)
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn))
    assert result.outcomes[:2] == ["stored", "unsupported_type"]
    assert conn.execute("SELECT COUNT(*) FROM source_version").fetchone()[0] == 1


def test_pow_page_is_a_challenge():
    from archive.fetch import classify_challenge
    assert classify_challenge(200, {}, b"<title>Preparing to download ...</title><script>const POW_CHALLENGE='x'</script>")
