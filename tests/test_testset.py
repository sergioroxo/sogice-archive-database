import hashlib
import json

import pytest

from archive import db
from archive.depot import Depot
from archive.passages import sha256_text
from archive.queue_import import add_urls
from archive.testset import TestsetError as SetError, entry_view, norm, prepare, score, validate
from archive.urls import source_id_for

NOW = "2026-11-10T12:00:00Z"
URL_A = "https://example.org/report.pdf"
URL_B = "https://mirror.example.net/report.pdf"
PAGE_1 = "Mme Valérie Petit. Ils disent vouloir « guérir les blessures de l’âme ». Parce qu’ils se prêtent aux"
PAGE_2 = "interprétations les plus diverses, ces alinéas risquent de réduire le champ.\nM. Breton. Cela concernerait 60 à 90 % d’entre eux."
TEXT = PAGE_1 + "\n\f\n" + PAGE_2
FOOTER = "Donate to Example Ministry today."


def entry(**extra):
    base = {"key": "fr", "split": "dev", "urls": [URL_A], "expect": []}
    base.update(extra)
    return base


def make_set(*expectations, entries=None):
    return {"version": "t", "gate": {"quotes_located": 0.9, "speaker_filled": 0.9, "stance_filled": 0.9,
                                     "seconds_per_1000_chars": 10},
            "entries": entries or [entry(expect=list(expectations))]}


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "a.db")
    db.init_db(connection)
    with connection:
        for n, url in enumerate([URL_A, URL_B], 1):
            sid = source_id_for(url)
            connection.execute("INSERT INTO source(id, url, created_at) VALUES (?,?,?)", (sid, url, NOW))
            connection.execute("INSERT INTO source_version(id, source_id, sha256, path, mime_type, acquisition_route,"
                               " created_at) VALUES (?,?,?, 'x', 'application/pdf', 'live_fetch', ?)",
                               (f"v{n}", sid, str(n) * 64, NOW))
            for seq, (text, start, first, last) in enumerate([(TEXT, 0, 1, 2), (FOOTER, len(TEXT), 2, 2)], 1):
                connection.execute(
                    "INSERT INTO passage(id, version_id, sequence, char_start, char_end, page_start, page_end, text,"
                    " text_sha256, language, created_at) VALUES (?,?,?,?,?,?,?,?,?, 'fr', ?)",
                    (f"p{n}{seq}", f"v{n}", seq, start, start + len(text), first, last, text, sha256_text(text), NOW))
        connection.execute("INSERT INTO source(id, url, created_at) VALUES ('s3', 'https://other.org/', ?)", (NOW,))
        connection.execute("INSERT INTO source_version(id, source_id, sha256, path, acquisition_route, created_at)"
                           " VALUES ('v3', 's3', ?, 'x', 'live_fetch', ?)", ("3" * 64, NOW))
        connection.execute("INSERT INTO passage(id, version_id, sequence, char_start, char_end, text, text_sha256,"
                           " language, created_at) VALUES ('p31', 'v3', 1, 0, ?, ?, ?, 'en', ?)",
                           (len(FOOTER), FOOTER, sha256_text(FOOTER), NOW))
    yield connection
    connection.close()


def read(connection, source_url=URL_A, run="r1", seconds=30.0, chars=None):
    inputs = {"source_id": source_id_for(source_url)}
    if chars is not None:
        inputs["chars"] = chars
    with connection:
        connection.execute("INSERT INTO run(id, role, model, inputs, seconds, outcome, started_at)"
                           " VALUES (?, 'reading', 'm', ?, ?, 'succeeded', ?)",
                           (run, json.dumps(inputs), seconds, NOW))


def usage(connection, uid, quote, *, passage="p11", run="r1", located=True, **fields):
    text = TEXT if passage.endswith("1") and passage != "p31" else FOOTER
    start = text.index(quote) if located else None
    row = {"id": uid, "passage_id": passage if located else None, "char_start": start,
           "char_end": start + len(quote) if located else None, "quote": quote,
           "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(), "found_by": "model", "run_id": run,
           "created_at": NOW, "evidence_state": "located" if located else "unlocated",
           "speaker": "unknown", "stance": "unknown", **fields}
    with connection:
        connection.execute(f"INSERT INTO usage({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                           tuple(row.values()))


def statuses(connection, data, judgements=None):
    return {r["id"]: (r["status"], r["detail"]) for r in score(connection, data, judgements)["results"]}


def test_validate_rejects_mistakes():
    with pytest.raises(SetError, match="unknown check"):
        validate(make_set({"id": "a", "check": "vibes"}))
    with pytest.raises(SetError, match="needs"):
        validate(make_set({"id": "a", "check": "usage"}))
    with pytest.raises(SetError, match="repeated"):
        validate(make_set({"id": "a", "check": "pages"}, {"id": "a", "check": "pages"}))
    with pytest.raises(SetError, match="where keys"):
        validate(make_set({"id": "a", "check": "usage", "anchor": "x", "where": {"mood": "x"}}))
    with pytest.raises(SetError, match="split"):
        validate(make_set(entries=[entry(split="train")]))
    validate(make_set({"id": "a", "check": "usage", "anchor": "x", "where": {"speaker_any": ["P"]}}))
    validate(make_set({"id": "a", "check": "usage", "anchor": "x", "where": [{"speaker_any": ["P"]}, {"stance_any": ["x"]}]}))
    with pytest.raises(SetError, match="where keys"):
        validate(make_set({"id": "a", "check": "usage", "anchor": "x", "where": [{"speaker_any": ["P"]}, {"mood": 1}]}))


def test_norm_folds_case_accents_quotes_and_space():
    assert norm("  Guérir les\nblessures de l’Âme ") == "guerir les blessures de l'ame"


def test_anchor_found_with_page_from_page_break(conn):
    view = entry_view(conn, entry())
    first = view.find("guerir les blessures de l'ame")      # plain apostrophe, no accents
    assert [(f.passage_id, f.page) for f in first] == [("p11", 1)]
    assert TEXT[first[0].start:first[0].end] == "guérir les blessures de l’âme"
    assert [f.page for f in view.find("ces alinéas risquent")] == [2]
    assert view.find("se prêtent aux interprétations")      # across the page break


def test_before_reading(conn):
    data = make_set(
        {"id": "lang", "check": "language", "code": "fr"},
        {"id": "pages", "check": "pages"},
        {"id": "u", "check": "usage", "anchor": "guérir les blessures", "page": 1},
        {"id": "wrong-page", "check": "usage", "anchor": "guérir les blessures", "page": 2},
        {"id": "missing", "check": "usage", "anchor": "words that are not there"},
        {"id": "j", "check": "judged", "criterion": "x"})
    got = statuses(conn, data)
    assert got["lang"][0] == got["pages"][0] == "pass"
    assert got["u"][0] == got["j"][0] == "pending"
    assert got["wrong-page"][0] == got["missing"][0] == "error"


def test_usage_checks_after_reading(conn):
    read(conn)
    usage(conn, "u1", "guérir les blessures de l’âme", speaker="Valérie Petit",
          quoted_party="représentants de Torrents de Vie", attribution_mode="direct_quote", stance="criticizes")
    usage(conn, "u2", "60 à 90 %", speaker="unknown")
    data = make_set(
        {"id": "fit", "check": "usage", "anchor": "guérir les blessures",
         "where": {"speaker_any": ["Petit"], "quoted_party_any": ["Torrents de Vie"]}},
        {"id": "not-speaker", "check": "usage", "anchor": "guérir les blessures", "must": "none",
         "where": {"speaker_any": ["Torrents de Vie"]}},
        {"id": "breton", "check": "usage", "anchor": "60 à 90 %", "where": {"speaker_any": ["Breton"]}},
        {"id": "nothing-near", "check": "usage", "anchor": "Mme Valérie Petit", "window": 0},
        {"id": "turn", "check": "same_speaker", "anchors": ["guérir les blessures", "60 à 90"], "window": 0},
        {"id": "either", "check": "usage", "anchor": "guérir les blessures",
         "where": [{"speaker_any": ["Torrents de Vie"]}, {"quoted_party_any": ["Torrents de Vie"]}]})
    got = statuses(conn, data)
    assert got["fit"][0] == "pass"
    assert got["not-speaker"][0] == "pass"
    assert got["breton"][0] == "fail" and "none fit" in got["breton"][1]
    assert got["nothing-near"][0] == "fail"
    assert got["turn"][0] == "fail"                       # second anchor has no attributed usage
    assert got["either"][0] == "pass"                     # second alternative fits


def test_must_none_fails_when_forbidden_usage_exists(conn):
    read(conn)
    usage(conn, "u1", "guérir les blessures de l’âme", speaker="Torrents de Vie")
    got = statuses(conn, make_set({"id": "x", "check": "usage", "anchor": "guérir", "must": "none",
                                  "where": {"speaker_any": ["Torrents de Vie"]}}))
    assert got["x"][0] == "fail"


def test_claims_relations_and_separation(conn):
    read(conn)
    usage(conn, "u1", "guérir les blessures de l’âme", speaker="Petit")
    usage(conn, "u2", "60 à 90 %", speaker="Breton")
    with conn:
        conn.execute("INSERT INTO claim(id, claimant_label, text, factual_status, created_at)"
                     " VALUES ('c1', 'Xavier Breton', '60-90% desist', 'not_assessed', ?),"
                     " ('c2', 'Breton', 'mixed', 'corroborated', ?)", (NOW, NOW))
        conn.execute("INSERT INTO claim_usage VALUES ('c1', 'u2'), ('c2', 'u1'), ('c2', 'u2')")
        conn.execute("INSERT INTO relation(id, subject_label, type, object_label, created_at)"
                     " VALUES ('r1', 'Torrents de Vie', 'authored_by', 'Rapport', ?)", (NOW,))
        conn.execute("INSERT INTO relation_usage(relation_id, usage_id) VALUES ('r1', 'u1')")
    got = statuses(conn, make_set(
        {"id": "claims", "check": "claims_attributed", "pages": [2, 2]},
        {"id": "claims-p1", "check": "claims_attributed", "pages": [1, 1], "min": 1},
        {"id": "norel", "check": "no_relation", "pages": [1, 1], "types": ["authored_by"]},
        {"id": "norel-other", "check": "no_relation", "pages": [1, 1], "types": ["affiliated_with"]},
        {"id": "sep", "check": "separate", "page_groups": [[1, 1], [2, 2]]}))
    assert got["claims"][0] == "fail"                      # c2 is marked corroborated
    assert got["claims-p1"][0] == "fail"
    assert got["norel"][0] == "fail" and got["norel-other"][0] == "pass"
    assert got["sep"][0] == "fail"                         # c2 draws on both pages


def test_duplicate_furniture_and_failure_cases(conn):
    pair = entry(key="dup", urls=[URL_A, URL_B],
                 expect=[{"id": "same", "check": "same_text"}, {"id": "furn", "check": "furniture"},
                         {"id": "none", "check": "no_model_usages"}])
    data = make_set(entries=[pair])
    assert statuses(conn, data)["same"][0] == "pending"   # copies share text; not read yet
    read(conn, URL_A, "r1")
    read(conn, URL_B, "r2")
    usage(conn, "u1", "guérir les blessures de l’âme", speaker="Petit")
    usage(conn, "u2", FOOTER, passage="p12")
    usage(conn, "u3", "60 à 90 %", passage="p21", run="r2", speaker="Breton")
    got = statuses(conn, data)
    assert got["same"][0] == "fail"                        # both copies were read
    assert got["furn"][0] == "fail"                        # footer is in 3 sources, usage counted as body
    assert got["none"][0] == "fail"
    with conn:
        conn.execute("UPDATE usage SET evidence_state = 'recurrent' WHERE id = 'u2'")
    assert statuses(conn, data)["furn"][0] == "pass"


def test_judgements_and_metrics(conn):
    read(conn, seconds=400.0, chars=20_000)                # 20 s per 1,000 characters: over the gate
    usage(conn, "u1", "guérir les blessures de l’âme", speaker="Petit", stance="criticizes")
    usage(conn, "u2", "60 à 90 %", speaker="unknown", stance="unknown")
    usage(conn, "u3", "a quote the model invented", located=False)
    data = make_set({"id": "j", "check": "judged", "criterion": "x"})
    report = score(conn, data, {"j": {"verdict": "pass", "reason": "ok"}})
    assert report["results"][0]["status"] == "pass"
    m = report["metrics"]
    assert m["model_usages"] == 3
    assert m["quotes_located"] == round(2 / 3, 3)
    assert m["speaker_filled"] == 0.5 and m["stance_filled"] == 0.5
    assert m["sources_over_time"] == ["fr"]
    assert report["gate_passed"] is False
    assert score(conn, data)["results"][0]["status"] == "judge"


def test_add_urls_queues_live_and_wayback_once(conn):
    urls = ["https://new.example.org/page/", "https://web.archive.org/web/20130811165248/http://old.example.org/a/"]
    first = add_urls(conn, urls, origin="testset-t")
    assert first["new_sources"] == 2 and first["new_targets"] == 2
    wayback = first["targets"][urls[1]]
    assert wayback["source_id"] == source_id_for("http://old.example.org/a/")
    row = conn.execute("SELECT route, capture_timestamp, legacy_refs FROM source_target WHERE id = ?",
                       (wayback["target_id"],)).fetchone()
    assert tuple(row) == ("wayback", "20130811165248", '["testset-t:002"]')
    again = add_urls(conn, urls, origin="testset-t")
    assert again["new_sources"] == again["new_targets"] == 0


def test_prepare_attaches_package_file_after_checksum(conn, tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    pdf = package / "copy.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    good = hashlib.sha256(pdf.read_bytes()).hexdigest()
    data = make_set(entries=[
        entry(key="a", urls=["https://pkg.example.org/a.pdf"],
              package_files=[{"path": "copy.pdf", "sha256": good, "use": "attach"}]),
        entry(key="b", urls=["https://pkg.example.org/b.pdf"],
              package_files=[{"path": "copy.pdf", "sha256": "0" * 64, "use": "attach"},
                             {"path": "gone.txt", "sha256": "0" * 64, "use": "reference"}])])
    result = prepare(conn, Depot(tmp_path / "data"), data, package_root=package)
    assert result["attached"] == [{"entry": "a", "file": "copy.pdf", "status": "attached"}]
    assert len(result["problems"]) == 2
    route = conn.execute("SELECT acquisition_route FROM source_version WHERE source_id = ?",
                         (source_id_for("https://pkg.example.org/a.pdf"),)).fetchone()[0]
    assert route == "manual_upload"


def test_shipped_testset_is_valid_and_quotes_stay_short():
    """The real file is public: it validates, and no anchor is longer than 12 words."""
    from archive.paths import REPO_ROOT
    from archive.testset import load_testset
    data = load_testset(REPO_ROOT / "evaluation" / "testset_2026-11-10.json")
    assert len(data["entries"]) == 20
    assert sum(e["split"] == "holdout" for e in data["entries"]) == 5
    anchors = [a for e in data["entries"] for x in e["expect"]
               for a in ([x["anchor"]] if "anchor" in x else x.get("anchors", []))]
    assert anchors and max(len(a.split()) for a in anchors) <= 12


def test_time_gate_scales_with_length(conn):
    read(conn, seconds=900.0, chars=150_000)               # a long report: 6 s per 1,000 characters
    report = score(conn, make_set())
    assert report["metrics"]["max_seconds_per_1000_chars"] == 6.0
    assert report["metrics"]["sources_over_time"] == []


def test_speaker_is_measured_on_statements_not_entity_mentions(conn):
    read(conn, chars=1000)
    with conn:
        conn.execute("INSERT INTO entity(id, kind, label, normalized_label, created_at)"
                     " VALUES ('e1', 'person', 'Petit', 'petit', ?)", (NOW,))
    usage(conn, "u1", "guérir les blessures de l’âme", speaker="Petit", stance="criticizes")
    usage(conn, "u2", "60 à 90 %", entity_id="e1", stance="not_applicable")   # a name, no voice
    m = score(conn, make_set())["metrics"]
    assert m["speaker_filled"] == 1.0 and m["entity_mentions"] == 1
