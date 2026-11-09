import json

import httpx
import pytest

from archive import db
from archive.acquire import Acquirer, pending_targets
from archive.depot import Depot
from tests.test_fetch import HTML, make_fetcher

NOW = "2026-11-09T12:00:00Z"
PAGE = httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=HTML)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "archive.db")
    db.init_db(connection)
    with connection:
        for sid, url in (("s1", "https://a.org/p"), ("s2", "https://b.org/doc.pdf")):
            connection.execute("INSERT INTO source(id, url, created_at) VALUES (?,?,?)", (sid, url, NOW))
        targets = [
            ("t_live", "s1", "live_fetch", "https://a.org/p", None, "webpage", None),
            ("t_wb", "s1", "wayback", "https://web.archive.org/web/20090101000000/http://a.org/p",
             "20090101000000", "webpage", None),
            ("t_pdf", "s2", "live_fetch", "https://b.org/doc.pdf", None, "pdf", None),
            ("t_held", "s2", "live_fetch", "https://b.org/held", None, "video", "legacy_priority_skip"),
            ("t_yt", "s2", "live_fetch", "https://youtu.be/x", None, "youtube", None),
        ]
        connection.executemany(
            "INSERT INTO source_target(id, source_id, route, url, capture_timestamp, kind_hint, hold,"
            " created_at) VALUES (?,?,?,?,?,?,?,'" + NOW + "')", targets)
    yield connection
    connection.close()


def target(conn, target_id):
    return conn.execute("SELECT * FROM source_target WHERE id = ?", (target_id,)).fetchone()


def attempts(conn, target_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM fetch_attempt WHERE target_id = ? ORDER BY created_at, attempt", (target_id,))]


def test_pending_targets_skips_held(conn):
    assert [t["id"] for t in pending_targets(conn)] == ["t_live", "t_pdf", "t_wb", "t_yt"]
    assert [t["id"] for t in pending_targets(conn, kinds=["pdf"])] == ["t_pdf"]
    assert [t["id"] for t in pending_targets(conn, target_ids=["t_yt"])] == ["t_yt"]


def test_live_page_is_stored_once(conn, tmp_path):
    fetcher, _, _ = make_fetcher({"https://a.org/p": PAGE})
    acq = Acquirer(conn, fetcher, Depot(tmp_path))
    result = acq.acquire(target(conn, "t_live"))
    assert result.status == "stored" and result.route == "live_fetch" and result.new_file
    version = conn.execute("SELECT * FROM source_version").fetchone()
    assert version["acquisition_route"] == "live_fetch" and version["mime_type"] == "text/html"
    assert (tmp_path / version["path"]).read_bytes() == HTML.encode()
    assert [a["outcome"] for a in attempts(conn, "t_live")] == ["stored"]
    assert "t_live" not in [t["id"] for t in pending_targets(conn)]
    # same bytes from the Wayback capture attach to the same version
    fetcher.client._transport = httpx.MockTransport(lambda request: PAGE)
    again = acq.acquire(target(conn, "t_wb"))
    assert again.version_id == result.version_id and not again.new_file
    assert conn.execute("SELECT COUNT(*) FROM source_version").fetchone()[0] == 1


def test_wayback_target_uses_raw_url_and_capture_time(conn, tmp_path):
    raw = "https://web.archive.org/web/20090101000000id_/http://a.org/p"
    other = httpx.Response(200, headers={"content-type": "text/html"}, text=HTML + "<p>2009</p>")
    fetcher, seen, _ = make_fetcher({raw: other})
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn, "t_wb"))
    assert result.status == "stored" and seen == [raw]
    version = conn.execute("SELECT * FROM source_version WHERE id = ?", (result.version_id,)).fetchone()
    assert version["captured_at"] == "2009-01-01T00:00:00Z"
    assert version["acquisition_route"] == "wayback"


def test_blocked_live_page_falls_back_to_wayback(conn, tmp_path):
    cdx_rows = [["timestamp", "original", "mimetype", "statuscode"],
                ["20120505000000", "http://a.org/p", "text/html", "200"]]
    raw = "https://web.archive.org/web/20120505000000id_/http://a.org/p"

    def handler(request):
        url = str(request.url)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if url == "https://a.org/p":
            return httpx.Response(403, headers={"server": "cloudflare"}, text="blocked")
        if url.startswith("https://web.archive.org/cdx/"):
            return httpx.Response(200, text=json.dumps(cdx_rows))
        if url == raw:
            return PAGE
        return httpx.Response(404)

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(handler)
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn, "t_live"))
    assert result.status == "stored" and result.route == "wayback"
    assert result.outcomes == ["blocked", "stored"]
    version = conn.execute("SELECT * FROM source_version").fetchone()
    assert json.loads(version["quality_flags"]) == ["wayback_fallback"]
    assert version["captured_at"] == "2012-05-05T00:00:00Z"
    rows = attempts(conn, "t_live")
    assert json.loads(rows[0]["detail"])["challenge"] == "cloudflare_http:403"
    assert json.loads(rows[1]["detail"])["fallback_for"] == "blocked"


def test_transient_failure_retried_once_then_no_capture(conn, tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "web.archive.org":
            return httpx.Response(200, text=json.dumps([["timestamp", "original", "mimetype", "statuscode"]]))
        return httpx.Response(503, headers={"retry-after": "5"})

    fetcher, _, fake = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(handler)
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn, "t_live"))
    assert result.status == "failed"
    assert result.outcomes == ["http_error", "http_error", "no_capture"]
    assert [a["attempt"] for a in attempts(conn, "t_live")] == [1, 2, 1]
    assert 5.0 in fake.slept
    assert "t_live" not in [t["id"] for t in pending_targets(conn)]
    assert "t_live" in [t["id"] for t in pending_targets(conn, retry_failed=True)]


def test_not_a_document_is_not_stored(conn, tmp_path):
    binary = httpx.Response(200, headers={"content-type": "application/octet-stream"},
                            content=b"\x00\x01\x02 binary")
    cdx_empty = httpx.Response(200, text="[]")
    fetcher, _, _ = make_fetcher({"https://b.org/doc.pdf": binary})
    original = fetcher.client._transport

    def handler(request):
        if request.url.host == "web.archive.org":
            return cdx_empty
        return original.handle_request(request)

    fetcher.client._transport = httpx.MockTransport(handler)
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn, "t_pdf"))
    assert result.status == "failed"
    assert result.outcomes[0] == "unsupported_type"
    assert conn.execute("SELECT COUNT(*) FROM source_version").fetchone()[0] == 0


def test_parked_live_page_skips_parked_captures(conn, tmp_path):
    from tests.test_fetch import EXODUS_STUB
    stub = httpx.Response(200, headers={"content-type": "text/html"}, text=EXODUS_STUB)
    cdx_rows = [["timestamp", "original", "mimetype", "statuscode"],
                ["20240101000000", "http://a.org/p", "text/html", "200"],   # parked already
                ["20100101000000", "http://a.org/p", "text/html", "200"]]   # the real site

    def handler(request):
        url = str(request.url)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if url.startswith("https://web.archive.org/cdx/"):
            assert request.url.params["limit"] == "-8"
            return httpx.Response(200, text=json.dumps(cdx_rows))
        if url == "https://web.archive.org/web/20100101000000id_/http://a.org/p":
            return PAGE
        return stub

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(handler)
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn, "t_live"))
    assert result.outcomes == ["gone", "gone", "stored"]
    version = conn.execute("SELECT * FROM source_version").fetchone()
    assert version["captured_at"] == "2010-01-01T00:00:00Z"


def test_media_file_does_not_fall_back(conn, tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"ID3...")

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(handler)
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn, "t_pdf"))
    assert result.outcomes == ["unsupported_type"]
    assert not any("web.archive.org" in url for url in seen)
    from archive.report import build_report
    assert build_report(conn)["media_files_for_transcription"] == 1


def test_script_only_live_page_falls_back(conn, tmp_path):
    shell = ("<html><head><script src='/app.js'></script>" + "<script>var x = 1;</script>" * 6000
             + "</head><body><div id='root'></div></body></html>")
    cdx_rows = [["timestamp", "original", "mimetype", "statuscode"],
                ["20150101000000", "http://a.org/p", "text/html", "200"]]

    def handler(request):
        url = str(request.url)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if url.startswith("https://web.archive.org/cdx/"):
            return httpx.Response(200, text=json.dumps(cdx_rows))
        if url.startswith("https://web.archive.org/web/20150101000000id_/"):
            return PAGE
        return httpx.Response(200, headers={"content-type": "text/html"}, text=shell)

    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(handler)
    result = Acquirer(conn, fetcher, Depot(tmp_path)).acquire(target(conn, "t_live"))
    assert result.outcomes == ["empty", "stored"] and result.route == "wayback"
    first = attempts(conn, "t_live")[0]
    assert json.loads(first["detail"])["error"].startswith("no extractable text")
