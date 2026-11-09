import json
import sqlite3

import pytest

from archive import db
from archive.__main__ import main
from archive.queue_import import kind_hint, plan_import, read_queue, run_import

NOW = "2026-11-09T12:00:00Z"
OLD_SCHEMA = """
CREATE TABLE source_queue (
  id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  source_type TEXT NOT NULL DEFAULT 'webpage', doc_type_hint TEXT NOT NULL DEFAULT 'unknown',
  priority TEXT NOT NULL DEFAULT 'medium', recommended_llm TEXT NOT NULL DEFAULT 'litelm',
  notes TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new',
  needs_testimony_review INTEGER NOT NULL DEFAULT 0, needs_legal_review INTEGER NOT NULL DEFAULT 0
);
"""

ROWS = [
    # id, url, source_type, priority, testimony flag
    ("a1", "https://web.archive.org/web/20160617034707/http://www.narth.com/", "video", "medium", 0),
    ("a2", "https://web.archive.org/web/20081218053759/http://www.narth.com/", "webpage", "high", 1),
    ("a3", "https://www.narth.com", "webpage", "medium", 0),
    ("b1", "https://example.org/report.pdf", "webpage", "medium", 0),
    ("b2", "http://example.org/report.pdf#page=4", "pdf", "medium", 0),   # same target? no: different URL
    ("c1", "https://vimeo.com/257127541", "video", "skip", 0),
    ("d1", "https://web.archive.org/web/sitemap/https://loveinaction.org/", "webpage", "low", 0),
    ("e1", "ftp://old.example.org/file", "webpage", "low", 0),
    ("f1", "https://youtu.be/abc", "video", "medium", 0),
    ("f2", "https://youtu.be/abc", "video", "skip", 0),                    # exact repeat
]


@pytest.fixture
def queue(tmp_path):
    path = tmp_path / "source_queue.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.executemany(
        "INSERT INTO source_queue(id, url, source_type, priority, needs_testimony_review)"
        " VALUES (?,?,?,?,?)", ROWS)
    conn.execute("UPDATE source_queue SET notes = 'check page 4', title = 'Report' WHERE id = 'b1'")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def archive(tmp_path):
    conn = db.connect(tmp_path / "archive.db")
    db.init_db(conn)
    yield conn
    conn.close()


def targets_by_legacy(plan):
    return {ref: t for t in plan.targets.values() for ref in t["legacy_refs"]}


def test_plan_groups_captures_and_live_under_one_source(queue):
    plan = plan_import(read_queue(queue), now=NOW)
    by = targets_by_legacy(plan)
    narth = {by[i]["source_id"] for i in ("a1", "a2", "a3")}
    assert len(narth) == 1
    source = plan.sources[narth.pop()]
    assert source["url"] == "https://www.narth.com"            # live URL preferred
    assert source["archive_url"].endswith("/20081218053759/http://www.narth.com/")  # earliest
    assert source["captured_date"] == "2008-12-18T05:37:59Z"
    assert source["captured_date_certainty"] == "exact"
    assert source["legacy_ref"] == "a1"
    assert by["a1"]["route"] == by["a2"]["route"] == "wayback"
    assert by["a2"]["capture_timestamp"] == "20081218053759"
    assert by["a3"]["route"] == "live_fetch"
    assert by["a2"]["legacy_hints"]["a2"]["flags"] == ["needs_testimony_review"]
    assert "recommended_llm" not in json.dumps(by["a2"]["legacy_hints"])


def test_plan_kinds_holds_repeats_and_rejects(queue):
    plan = plan_import(read_queue(queue), now=NOW)
    by = targets_by_legacy(plan)
    assert by["b1"]["kind_hint"] == "pdf"                       # URL beats old type
    assert by["b1"]["source_id"] == by["b2"]["source_id"]      # fragment and scheme ignored
    assert by["b1"]["id"] != by["b2"]["id"]                    # but both URLs kept as given
    assert plan.sources[by["b1"]["source_id"]]["title"] == "Report"
    assert by["c1"]["hold"] == "legacy_priority_skip" and by["c1"]["kind_hint"] == "video"
    assert by["f1"] is by["f2"] and by["f1"]["legacy_refs"] == ["f1", "f2"]
    assert by["f1"]["hold"] is None                            # not every row said skip
    assert by["f1"]["kind_hint"] == "youtube"
    sitemap = by["d1"]
    assert sitemap["route"] == "live_fetch" and sitemap["url"] == "https://loveinaction.org/"
    assert sitemap["legacy_hints"]["d1"]["from_wayback_link"].endswith("sitemap/https://loveinaction.org/")
    assert [r["legacy_id"] for r in plan.rejected] == ["e1"]


def test_run_import_writes_and_is_idempotent(queue, archive):
    first = run_import(queue, archive)
    assert first["old_rows"] == 10
    assert first["new_sources"] == first["sources"] == 5
    assert first["new_targets"] == first["targets"] == 8
    assert first["sources_with_live_and_wayback"] == 1
    assert len(first["rejected"]) == 1
    second = run_import(queue, archive)
    assert second["new_sources"] == 0 and second["new_targets"] == 0
    assert archive.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 5
    assert archive.execute("SELECT COUNT(*) FROM run WHERE role = 'other'").fetchone()[0] == 2
    row = archive.execute("SELECT legacy_refs, legacy_hints FROM source_target"
                          " WHERE legacy_refs LIKE '%f2%'").fetchone()
    assert json.loads(row["legacy_refs"]) == ["f1", "f2"]
    assert json.loads(row["legacy_hints"])["f2"]["priority"] == "skip"


def test_old_queue_is_not_written(queue, archive):
    before = queue.read_bytes()
    run_import(queue, archive)
    assert queue.read_bytes() == before


def test_cli_dry_run_writes_no_database(queue, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SOGICE_DATA_DIR", str(tmp_path / "data"))
    assert main(["import-queue", "--queue", str(queue), "--dry-run",
                 "--db", str(tmp_path / "data" / "archive.db")]) == 0
    assert not (tmp_path / "data" / "archive.db").exists()
    assert list((tmp_path / "data" / "reports").glob("queue-import-*-dry.json"))
    assert '"sources": 5' in capsys.readouterr().out


def test_missing_queue(tmp_path, archive):
    with pytest.raises(FileNotFoundError):
        run_import(tmp_path / "nope.db", archive)


@pytest.mark.parametrize("source_type, url, expected", [
    ("webpage", "https://a.org/x.PDF", "pdf"),
    ("webpage", "https://a.org/x.doc", "docx"),
    ("webpage", "https://eu.eu/download/f4a_da?filename=ECI_factsheet_DA.pdf", "pdf"),
    ("webpage", "https://www.youtube.com/watch?v=1", "youtube"),
    ("url", "https://a.org/", "unknown"),
    ("social", "https://x.com/a", "social"),
])
def test_kind_hint(source_type, url, expected):
    assert kind_hint(source_type, url) == expected
