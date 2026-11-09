import hashlib
import sqlite3

import pytest

from archive import db

NOW = "2026-11-09T12:00:00Z"
PASSAGE_1 = "The ministry offered healing from same-sex attraction. "
PASSAGE_2 = "Critics called it conversion therapy."


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "archive.db")
    db.init_db(connection)
    yield connection
    connection.close()


def add_source(conn, source_id="src1", version_id="v1"):
    with conn:
        conn.execute("INSERT INTO source(id, url, title, country, created_at) VALUES (?,?,?,?,?)",
                     (source_id, f"https://example.org/{source_id}", "Example", "GB", NOW))
        conn.execute(
            "INSERT INTO source_version(id, source_id, sha256, path, acquisition_route, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (version_id, source_id, sha(source_id), f"blobs/{sha(source_id)}", "wayback", NOW))


def add_passage(conn, passage_id, sequence, start, text, version_id="v1"):
    conn.execute(
        "INSERT INTO passage(id, version_id, sequence, char_start, char_end, text, text_sha256,"
        " language, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (passage_id, version_id, sequence, start, start + len(text), text, sha(text), "en", NOW))


@pytest.fixture
def passages(conn):
    add_source(conn)
    with conn:
        add_passage(conn, "p1", 1, 0, PASSAGE_1)
        add_passage(conn, "p2", 2, len(PASSAGE_1), PASSAGE_2)
    return conn


def add_usage(conn, usage_id, passage_id, quote, start, **extra):
    row = {"id": usage_id, "passage_id": passage_id, "char_start": start,
           "char_end": None if start is None else start + len(quote), "quote": quote, "quote_sha256": sha(quote),
           "found_by": "exact_match", "created_at": NOW}
    row.update(extra)
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with conn:
        conn.execute(f"INSERT INTO usage({columns}) VALUES ({marks})", tuple(row.values()))


def add_run(conn, run_id="run1"):
    with conn:
        conn.execute("INSERT INTO run(id, role, model, prompt_version, started_at) VALUES (?,?,?,?,?)",
                     (run_id, "reading", "gemma4:12b", "read-v1", NOW))


# --- schema ----------------------------------------------------------------

EXPECTED_TABLES = {
    "schema_version", "source", "source_version", "section", "passage", "passage_fts",
    "usage", "concept", "concept_label", "concept_relation", "entity", "entity_variant",
    "relation", "relation_usage", "claim", "claim_usage", "decision", "run",
    "release", "release_record", "source_target", "fetch_attempt",
}


def test_v1_database_gains_new_tables(conn):
    with conn:
        conn.execute("DROP TABLE fetch_attempt")
        conn.execute("DROP TABLE source_target")
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version VALUES (1, ?, ?)", (NOW, "0" * 64))
    assert db.init_db(conn) == db.SCHEMA_VERSION == 5
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"source_target", "fetch_attempt"} <= names


def test_old_database_gains_time_columns(tmp_path):
    path = tmp_path / "old.db"
    old = db.connect(path)
    sql = db.schema_sql()
    for line in ("  time_start        REAL CHECK (time_start IS NULL OR time_start >= 0),  -- seconds (v4)\n",
                 "  time_end          REAL CHECK (time_end IS NULL OR time_end >= 0),\n",
                 "  time_start          REAL CHECK (time_start IS NULL OR time_start >= 0),  -- seconds into audio/video (v4)\n",
                 "  time_end            REAL CHECK (time_end IS NULL OR time_end >= 0),\n"):
        assert line in sql
        sql = sql.replace(line, "", 1)
    old.executescript(sql)
    assert "time_start" not in {r[1] for r in old.execute("PRAGMA table_info(passage)")}
    old.close()
    conn = db.connect(path)
    assert db.init_db(conn) == 5
    for table in ("section", "passage"):
        assert {"time_start", "time_end"} <= {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    db.init_db(conn)  # idempotent
    conn.close()


def test_fetch_attempt_rebuilt_for_local_file_route(tmp_path):
    path = tmp_path / "v4.db"
    old = db.connect(path)
    sql = db.schema_sql().replace("CHECK (route IN ('live_fetch', 'wayback', 'local_file'))",
                                  "CHECK (route IN ('live_fetch', 'wayback'))")
    old.executescript(sql)
    with old:
        old.execute("INSERT INTO source(id, url, created_at) VALUES ('s', 'https://a.org/', ?)", (NOW,))
        old.execute("INSERT INTO source_target(id, source_id, route, url, created_at)"
                    " VALUES ('t', 's', 'live_fetch', 'https://a.org/', ?)", (NOW,))
        old.execute("INSERT INTO fetch_attempt(id, target_id, attempt, route, requested_url, outcome,"
                    " created_at) VALUES ('f1', 't', 1, 'live_fetch', 'https://a.org/', 'not_found', ?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):
        with old:
            old.execute("INSERT INTO fetch_attempt(id, target_id, attempt, route, requested_url, outcome,"
                        " created_at) VALUES ('f2', 't', 1, 'local_file', 'file:///x.pdf', 'not_found', ?)", (NOW,))
    old.close()
    conn = db.connect(path)
    db.init_db(conn)
    assert conn.execute("SELECT id FROM fetch_attempt").fetchall()[0][0] == "f1"     # row kept
    with conn:
        conn.execute("INSERT INTO fetch_attempt(id, target_id, attempt, route, requested_url, outcome,"
                     " created_at) VALUES ('f2', 't', 1, 'local_file', 'file:///x.pdf', 'not_found', ?)", (NOW,))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name = 'fetch_attempt'")}
    assert {"idx_fetch_attempt_target", "idx_fetch_attempt_outcome"} <= names     # indexes recreated
    db.init_db(conn)                                                               # idempotent
    conn.close()


def test_source_target_rules(conn):
    add_source(conn)
    insert = ("INSERT INTO source_target(id, source_id, route, url, created_at)"
              " VALUES (?, 'src1', ?, ?, ?)")
    with conn:
        conn.execute(insert, ("t1", "live_fetch", "https://example.org/a", NOW))
        conn.execute(insert, ("t2", "wayback", "https://web.archive.org/web/2009/http://example.org/a", NOW))
    with pytest.raises(sqlite3.IntegrityError):  # a wayback target must be a Wayback URL
        with conn:
            conn.execute(insert, ("t3", "wayback", "https://example.org/a", NOW))
    with pytest.raises(sqlite3.IntegrityError):  # same route + url twice
        with conn:
            conn.execute(insert, ("t4", "live_fetch", "https://example.org/a", NOW))


def test_schema_creates_all_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= names
    assert db.current_schema_version(conn) == db.SCHEMA_VERSION


def test_connect_sets_pragmas(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_init_db_is_idempotent(conn):
    before = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    add_source(conn)
    assert db.init_db(conn) == db.SCHEMA_VERSION
    db.init_db(conn)
    after = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 1


def test_interpretive_tables_have_three_separate_states(conn):
    for table in ("usage", "concept", "concept_label", "concept_relation", "entity",
                  "entity_variant", "relation", "claim"):
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert {"evidence_state", "review_state", "visibility"} <= columns, table


# --- enum and integrity checks --------------------------------------------

@pytest.mark.parametrize("sql, params", [
    ("INSERT INTO concept(id, scheme, pref_label, created_at) VALUES ('c1','not_a_scheme','x',?)", (NOW,)),
    ("INSERT INTO concept(id, scheme, pref_label, review_state, created_at) VALUES ('c1','term','x','maybe',?)", (NOW,)),
    ("INSERT INTO concept(id, scheme, pref_label, evidence_state, created_at) VALUES ('c1','term','x','attested',?)", (NOW,)),
    ("INSERT INTO concept(id, scheme, pref_label, visibility, created_at) VALUES ('c1','term','x','public',?)", (NOW,)),
    ("INSERT INTO concept(id, scheme, pref_label, definition, definition_source, created_at) VALUES ('c1','term','x','d','guess',?)", (NOW,)),
    ("INSERT INTO entity(id, kind, label, normalized_label, created_at) VALUES ('e1','company','X','x',?)", (NOW,)),
    ("INSERT INTO entity(id, kind, label, normalized_label, country, created_at) VALUES ('e1','person','X','x','uk',?)", (NOW,)),
    ("INSERT INTO source(id, url, published_date, created_at) VALUES ('s1','https://a', '2019', ?)", (NOW,)),
    ("INSERT INTO run(id, role, started_at) VALUES ('r1','reading',?)", (NOW,)),
    ("INSERT INTO run(id, role, model, outcome, started_at) VALUES ('r1','audit','m','retrying',?)", (NOW,)),
    ("INSERT INTO run(id, role, model, attempt, started_at) VALUES ('r1','audit','m',3,?)", (NOW,)),
])
def test_check_constraints_reject_bad_values(conn, sql, params):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, params)


def test_unlocated_record_cannot_be_public(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO concept(id, scheme, pref_label, visibility, created_at)"
                     " VALUES ('c1','term','x','public_eligible',?)", (NOW,))
    conn.execute("INSERT INTO concept(id, scheme, pref_label, evidence_state, visibility, created_at)"
                 " VALUES ('c1','term','x','located','public_eligible',?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE concept SET review_state='rejected' WHERE id='c1'")


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO source_version(id, source_id, sha256, path, acquisition_route, created_at)"
                     " VALUES ('v1','missing',?,'x','wayback',?)", (sha("x"), NOW))


def test_quality_flags_must_be_json_array(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO source(id, url, created_at) VALUES ('s1','https://a',?)", (NOW,))
        conn.execute("INSERT INTO source_version(id, source_id, sha256, path, acquisition_route,"
                     " quality_flags, created_at) VALUES ('v1','s1',?,'x','wayback','{}',?)", (sha("x"), NOW))


def test_concept_one_live_pref_label_per_language(conn):
    with conn:
        conn.execute("INSERT INTO concept(id, scheme, pref_label, created_at) VALUES ('c1','term','x',?)", (NOW,))
        conn.execute("INSERT INTO concept_label(id, concept_id, label, normalized_label, language, kind, created_at)"
                     " VALUES ('l1','c1','Conversion therapy','conversion therapy','en','pref',?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO concept_label(id, concept_id, label, normalized_label, language, kind, created_at)"
                     " VALUES ('l2','c1','Reparative therapy','reparative therapy','en','pref',?)", (NOW,))


# --- passages and FTS -----------------------------------------------------

def test_passages_must_be_gap_free(conn):
    add_source(conn)
    with conn:
        add_passage(conn, "p1", 1, 0, "abcdef")
    with pytest.raises(sqlite3.IntegrityError):
        add_passage(conn, "p2", 2, 7, "ghij")      # gap of one character
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        add_passage(conn, "p0", 1, 3, "xyz", version_id="v1")  # duplicate sequence
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        add_passage(conn, "p3", 3, 6, "ghij")      # skips sequence 2
    conn.rollback()
    with conn:
        add_passage(conn, "p2", 2, 6, "ghij")


def test_passage_text_is_immutable(passages):
    with pytest.raises(sqlite3.IntegrityError):
        passages.execute("UPDATE passage SET text='changed' WHERE id='p1'")
    passages.rollback()
    with passages:
        passages.execute("UPDATE passage SET language='pt' WHERE id='p1'")  # metadata may change


def test_fts_finds_passages_and_folds_diacritics(passages):
    hits = passages.execute(
        "SELECT p.id FROM passage_fts JOIN passage p ON p.pk = passage_fts.rowid"
        " WHERE passage_fts MATCH ? ORDER BY p.sequence", ('"conversion therapy"',)).fetchall()
    assert [h[0] for h in hits] == ["p2"]
    with passages:
        passages.execute("INSERT INTO source(id, url, created_at) VALUES ('s2','https://b',?)", (NOW,))
        passages.execute("INSERT INTO source_version(id, source_id, sha256, path, acquisition_route, created_at)"
                         " VALUES ('v2','s2',?,'y','live_fetch',?)", (sha("y"), NOW))
        add_passage(passages, "pt1", 1, 0, "terapia de conversão", version_id="v2")
    hits = passages.execute("SELECT rowid FROM passage_fts WHERE passage_fts MATCH 'conversao'").fetchall()
    assert len(hits) == 1


# --- usages -------------------------------------------------------------

def test_usage_quote_must_be_exact_passage_slice(passages):
    start = PASSAGE_2.index("conversion therapy")
    add_usage(passages, "u1", "p2", "conversion therapy", start)
    with pytest.raises(sqlite3.IntegrityError):
        add_usage(passages, "u2", "p2", "conversion therapies", start)   # not the slice
    with pytest.raises(sqlite3.IntegrityError):
        add_usage(passages, "u3", "p2", "conversion therapy", start + 1)  # wrong offsets


def test_usage_enums_and_run_requirement(passages):
    start = PASSAGE_2.index("Critics")
    with pytest.raises(sqlite3.IntegrityError):
        add_usage(passages, "u1", "p2", "Critics", start, stance="supports")
    with pytest.raises(sqlite3.IntegrityError):
        add_usage(passages, "u1", "p2", "Critics", start, found_by="guess")
    with pytest.raises(sqlite3.IntegrityError):
        add_usage(passages, "u1", "p2", "Critics", start, found_by="model")  # model usage needs a run
    add_run(passages)
    add_usage(passages, "u1", "p2", "Critics", start, found_by="model", run_id="run1", stance="reports")


def test_located_usage_needs_location(passages):
    with pytest.raises(sqlite3.IntegrityError):
        add_usage(passages, "u1", None, "healing", None, char_end=None)
    add_usage(passages, "u1", None, "healing", None, char_end=None, evidence_state="unlocated")


# --- decisions ------------------------------------------------------------

def decide(conn, **overrides):
    kwargs = dict(actor_kind="researcher", actor_id="sgr", action="confirm",
                  target_table="concept", target_id="c1", reason="Quote supports the sense.")
    kwargs.update(overrides)
    return db.record_decision(conn, **kwargs)


def test_record_decision_builds_hash_chain(conn):
    first = decide(conn, previous_state={"review_state": "proposed"}, new_state={"review_state": "confirmed"})
    second = decide(conn, action="dispute", reason="Second source disagrees.")
    assert first["prior_hash"] == db.GENESIS_HASH
    assert second["prior_hash"] == first["event_hash"]
    assert db.chain_head(conn) == second["event_hash"]
    report = db.verify_decision_chain(conn, expected_head=second["event_hash"])
    assert report.ok, report.errors
    assert report.checked == 2


def test_hash_does_not_depend_on_json_spacing(conn):
    row = decide(conn, previous_state='{"b": 1,  "a": [1, 2]}')
    assert row["previous_state"] == '{"a":[1,2],"b":1}'
    assert db.verify_decision_chain(conn).ok


def test_decision_enums_checked(conn):
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, actor_kind="robot")
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, action="approve")
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, target_table="nowhere")
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, audit_verdict="ok")
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, reason="  ")
    assert conn.execute("SELECT COUNT(*) FROM decision").fetchone()[0] == 0


def test_model_decision_requires_run(conn):
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, actor_kind="model", actor_id="gemma4:12b")
    add_run(conn)
    row = decide(conn, actor_kind="model", actor_id="gemma4:12b", run_id="run1", action="propose",
                 quote="conversion therapy", audit_verdict="pass")
    undo = decide(conn, action="undo", reverts_decision_id=row["id"], reason="Wrong sense.")
    assert undo["reverts_decision_id"] == row["id"]
    assert db.verify_decision_chain(conn).ok


def test_decisions_cannot_be_updated_or_deleted(conn):
    decide(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE decision SET reason='rewritten'")
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM decision")
    conn.rollback()
    assert conn.execute("SELECT reason FROM decision").fetchone()[0] == "Quote supports the sense."


def test_decision_inside_caller_transaction_rolls_back_with_it(conn):
    conn.execute("INSERT INTO concept(id, scheme, pref_label, created_at) VALUES ('c1','term','x',?)", (NOW,))
    assert conn.in_transaction
    decide(conn)
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM decision").fetchone()[0] == 0
    assert db.verify_decision_chain(conn).ok


def _drop_guards(conn):
    conn.execute("DROP TRIGGER decision_no_update")
    conn.execute("DROP TRIGGER decision_no_delete")


def test_chain_detects_edited_row(conn):
    decide(conn)
    decide(conn, action="dispute")
    _drop_guards(conn)
    with conn:
        conn.execute("UPDATE decision SET reason='rewritten' WHERE seq=1")
    report = db.verify_decision_chain(conn)
    assert not report.ok
    assert any("missing append-only trigger" in e for e in report.errors)
    assert any("seq 1" in e and "edited" in e for e in report.errors)
    # init_db restores the guards; the edit stays visible in the chain.
    db.init_db(conn)
    report = db.verify_decision_chain(conn)
    assert not report.ok and all("trigger" not in e for e in report.errors)


def test_chain_detects_deleted_rows(conn):
    decide(conn)
    decide(conn, action="dispute")
    head = decide(conn, action="reject")["event_hash"]
    _drop_guards(conn)
    with conn:
        conn.execute("DELETE FROM decision WHERE seq=2")
    db.init_db(conn)
    report = db.verify_decision_chain(conn)
    assert not report.ok
    assert any("removed or reordered" in e for e in report.errors)
    # Truncating the newest decision leaves a valid chain; the anchor catches it.
    with conn:
        conn.execute("DROP TRIGGER decision_no_delete")
        conn.execute("DELETE FROM decision WHERE seq IN (2, 3)")
    db.init_db(conn)
    assert db.verify_decision_chain(conn).ok
    assert not db.verify_decision_chain(conn, expected_head=head).ok


def test_release_records_are_frozen(conn):
    with conn:
        conn.execute("INSERT INTO release(id, created_at, rules, decision_head_hash) VALUES ('r1',?,'{}',?)",
                     (NOW, db.chain_head(conn)))
        conn.execute("INSERT INTO release_record(release_id, table_name, record_id, record_sha256)"
                     " VALUES ('r1','usage','u1',?)", (sha("row"),))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM release_record")
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE release_record SET record_sha256=?", (sha("other"),))
