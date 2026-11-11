import json

import pytest

from archive import authors, db, read, review
from archive.db import record_decision
from archive.passages import sha256_text
from archive.urls import source_id_for

NOW = "2026-11-11T00:00:00Z"
ESSAY = ('<html><head><meta property="og:site_name" content="Example Network">'
         '<meta name="author" content="admin">'
         '<script type="application/ld+json">{"@context": "https://schema.org", "@graph": ['
         '{"@type": "Article", "headline": "On the MoU", "author": {"@id": "#p1"}},'
         '{"@type": "Person", "@id": "#p1", "name": "Jane Q. Writer, Clinical Psychologist"},'
         '{"@type": "Organization", "@id": "#org", "name": "Example Network"}]}</script></head>'
         '<body><p class="byline">By Jane Q. Writer</p><p>The essay.</p></body></html>')


@pytest.mark.parametrize("value, names", [
    ("Jane Doe, Clinical Psychologist", ["Jane Doe"]),
    ("Sílvia Maia, Lusa; Susana Venceslau, Lusa", ["Sílvia Maia", "Susana Venceslau"]),
    ("Jane Doe and John Smith", ["Jane Doe", "John Smith"]),
    ("Women and Equalities Committee", ["Women and Equalities Committee"]),
    ("View all posts by Editor", ["Editor"]),
    ("McDermott Will & Emery LLP", ["McDermott Will & Emery LLP"]),
])
def test_split_names(value, names):
    assert authors.split_names(value) == names


@pytest.mark.parametrize("name, ok", [("Jane Doe", True), ("admin", False), ("Editor", False), ("Council on", False),
                                      ("Emery LLP", False), ("Queer theory", False), ("Participant – R", False), ("Exodus International", False),
                                      ("Visualizza il mio profilo completo", False), ("info@example.org", False),
                                      ("Example Network", False), ("Jane", False)])
def test_plausible(name, ok):
    assert authors.plausible(name, {authors._squash("Example Network")}) is ok


def add(conn, base, url, *, page=None, publisher=None, n=1):
    sid = source_id_for(url)
    conn.execute("INSERT OR IGNORE INTO source(id, url, title, publisher, created_at) VALUES (?,?, 'A title', ?, ?)",
                 (sid, url, publisher, NOW))
    vid = f"v{n}_{sid}"
    (base / "objects").mkdir(parents=True, exist_ok=True)
    (base / f"objects/{vid}.html").write_text(page or "", encoding="utf-8")
    conn.execute("INSERT INTO source_version(id, source_id, sha256, path, mime_type, acquisition_route, byte_size,"
                 " created_at) VALUES (?,?,?,?, 'text/html', 'live_fetch', 1, ?)",
                 (vid, sid, sha256_text(vid), f"objects/{vid}.html", NOW))
    return sid


@pytest.fixture
def archive(tmp_path):
    base = tmp_path / "data"
    conn = db.connect(tmp_path / "a.db")
    db.init_db(conn)
    yield conn, base
    conn.close()


def test_a_person_from_structured_data_who_the_page_prints(archive):
    conn, base = archive
    with conn:
        sid = add(conn, base, "https://example.net/essay/", page=ESSAY, publisher="Example Network")
        hidden = add(conn, base, "https://example.net/page/",
                     page='<meta name="author" content="Joe Webmaster"><p>No byline here.</p>')
    out = authors.assign(conn, data_dir=base)
    got = dict(conn.execute("SELECT id, author FROM source").fetchall())
    assert got[sid] == "Jane Q. Writer" and got[hidden] is None          # a name the page never shows is not used
    assert out["counts"]["structured data"] == 1
    d = conn.execute("SELECT * FROM decision WHERE target_id = ?", (sid,)).fetchone()
    assert (d["action"], d["actor_id"]) == ("edit", authors.ACTOR_ID)
    assert json.loads(d["new_state"])["method"] == "structured data"
    review.undo(conn, out["runs"][0], reason="test")
    assert conn.execute("SELECT author FROM source WHERE id = ?", (sid,)).fetchone()[0] is None


def test_crossref_authors_of_a_merged_doi_source(archive):
    conn, base = archive
    with conn:
        keeper = add(conn, base, "https://journal.example/full/10.1/x", page="<p>Full text.</p>")
        landing = add(conn, base, "https://doi-landing.example/10.1/x", page="<p>Abstract.</p>")
        conn.execute("UPDATE source SET merged_into = ? WHERE id = ?", (keeper, landing))
        (base / "objects/crossref.json").write_text(json.dumps({"author": [
            {"given": "Ann", "family": "Author", "sequence": "first"},
            {"given": "Bo", "family": "Second", "sequence": "additional"}]}), encoding="utf-8")
        conn.execute("INSERT INTO source_target(id, source_id, route, url, created_at) VALUES ('t1', ?, 'live_fetch', ?, ?)",
                     (landing, "https://doi-landing.example/10.1/x", NOW))
        conn.execute("INSERT INTO fetch_attempt(id, target_id, attempt, route, requested_url, outcome, detail, version_id,"
                     " created_at) VALUES ('f1', 't1', 1, 'live_fetch', 'x', 'stored', ?, ?, ?)",
                     (json.dumps({"crossref_path": "objects/crossref.json", "doi": "10.1/x"}), f"v1_{landing}", NOW))
    authors.assign(conn, data_dir=base)
    assert conn.execute("SELECT author FROM source WHERE id = ?", (keeper,)).fetchone()[0] == "Ann Author; Bo Second"
    header = read.source_header(conn, keeper)
    assert "Author: Ann Author; Bo Second" in header


def test_the_header_shows_five_names_and_counts_the_rest():
    many = authors.as_text([f"Person Number{chr(65 + i)}" for i in range(12)])
    assert many.endswith("; and 2 others")
    assert authors.shown(many) == "; ".join(f"Person Number{chr(65 + i)}" for i in range(5)) + "; and 7 others"


def test_copies_naming_different_authors_are_left_alone(archive):
    conn, base = archive
    with conn:
        sid = add(conn, base, "https://blog.example/post/", n=1,
                  page='<meta name="author" content="Ann Old"><p>By Ann Old</p>')
        add(conn, base, "https://blog.example/post/", n=2,
            page='<meta name="author" content="Zed Newer"><p>By Zed Newer</p>')
    out = authors.assign(conn, data_dir=base)
    assert out["counts"]["copies disagree"] == 1
    assert conn.execute("SELECT author FROM source WHERE id = ?", (sid,)).fetchone()[0] is None


def test_an_author_the_researcher_set_is_never_changed(archive):
    conn, base = archive
    with conn:
        sid = add(conn, base, "https://example.net/essay/", page=ESSAY)
        record_decision(conn, actor_kind="researcher", actor_id="sergio", action="edit", target_table="source",
                        target_id=sid, reason="checked", previous_state={"author": None}, new_state={"author": None})
    assert authors.assign(conn, data_dir=base, again=True)["counts"]["sources"] == 0
