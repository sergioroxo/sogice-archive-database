import json

import pytest

from archive import db, review, titles
from archive.config import RoleConfig
from archive.db import record_decision
from archive.passages import sha256_text
from archive.urls import source_id_for
from tests.test_read import FakeClient

NOW = "2026-11-11T00:00:00Z"
ROLE = RoleConfig(name="titles", model="small:model", temperature=0, extra={"num_predict": 512})


@pytest.mark.parametrize("title, url, reason", [
    (None, "https://a.org/x", "missing"),
    ("Home", "https://a.org/", "generic"),
    ("Accueil", "https://a.fr/", "generic"),
    ("r4501", "https://assemblee-nationale.fr/x.pdf", "code"),
    ("Microsoft Word - 127593789_8.docx", "https://a.org/x.pdf", "file name"),
    ("Report 2024.pdf - Google Drive", "https://drive.google.com/x", "file name"),
    ("exoduseurope.org", "http://www.exoduseurope.org/", "domain"),
    ("Identity", "https://boldlybeloved.com/identity", "one short word"),
    ("Exodus International closes its doors", "https://a.org/", None),
])
def test_poor_reason(title, url, reason):
    assert titles.poor_reason(title, url) == reason


def test_page_candidates_prefer_the_pages_own_title_without_the_site_name():
    page = ('<head><meta property="og:site_name" content="Desert Stream">'
            '<meta property="og:title" content="Home / Desert Stream">'
            '<title>Living Waters Programme | Desert Stream</title></head><body><h1>Welcome</h1></body>')
    found = titles.page_candidates(page, "https://www.desertstream.org/")
    assert [(f["method"], f["title"]) for f in found] == [("title element", "Living Waters Programme")]
    assert page[found[0]["char_start"]:found[0]["char_end"]] == found[0]["snippet"]
    inner = '<meta property="og:site_name" content="Courage"><meta property="og:title" content="Courage"><h1>Our chapters</h1>'
    assert [f["title"] for f in titles.page_candidates(inner, "https://couragerc.org/chapters/")] == ["Our chapters"]


@pytest.mark.parametrize("page", ['<title>newdirection.ca for sale</title>', '<title>Buy example.org now</title>',
                                  '<h1>PUBLISHED: November 6, 2024</h1>'])
def test_parked_domains_and_datelines_are_not_titles(page):
    assert titles.page_candidates(page, "https://example.org/x") == []


def add(conn, base, url, *, title=None, html=None, pdf_text=None, captured=None, n=1):
    sid = source_id_for(url)
    conn.execute("INSERT OR IGNORE INTO source(id, url, title, created_at) VALUES (?,?,?,?)", (sid, url, title, NOW))
    vid = f"v{n}_{sid}"
    mime = "text/html" if html is not None else "application/pdf"
    rel = f"objects/{vid}.{'html' if html is not None else 'pdf'}"
    (base / "objects").mkdir(parents=True, exist_ok=True)
    (base / rel).write_text(html or "", encoding="utf-8")
    conn.execute("INSERT INTO source_version(id, source_id, sha256, path, mime_type, acquisition_route, byte_size,"
                 " captured_at, created_at) VALUES (?,?,?,?,?, 'live_fetch', 1, ?, ?)",
                 (vid, sid, sha256_text(vid), rel, mime, captured, NOW))
    if pdf_text:
        conn.execute("INSERT INTO passage(id, version_id, sequence, char_start, char_end, page_start, text,"
                     " text_sha256, language, created_at) VALUES (?,?,1,0,?,1,?,?, 'fr', ?)",
                     (f"p_{vid}", vid, len(pdf_text), pdf_text, sha256_text(pdf_text), NOW))
    return sid


@pytest.fixture
def archive(tmp_path):
    base = tmp_path / "data"
    conn = db.connect(tmp_path / "a.db")
    db.init_db(conn)
    yield conn, base
    conn.close()


def test_repair_from_page_code_is_a_recorded_undoable_decision(archive):
    conn, base = archive
    with conn:
        sid = add(conn, base, "https://www.affirmunited.com/", title="Home",
                  html="<title>Affirm United - Home</title>")
        add(conn, base, "https://good.org/", title="A perfectly good title", html="<title>Other</title>")
    dry = titles.repair(conn, data_dir=base, dry_run=True)
    assert dry["counts"]["page code"] == 1 and dry["runs"] == []
    assert conn.execute("SELECT title FROM source WHERE id = ?", (sid,)).fetchone()[0] == "Home"
    done = titles.repair(conn, data_dir=base)
    assert conn.execute("SELECT title FROM source WHERE id = ?", (sid,)).fetchone()[0] == "Affirm United"
    d = conn.execute("SELECT * FROM decision WHERE target_id = ?", (sid,)).fetchone()
    assert (d["action"], d["actor_id"]) == ("edit", titles.ACTOR_ID)
    assert json.loads(d["previous_state"]) == {"title": "Home"}
    assert json.loads(d["new_state"])["method"] == "title element"
    assert titles.repair(conn, data_dir=base)["counts"]["poor"] == 0          # nothing left to repair
    review.undo(conn, done["runs"][0], reason="test")
    assert conn.execute("SELECT title FROM source WHERE id = ?", (sid,)).fetchone()[0] == "Home"


def test_a_title_other_pages_of_the_site_share_is_skipped(archive):
    conn, base = archive
    with conn:
        a = add(conn, base, "https://www.reddit.com/r/a/1/", title="Reddit", html="<title>Dive into everything</title><h1>Ex-gay AMA</h1>")
        b = add(conn, base, "https://www.reddit.com/r/b/2/", title="Reddit", html="<title>Dive into everything</title><h1>Terms for orientation</h1>")
    titles.repair(conn, data_dir=base)
    got = dict(conn.execute("SELECT id, title FROM source").fetchall())
    assert (got[a], got[b]) == ("Ex-gay AMA", "Terms for orientation")


def test_copies_that_disagree_are_left_alone(archive):
    conn, base = archive
    with conn:
        sid = add(conn, base, "https://survivors.example/", title="Survivors", html="<title>Healthy living tips</title>",
                  captured="2026-01-01T00:00:00Z", n=1)
        add(conn, base, "https://survivors.example/", html="<title>Conversion therapy survivors network</title>",
            captured="2016-01-01T00:00:00Z", n=2)
    out = titles.repair(conn, data_dir=base)
    assert out["counts"]["copies disagree (left alone)"] == 1
    assert conn.execute("SELECT title FROM source WHERE id = ?", (sid,)).fetchone()[0] == "Survivors"


def test_first_page_title_is_located_before_it_is_used(archive):
    conn, base = archive
    page = ("N° 4501\nASSEMBLÉE NATIONALE\nRAPPORT D'INFORMATION\nsur les pratiques prétendant modifier\n"
            "l'orientation sexuelle ou l'identité de genre\n\f page two")
    with conn:
        good = add(conn, base, "https://an.fr/l15b4501.pdf", title="r4501", pdf_text=page)
        bad = add(conn, base, "https://an.fr/other.pdf", title="x_1", pdf_text="Some first page text about nothing in particular.")
    client = FakeClient([
        {"title": "RAPPORT D'INFORMATION sur les pratiques prétendant modifier l'orientation sexuelle ou l'identité de genre",
         "issued_by": "ASSEMBLÉE NATIONALE", "kind": "report"},
        {"title": "An Invented Title", "issued_by": "", "kind": "other"},
    ])
    out = titles.repair(conn, client=client, role=ROLE, data_dir=base)
    row = conn.execute("SELECT title, publisher FROM source WHERE id = ?", (good,)).fetchone()
    assert tuple(row) == ("RAPPORT D'INFORMATION sur les pratiques prétendant modifier l'orientation sexuelle "
                          "ou l'identité de genre", "ASSEMBLÉE NATIONALE")
    assert conn.execute("SELECT title FROM source WHERE id = ?", (bad,)).fetchone()[0] == "x_1"
    assert out["counts"]["first page: located"] == 1 and out["counts"]["first page: title not located"] == 1
    d = conn.execute("SELECT actor_id, run_id FROM decision WHERE target_id = ?", (good,)).fetchone()
    assert d["actor_id"] == "small:model"
    run = conn.execute("SELECT role, model, prompt_version FROM run WHERE id = ?", (d["run_id"],)).fetchone()
    assert tuple(run) == ("extraction", "small:model", titles.PROMPT)


def test_a_title_the_researcher_set_is_never_changed(archive):
    conn, base = archive
    with conn:
        sid = add(conn, base, "https://a.org/", title="Home", html="<title>Something better here</title>")
        record_decision(conn, actor_kind="researcher", actor_id="sergio", action="edit", target_table="source",
                        target_id=sid, reason="I checked: the page is called Home",
                        previous_state={"title": "Home"}, new_state={"title": "Home"})
    assert titles.repair(conn, data_dir=base)["counts"]["poor"] == 0
