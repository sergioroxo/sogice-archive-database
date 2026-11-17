"""scripts/reading_order.py: the sources of one language still to be read, in priority order. No model."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from archive import config, db, read
from archive.__main__ import sources_from_file
from archive.passages import sha256_text
from archive.urls import source_id_for

SPEC = importlib.util.spec_from_file_location(
    "reading_order", Path(__file__).resolve().parent.parent / "scripts" / "reading_order.py")
order = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(order)

NOW = "2026-11-17T12:00:00Z"
FIRST_READER, SECOND_READER = "reader-one:1b", "reader-two:1b"


def add_source(conn, url, passages, *, flags=None):
    """A source with one version; passages are (language, text)."""
    sid = source_id_for(url)
    conn.execute("INSERT INTO source(id, url, title, created_at) VALUES (?,?, 'A title', ?)", (sid, url, NOW))
    vid = f"v_{sid}"
    conn.execute("INSERT INTO source_version(id, source_id, sha256, path, mime_type, acquisition_route,"
                 " quality_flags, byte_size, created_at) VALUES (?,?,?, 'x', 'text/html', 'live_fetch', ?, 1, ?)",
                 (vid, sid, sha256_text(vid), json.dumps(flags or []), NOW))
    offset = 0
    for n, (language, text) in enumerate(passages, 1):
        text = f"{url[-8:]:>8}{text[8:]}"   # unique text: a passage in three sources is page furniture
        conn.execute("INSERT INTO passage(id, version_id, sequence, char_start, char_end, text, text_sha256,"
                     " language, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (f"p{n}_{vid}", vid, n, offset, offset + len(text), text, sha256_text(text), language, NOW))
        offset += len(text)
    return sid


def add_match(conn, sid, word, *, n=1, state="proposed"):
    """An exact match of ``word`` in passage n of the source, as `match` stores it."""
    pid = f"p{n}_v_{sid}"
    text = conn.execute("SELECT text FROM passage WHERE id = ?", (pid,)).fetchone()[0]
    start = text.index(word)
    count = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    conn.execute("INSERT INTO usage(id, passage_id, char_start, char_end, quote, quote_sha256, found_by,"
                 " review_state, created_at) VALUES (?,?,?,?,?,?, 'exact_match', ?, ?)",
                 (f"use_{count}", pid, start, start + len(word), word, sha256_text(word), state, NOW))


def add_reading(conn, sid, model, prompt_version=read.DEFAULT_PROMPT, outcome="succeeded"):
    prompt = read.load_prompt(prompt_version)
    conn.execute("INSERT INTO run(id, role, model, prompt_version, inputs, outcome, started_at)"
                 " VALUES (?, 'reading', ?, ?, ?, ?, ?)",
                 (f"run_{model}_{sid}", model, prompt.version,
                  json.dumps({"source_id": sid, "prompt_sha256": prompt.sha256}), outcome, NOW))


EN = "Exodus said conversion therapy can change people. " * 4          # 200 characters
EN_LONG = EN * 4                                                       # 800 characters
FR = "Le Conseil a dit que les thérapies de conversion sont interdites. " * 5


@pytest.fixture
def models(tmp_path, monkeypatch):
    """Reader names come from the models config, never from the script."""
    path = tmp_path / "models.yaml"
    path.write_text("ollama:\n  base_url: http://localhost:9999\nroles:\n"
                    f"  reading:\n    model: {FIRST_READER}\n"
                    f"  reading_second:\n    model: {SECOND_READER}\n"
                    "  audit:\n    model: a:1\n  embedding:\n    model: e:1\n    dimensions: 8\n", encoding="utf-8")
    monkeypatch.setenv(config.ENV_VAR, str(path))


@pytest.fixture
def archive_db(tmp_path):
    path = tmp_path / "a.db"
    conn = db.connect(path)
    db.init_db(conn)
    ids = {}
    with conn:
        ids["dense"] = add_source(conn, "https://example.org/dense", [("en", EN)])
        add_match(conn, ids["dense"], "Exodus")
        add_match(conn, ids["dense"], "conversion therapy")
        ids["sparse"] = add_source(conn, "https://example.org/sparse", [("en", EN_LONG)])
        add_match(conn, ids["sparse"], "Exodus")                           # 1 per 800 chars: 1.25 per 1,000
        ids["plain"] = add_source(conn, "https://example.org/plain", [("en", EN)])
        add_match(conn, ids["plain"], "Exodus", state="rejected")          # a rejected match counts for nothing
        ids["french"] = add_source(conn, "https://example.org/french", [("fr", FR), ("en", EN)])
        ids["copy"] = add_source(conn, "https://example.org/copy", [("en", EN)])
        conn.execute("UPDATE source SET merged_into = ? WHERE id = ?", (ids["dense"], ids["copy"]))
        ids["done"] = add_source(conn, "https://example.org/done", [("en", EN)])
        add_reading(conn, ids["done"], FIRST_READER)
        add_reading(conn, ids["done"], SECOND_READER)
        ids["half"] = add_source(conn, "https://example.org/half", [("en", EN)])
        add_reading(conn, ids["half"], FIRST_READER)                       # one reader only: still to read
        add_reading(conn, ids["half"], SECOND_READER, outcome="failed")
        ids["failing"] = add_source(conn, "https://example.org/failing", [("en", EN)])
        add_reading(conn, ids["failing"], FIRST_READER)
        add_reading(conn, ids["failing"], SECOND_READER, outcome="failed")
        conn.execute("UPDATE run SET id = id || '_1' WHERE model = ? AND outcome = 'failed'"
                     " AND json_extract(inputs, '$.source_id') = ?", (SECOND_READER, ids["failing"]))
        add_reading(conn, ids["failing"], SECOND_READER, outcome="failed")   # retried once and failed again
        ids["blocked"] = add_source(conn, "https://example.org/blocked", [("en", EN)], flags=["challenge_page"])
    conn.close()
    return path, ids


def listed(path):
    return sources_from_file(path)


def test_order_filters_and_density(archive_db, models, tmp_path, capsys):
    path, ids = archive_db
    out = tmp_path / "reports" / "order-en.txt"
    assert order.main(["--db", str(path), "--language", "en", "--out", str(out)]) == 0
    # densest first; sources without matches after, by id; merged, French, read-by-both and no-text left out
    rest = sorted([ids["plain"], ids["half"]])
    assert listed(out) == [ids["dense"], ids["sparse"], *rest]
    printed = capsys.readouterr().out
    assert "sources: 4" in printed and f"characters: {4 * len(EN) + len(EN_LONG)}" in printed
    assert "'merged': 1" in printed and "'mostly another language': 1" in printed
    assert "'read by both readers': 1" in printed and "'no text to read': 1" in printed
    assert "'failed twice': 1" in printed
    assert FIRST_READER in printed and SECOND_READER in printed
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# reading order: language en, prompt read-v4")
    # a merged copy's text is read with its keeper, so it counts there: 2 matches in 400 characters
    assert f"{ids['dense']}  # {2 * len(EN)} chars, 5.00 matches per 1,000" in text


def test_first_ids_lead_in_their_order(archive_db, models, tmp_path):
    path, ids = archive_db
    first = tmp_path / "demo.txt"
    # a merged copy stands for its keeper; a source in another language or already read is not listed
    first.write_text(f"# demo set\n{ids['plain']}\n\n{ids['french']}\n{ids['copy']}  # merged\n{ids['done']}\n",
                     encoding="utf-8")
    out = tmp_path / "order.txt"
    assert order.main(["--db", str(path), "--language", "en", "--out", str(out), "--first", str(first)]) == 0
    assert listed(out) == [ids["plain"], ids["dense"], ids["sparse"], ids["half"]]
    assert out.read_text(encoding="utf-8").count(", first") == 2


def test_unknown_first_id_stops(archive_db, models, tmp_path):
    path, _ = archive_db
    first = tmp_path / "demo.txt"
    first.write_text("src_nothing\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="unknown source"):
        order.main(["--db", str(path), "--language", "en", "--out", str(tmp_path / "o.txt"), "--first", str(first)])
    assert not (tmp_path / "o.txt").exists()


def test_another_prompt_lists_sources_read_with_the_old_one(archive_db, models, tmp_path):
    path, ids = archive_db
    out = tmp_path / "order.txt"
    assert order.main(["--db", str(path), "--language", "en", "--out", str(out), "--prompt", "read-v5"]) == 0
    assert ids["done"] in listed(out)


def test_other_language(archive_db, models, tmp_path):
    path, ids = archive_db
    out = tmp_path / "order.txt"
    assert order.main(["--db", str(path), "--language", "fr", "--out", str(out)]) == 0
    assert listed(out) == [ids["french"]]


def test_database_is_opened_read_only(archive_db, models, tmp_path):
    path, _ = archive_db
    before = path.read_bytes()
    conn = order.connect_read_only(path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO run(id, role, started_at) VALUES ('r', 'other', 'now')")
    conn.close()
    order.main(["--db", str(path), "--language", "en", "--out", str(tmp_path / "o.txt")])
    assert path.read_bytes() == before
    with pytest.raises(SystemExit, match="no database"):
        order.connect_read_only(tmp_path / "missing.db")
