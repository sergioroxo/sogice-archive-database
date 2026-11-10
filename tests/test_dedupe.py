import json

import pytest

from archive import db
from archive.acquire import pending_targets, recheck_stored
from archive.dedupe import find_groups, merge_source, run_dedupe, undo_merge, work_key
from archive.depot import Depot
from archive.match import build_match_report
from archive.passages import sha256_text
from archive.queue_import import add_urls
from archive.urls import source_id_for

NOW = "2026-11-10T12:00:00Z"


@pytest.mark.parametrize("url, key", [
    ("https://youtu.be/9UlFSTrSnnY?si=c81FfkANnaBJ-3xf", "youtube:9UlFSTrSnnY"),
    ("https://www.youtube.com/watch?v=9UlFSTrSnnY&t=30s", "youtube:9UlFSTrSnnY"),
    ("https://m.youtube.com/shorts/9UlFSTrSnnY", "youtube:9UlFSTrSnnY"),
    ("https://www.youtube-nocookie.com/embed/9UlFSTrSnnY", "youtube:9UlFSTrSnnY"),
    ("https://web.archive.org/web/2013/https://www.youtube.com/watch?v=9UlFSTrSnnY", "youtube:9UlFSTrSnnY"),
    ("https://www.youtube.com/@channel", None),
    ("https://www.youtube.com/watch?v=short", None),
    ("https://doi.org/10.1080/0092623X.2026.2651856", "doi:10.1080/0092623x.2026.2651856"),
    ("https://www.tandfonline.com/doi/pdf/10.1080/0092623X.2026.2651856", "doi:10.1080/0092623x.2026.2651856"),
    ("https://journals.sagepub.com/doi/abs/10.1177/1066480702238473", "doi:10.1177/1066480702238473"),
    ("https://psycnet.apa.org/doiLanding?doi=10.1037%2F0735-7028.33.3.249", "doi:10.1037/0735-7028.33.3.249"),
    ("https://www.tandfonline.com/doi/suppl/10.1080/0092623X.2026.2651856", None),   # supplement, not the work
    ("https://example.org/article", None),
])
def test_work_key(url, key):
    assert work_key(url) == key


def add_source(conn, url, text=None, *, version="v", flags=None, mime="text/html", path="x"):
    sid = source_id_for(url)
    conn.execute("INSERT INTO source(id, url, created_at) VALUES (?,?,?)", (sid, url, NOW))
    tid = "tgt_" + sid
    conn.execute("INSERT INTO source_target(id, source_id, route, url, created_at) VALUES (?,?, 'live_fetch', ?, ?)",
                 (tid, sid, url, NOW))
    if text is not None:
        vid = f"{version}_{sid}"
        conn.execute("INSERT INTO source_version(id, source_id, sha256, path, mime_type, acquisition_route, quality_flags,"
                     " byte_size, created_at) VALUES (?,?,?,?,?, 'live_fetch', ?, 100, ?)",
                     (vid, sid, sha256_text(vid), path, mime, json.dumps(flags or []), NOW))
        conn.execute("INSERT INTO passage(id, version_id, sequence, char_start, char_end, text, text_sha256, language,"
                     " created_at) VALUES (?,?,1,0,?,?,?, 'en', ?)",
                     ("p_" + vid, vid, len(text), text, sha256_text(text), NOW))
        conn.execute("INSERT INTO fetch_attempt(id, target_id, attempt, route, requested_url, outcome, version_id,"
                     " created_at) VALUES (?,?,1, 'live_fetch', ?, 'stored', ?, ?)", ("fa_" + vid, tid, url, vid, NOW))
    return sid


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "a.db")
    db.init_db(connection)
    with connection:
        add_source(connection, "https://www.youtube.com/watch?v=9UlFSTrSnnY", "captions of the interview")
        add_source(connection, "https://youtu.be/9UlFSTrSnnY?si=abc", "captions of the interview")
        add_source(connection, "https://doi.org/10.1080/00224499.2022.2060927", "abstract only")
        add_source(connection, "https://www.tandfonline.com/doi/full/10.1080/00224499.2022.2060927",
                   "the full text of the article, much longer than the abstract")
        add_source(connection, "https://example.org/a", "unrelated page")
    yield connection
    connection.close()


def test_groups_and_keeper(conn):
    groups = find_groups(conn)
    assert sorted(k for g in groups for k in g["keys"]) == ["doi:10.1080/00224499.2022.2060927",
                                                           "youtube:9UlFSTrSnnY"]
    report = run_dedupe(conn, dry_run=True)
    assert report["groups"] == 2 and report["merges"] == 2
    doi = next(i for i in report["plan"] if i["keys"][0].startswith("doi:"))
    assert doi["keeper"] == source_id_for("https://www.tandfonline.com/doi/full/10.1080/00224499.2022.2060927")
    assert conn.execute("SELECT COUNT(*) FROM source WHERE merged_into IS NOT NULL").fetchone()[0] == 0


def test_merge_is_a_recorded_undoable_decision(conn):
    report = run_dedupe(conn)
    assert report["merges"] == 2
    rows = conn.execute("SELECT * FROM decision WHERE action = 'merge' AND target_table = 'source'").fetchall()
    assert len(rows) == 2 and all(r["actor_kind"] == "model" and r["run_id"] for r in rows)
    duplicate = rows[0]["target_id"]
    keeper = json.loads(rows[0]["new_state"])["merged_into"]
    assert conn.execute("SELECT merged_into FROM source WHERE id = ?", (duplicate,)).fetchone()[0] == keeper
    assert find_groups(conn) == []                       # nothing left to merge
    assert run_dedupe(conn)["merges"] == 0               # idempotent
    with conn:
        undo = undo_merge(conn, rows[0]["id"], reason="not the same edition", actor_id="sergio")
    assert undo["reverts_decision_id"] == rows[0]["id"]
    assert conn.execute("SELECT merged_into FROM source WHERE id = ?", (duplicate,)).fetchone()[0] is None
    with pytest.raises(ValueError, match="already undone"):
        undo_merge(conn, rows[0]["id"], reason="again", actor_id="sergio")
    assert db.verify_decision_chain(conn).ok


def test_merge_refuses_self_and_chains(conn):
    a = source_id_for("https://example.org/a")
    b = source_id_for("https://doi.org/10.1080/00224499.2022.2060927")
    c = source_id_for("https://www.tandfonline.com/doi/full/10.1080/00224499.2022.2060927")
    with pytest.raises(ValueError):
        merge_source(conn, a, a, reason="x", actor_kind="researcher", actor_id="s")
    merge_source(conn, b, c, reason="same DOI", actor_kind="researcher", actor_id="s")
    with pytest.raises(ValueError, match="unmerged"):
        merge_source(conn, a, b, reason="x", actor_kind="researcher", actor_id="s")


def test_new_url_for_a_stored_work_attaches_to_it(conn):
    report = add_urls(conn, ["https://m.youtube.com/watch?v=9UlFSTrSnnY",
                             "https://onlinelibrary.wiley.com/doi/abs/10.1111/new.12345",
                             "https://onlinelibrary.wiley.com/doi/10.1111/new.12345"], origin="t")
    assert report["attached_by_work_key"] == 2          # the video, and the second copy of the new DOI
    assert report["new_sources"] == 1
    video = report["targets"]["https://m.youtube.com/watch?v=9UlFSTrSnnY"]["source_id"]
    assert video in {source_id_for("https://www.youtube.com/watch?v=9UlFSTrSnnY"),
                     source_id_for("https://youtu.be/9UlFSTrSnnY?si=abc")}
    wiley = {report["targets"][u]["source_id"] for u in report["targets"] if "wiley" in u}
    assert len(wiley) == 1


def test_reports_count_a_merged_copy_once(conn):
    with conn:
        conn.execute("INSERT INTO concept(id, scheme, pref_label, created_at) VALUES ('term:x', 'term', 'X', ?)", (NOW,))
        for sid in [source_id_for("https://www.youtube.com/watch?v=9UlFSTrSnnY"),
                    source_id_for("https://youtu.be/9UlFSTrSnnY?si=abc")]:
            pid = "p_v_" + sid
            conn.execute("INSERT INTO usage(id, passage_id, char_start, char_end, quote, quote_sha256, concept_id,"
                         " found_by, created_at) VALUES (?,?,0,8,'captions',?, 'term:x', 'exact_match', ?)",
                         ("u" + sid, pid, "0" * 64, NOW))
    before = build_match_report(conn, set())
    run_dedupe(conn)
    after = build_match_report(conn, set())

    def sources(report):
        rows = [r for table in report.values() if isinstance(table, list) for r in table if r.get("id") == "term:x"]
        return rows[0]["sources"] if rows else None
    assert sources(before) == 2 and sources(after) == 1


def test_recheck_flags_bot_check_pages_and_target_is_pending_again(conn, tmp_path):
    depot = Depot(tmp_path / "data")
    page = depot.put(b"<html><title>Verifying your browser</title><h1>Verifying your browser</h1>"
                     b"<form action='/__mw_turnstile'></form></html>", "html")
    article = depot.put(b"<html><body><p>A real article about the briefing.</p></body></html>", "html")
    with conn:
        bot = add_source(conn, "https://webarchive.example.gov.uk/briefing.pdf", "Verifying your browser",
                         path=page.relative_path)
        ok = add_source(conn, "https://example.org/article", "A real article", path=article.relative_path)
    assert "tgt_" + bot not in {t["id"] for t in pending_targets(conn)}
    found = recheck_stored(conn, depot, dry_run=True)
    assert [f["source_id"] for f in found] == [bot]
    assert "tgt_" + bot not in {t["id"] for t in pending_targets(conn)}   # dry run changes nothing
    recheck_stored(conn, depot)
    flags = conn.execute("SELECT quality_flags FROM source_version WHERE source_id = ?", (bot,)).fetchone()[0]
    assert "challenge_page" in json.loads(flags)
    pending = {t["id"] for t in pending_targets(conn)}
    assert "tgt_" + bot in pending and "tgt_" + ok not in pending
    assert recheck_stored(conn, depot) == []                               # already flagged


def test_missing_file_is_flagged_and_batch_continues(conn, tmp_path):
    from archive.index_text import TextIndexer, pending_versions
    depot = Depot(tmp_path / "data")
    good = depot.put(b"<html><body><p>A page that is still on disk, with enough words.</p></body></html>", "html")
    with conn:
        conn.execute("DELETE FROM passage")
        conn.execute("UPDATE source_version SET path = 'objects/00/00/gone.html'")
        conn.execute("UPDATE source_version SET path = ? WHERE source_id = ?",
                     (good.relative_path, source_id_for("https://example.org/a")))
    indexer = TextIndexer(conn, depot)
    results = [indexer.index(v) for v in pending_versions(conn)]
    assert sum(r.status == "indexed" for r in results) == 1
    failed = [r for r in results if r.status == "failed"]
    assert failed and all("missing_object" in r.flags for r in failed)
    pending = {t["id"] for t in pending_targets(conn)}
    assert "tgt_" + source_id_for("https://doi.org/10.1080/00224499.2022.2060927") not in pending  # review, no refetch
    assert pending_versions(conn) == []                  # not retried in a loop


def test_unreadable_file_is_flagged(conn, tmp_path, monkeypatch):
    from archive.index_text import TextIndexer, pending_versions
    depot = Depot(tmp_path / "data")

    def refuse(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(depot, "read", refuse)
    with conn:
        conn.execute("DELETE FROM passage")
    results = [TextIndexer(conn, depot).index(v) for v in pending_versions(conn)]
    assert results and all(r.status == "failed" and "unreadable_object" in r.flags for r in results)
