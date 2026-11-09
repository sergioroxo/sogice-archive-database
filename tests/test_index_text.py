import json

import pytest

from archive import db
from archive.depot import Depot
from archive.index_text import TextIndexer, detect_language, pending_versions
from archive.quote_locator import locate_quote
from tests.test_extract import ARTICLE, make_pdf

NOW = "2026-11-09T12:00:00Z"
PT = ("A terapia de conversão é uma prática que tenta mudar a orientação sexual das pessoas. "
      "Os sobreviventes relatam danos duradouros e vergonha. ")


@pytest.fixture
def setup(tmp_path):
    conn = db.connect(tmp_path / "archive.db")
    db.init_db(conn)
    depot = Depot(tmp_path)
    with conn:
        conn.execute("INSERT INTO source(id, url, created_at) VALUES ('s1', 'https://example.org/a', ?)", (NOW,))
    yield conn, depot
    conn.close()


def add_version(conn, depot, version_id, data, extension):
    stored = depot.put(data, extension)
    with conn:
        conn.execute(
            "INSERT INTO source_version(id, source_id, sha256, path, acquisition_route, quality_flags,"
            " created_at) VALUES (?, 's1', ?, ?, 'live_fetch', ?, ?)",
            (version_id, stored.sha256, stored.relative_path,
             json.dumps(["wayback_fallback"]), NOW))
    return conn.execute("SELECT v.*, 'https://example.org/a' AS source_url FROM source_version v"
                        " WHERE id = ?", (version_id,)).fetchone()


def passages(conn, version_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM passage WHERE version_id = ? ORDER BY sequence", (version_id,))]


def test_detect_language():
    assert detect_language(PT)[0] == "pt"
    lang, confidence = detect_language("The ministry offered healing from same-sex attraction to all.")
    assert lang == "en" and 0 < confidence <= 1
    assert detect_language("p. 12 — 3/4") == ("und", None)


def test_html_version_is_indexed(setup):
    conn, depot = setup
    version = add_version(conn, depot, "ver_html", ARTICLE.encode(), "html")
    assert [v["id"] for v in pending_versions(conn)] == ["ver_html"]
    result = TextIndexer(conn, depot).index(version)
    assert result.status == "indexed" and result.passages == 1 and result.languages.keys() == {"en"}
    row = conn.execute("SELECT * FROM source_version WHERE id = 'ver_html'").fetchone()
    text = depot.read(row["path"].replace(".html", ".html")) and depot.read(row["text_path"]).decode()
    assert "lasting harm" in text and row["extraction_tool"].startswith("trafilatura")
    assert json.loads(row["quality_flags"]) == ["wayback_fallback"]   # kept
    rows = passages(conn, "ver_html")
    assert "".join(p["text"] for p in rows) == text                   # gap-free
    assert rows[0]["section_id"] == "sec_html_001" and rows[0]["language"] == "en"
    source = conn.execute("SELECT * FROM source WHERE id = 's1'").fetchone()
    assert source["title"].startswith("Ministry promises change")
    assert source["publisher"] == "Example News"
    assert (source["published_date"], source["published_date_certainty"]) == ("2015-04-09", "inferred")
    assert source["language"] == "en"
    hits = conn.execute("SELECT rowid FROM passage_fts WHERE passage_fts MATCH 'harm'").fetchall()
    assert len(hits) == 1
    assert pending_versions(conn) == []


def test_long_pdf_pages_sections_and_quotes(setup):
    conn, depot = setup
    sentence = "Leaders described the programme as a path to healing and restoration for members. "
    pages = [(sentence * 9).strip() for _ in range(50)] + [PT.strip()]
    version = add_version(conn, depot, "ver_pdf", make_pdf(pages), "pdf")
    result = TextIndexer(conn, depot).index(version)
    assert result.sections >= 2 and result.passages > 10
    rows = passages(conn, "ver_pdf")
    text = depot.read(conn.execute("SELECT text_path FROM source_version WHERE id='ver_pdf'")
                      .fetchone()[0]).decode()
    assert "".join(p["text"] for p in rows) == text
    assert rows[0]["page_start"] == 1 and rows[-1]["page_end"] == 51
    assert rows[-1]["language"] == "pt" and rows[0]["language"] == "en"
    sections = conn.execute("SELECT * FROM section WHERE version_id='ver_pdf' ORDER BY sequence").fetchall()
    for p in rows:                                      # each passage inside its section
        s = next(s for s in sections if s["id"] == p["section_id"])
        assert s["char_start"] <= p["char_start"] < p["char_end"] <= s["char_end"]
    records = [{"unit_id": p["id"], "text": p["text"]} for p in rows]
    assert locate_quote("tenta mudar a orientação sexual", records)["unit_id"] == rows[-1]["id"]
    source = conn.execute("SELECT published_date FROM source WHERE id = 's1'").fetchone()
    assert source["published_date"] is None              # PDF dates are not trusted


def test_unreadable_file_is_flagged_and_skipped(setup):
    conn, depot = setup
    version = add_version(conn, depot, "ver_bad", b"%PDF-1.4 not a real pdf", "pdf")
    result = TextIndexer(conn, depot).index(version)
    assert result.status == "failed" and result.error
    flags = json.loads(conn.execute("SELECT quality_flags FROM source_version WHERE id='ver_bad'")
                       .fetchone()[0])
    assert flags == ["extraction_failed", "wayback_fallback"]
    assert pending_versions(conn) == []
    assert [v["id"] for v in pending_versions(conn, retry_failed=True)] == ["ver_bad"]


def test_empty_text_has_no_passages(setup):
    conn, depot = setup
    version = add_version(conn, depot, "ver_blank", make_pdf(["", ""]), "pdf")
    result = TextIndexer(conn, depot).index(version)
    assert result.status == "indexed" and result.passages == 0
    assert "no_text" in result.flags and "needs_ocr" in result.flags
    row = conn.execute("SELECT * FROM source_version WHERE id='ver_blank'").fetchone()
    assert row["text_path"] is None and row["text_sha256"] is not None
