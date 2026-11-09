import json

import httpx
import pytest

from archive import db
from archive.preserve import interleave_by_host, run_preserve
from archive.report import build_report, to_markdown
from tests.test_captions import INFO, FakeYtdlp
from tests.test_fetch import HTML, make_fetcher

NOW = "2026-11-09T12:00:00Z"
TARGETS = [
    ("s1", "t1", "live_fetch", "https://a.org/1", "webpage", None),
    ("s2", "t2", "live_fetch", "https://a.org/2", "webpage", None),
    ("s3", "t3", "live_fetch", "https://b.org/gone", "webpage", None),
    ("s4", "t4", "wayback", "https://web.archive.org/web/20090101000000/http://c.org/", "webpage", None),
    ("s5", "t5", "live_fetch", "https://www.youtube.com/watch?v=abc123", "youtube", None),
    ("s6", "t6", "live_fetch", "https://vimeo.com/1", "video", "legacy_priority_skip"),
]


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "archive.db"
    conn = db.connect(path)
    db.init_db(conn)
    with conn:
        for sid, tid, route, url, kind, hold in TARGETS:
            conn.execute("INSERT INTO source(id, url, created_at) VALUES (?,?,?)", (sid, url, NOW))
            conn.execute("INSERT INTO source_target(id, source_id, route, url, kind_hint, hold, created_at)"
                         " VALUES (?,?,?,?,?,?,?)", (tid, sid, route, url, kind, hold, NOW))
    conn.close()
    return path


def handler(request):
    url = str(request.url)
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    if url in ("https://a.org/1", "https://a.org/2"):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text=HTML.replace("Real article", f"Article {url[-1]}"))
    if url == "https://web.archive.org/web/20090101000000id_/http://c.org/":
        return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML + "<p>c.org 2009</p>")
    if url.startswith("https://web.archive.org/cdx/"):
        return httpx.Response(200, text="[]")
    return httpx.Response(404)


def test_interleave_by_host():
    rows = [{"url": u} for u in ("https://a.org/1", "https://a.org/2", "https://a.org/3", "https://b.org/1",
                                 "https://web.archive.org/web/2009/http://a.org/")]
    assert [r["url"] for r in interleave_by_host(rows)] == [
        "https://a.org/1", "https://b.org/1", "https://web.archive.org/web/2009/http://a.org/",
        "https://a.org/2", "https://a.org/3"]


def test_preserve_end_to_end_and_report(database, tmp_path):
    fetcher, _, _ = make_fetcher({})
    fetcher.client._transport = httpx.MockTransport(handler)
    lines = []
    summary = run_preserve(database, tmp_path, workers=3, fetcher=fetcher,
                           video_runner=FakeYtdlp(INFO), log=lines.append)
    assert summary["targets"] == 5                                   # held one excluded
    assert summary["fetch"] == {"stored_live_fetch": 3, "stored_wayback": 1, "failed": 1}
    assert summary["index"] == {"indexed": 5}                       # 3 pages, description, captions
    conn = db.connect(database)
    assert conn.execute("SELECT COUNT(*) FROM passage").fetchone()[0] >= 4
    run = conn.execute("SELECT * FROM run WHERE id = ?", (summary["run_id"],)).fetchone()
    assert run["outcome"] == "succeeded" and json.loads(run["inputs"])["summary"]["targets"] == 5

    report = build_report(conn)
    assert report["gate"]["text_sources"] == 4                      # s1..s4 (s5 youtube, s6 held)
    assert report["gate"]["passing"] == 3 and report["gate"]["share"] == 0.75
    assert report["targets_by_kind"]["webpage"] == {"stored": 3, "failed": 1}
    assert report["targets_by_kind"]["video"] == {"held": 1}
    assert report["failure_first_outcome"] == {"not_found": 1}
    assert report["failure_final_outcome"] == {"no_capture": 1}
    assert report["failures_by_domain"] == [{"domain": "b.org", "failed": 1, "outcomes": {"not_found": 1}}]
    assert report["youtube"] == {"description:stored": 1, "captions:stored": 1}
    markdown = to_markdown(report)
    assert "**Gate:** 3 of 4 text sources" in markdown and "| b.org | 1 | not_found 1 |" in markdown

    again = run_preserve(database, tmp_path, fetcher=fetcher, log=lines.append)
    assert again["targets"] == 0 and again["index"] == {}
    conn.close()


def test_pilot_sample_is_stratified_and_deterministic():
    from archive.preserve import pilot_sample
    rows = []
    for i in range(300):
        rows.append({"id": f"w{i:03d}", "kind_hint": "webpage", "route": "live_fetch", "url": f"https://site{i}.org/p"})
    for i in range(40):
        rows.append({"id": f"b{i:03d}", "kind_hint": "webpage", "route": "wayback",
                     "url": f"https://web.archive.org/web/2009/http://old{i % 10}.org/p{i}"})
    for i in range(30):
        rows.append({"id": f"p{i:03d}", "kind_hint": "pdf", "route": "live_fetch", "url": f"https://same.org/{i}.pdf"})
    rows.append({"id": "y1", "kind_hint": "youtube", "route": "live_fetch", "url": "https://www.youtube.com/watch?v=1"})
    sample = pilot_sample(rows, 50, seed=3)
    assert len(sample) == 50 and sample == pilot_sample(rows, 50, seed=3)
    kinds = {}
    for row in sample:
        kinds[(row["kind_hint"], row["route"])] = kinds.get((row["kind_hint"], row["route"]), 0) + 1
    assert kinds[("webpage", "wayback")] == 6                  # quota met from 10 distinct old sites
    assert kinds[("pdf", "live_fetch")] == 1                   # one per domain
    assert kinds[("youtube", "live_fetch")] == 1
    assert kinds[("webpage", "live_fetch")] == 42              # shortfalls filled with live pages
    assert sample != pilot_sample(rows, 50, seed=4)
