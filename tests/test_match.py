import json

import pytest

from archive import db
from archive.match import LabelSpec, build_match_report, eligible_labels, label_pattern, load_rules, run_match

NOW = "2026-11-09T12:00:00Z"
RULES = {"allowed_short_acronyms": {"SSA": "core"}, "context_required": {"gay": "identity word"}}


def spec(label, acronym=False):
    return LabelSpec(label, "term:x", None, acronym)


@pytest.mark.parametrize("label, text, hit", [
    ("ex-gay", "an Ex Gay ministry", "Ex Gay"),
    ("ex-gay", "the ex‑gay movement", "ex‑gay"),
    ("Same-Sex Attraction", "his same-sex\nattraction was", "same-sex\nattraction"),
    ("you're broken", "You’re broken, they said", "You’re broken"),
    ("pray the gay away", "They prayed the gay away", None),          # word changed: no match
    ("SSA", "struggling with SSA daily", "SSA"),
    ("SSA", "the ssa office", None),                                  # acronyms keep capitals
    ("SSA", "USSA is different", None),                               # not inside a word
    ("conversão", "terapia de conversao", None),                      # no accent folding
])
def test_label_pattern(label, text, hit):
    found = label_pattern(spec(label, label.isupper())).search(text)
    assert (found.group(0) if found else None) == hit


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "a.db")
    db.init_db(connection)
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps(RULES))
    texts = {
        "s1": "They spoke of unwanted same-sex attraction and of SSA. Footer: Donate to Example Ministry today.",
        "s2": "Critics call SSA a euphemism. Footer: Donate to Example Ministry today.",
        "s3": "A gay man left the IFTCC conference. Footer: Donate to Example Ministry today.",
    }
    with connection:
        connection.execute("INSERT INTO concept(id, scheme, pref_label, created_at) VALUES"
                           " ('term:ssa','term','SSA',?), ('term:ussa','term','USSA',?),"
                           " ('term:gay','term','Gay',?), ('term:donate','term','Example Ministry',?)",
                           (NOW, NOW, NOW, NOW))
        for i, (cid, label, kind) in enumerate([("term:ssa", "SSA", "pref"), ("term:ssa", "Same-Sex Attraction", "alt"),
                                               ("term:ussa", "Unwanted Same-Sex Attraction", "alt"),
                                               ("term:gay", "Gay", "pref"), ("term:donate", "Example Ministry", "pref")]):
            connection.execute("INSERT INTO concept_label(id, concept_id, label, normalized_label, language, kind,"
                               " created_at) VALUES (?,?,?,?, 'en', ?, ?)", (f"l{i}", cid, label, label.lower(), kind, NOW))
        connection.execute("INSERT INTO entity(id, kind, label, normalized_label, created_at)"
                           " VALUES ('organization:iftcc', 'organization', 'IFTCC', 'iftcc', ?)", (NOW,))
        connection.execute("INSERT INTO entity_variant(id, entity_id, label, normalized_label, kind, created_at)"
                           " VALUES ('v1', 'organization:iftcc', 'IFTCC', 'iftcc', 'pref', ?)", (NOW,))
        for n, (sid, text) in enumerate(texts.items(), 1):
            connection.execute("INSERT INTO source(id, url, published_date, published_date_certainty, created_at)"
                               " VALUES (?,?, '2019', 'exact', ?)", (sid, f"https://e{n}.org/", NOW))
            connection.execute("INSERT INTO source_version(id, source_id, sha256, path, acquisition_route, created_at)"
                               " VALUES (?,?,?, 'x', 'live_fetch', ?)", (f"v{n}", sid, f"{n}" * 64, NOW))
            body, footer = text.split(" Footer: ")
            body += " "
            footer = "Footer: " + footer
            for seq, (start, chunk) in enumerate([(0, body), (len(body), footer)], 1):
                from archive.passages import sha256_text
                connection.execute(
                    "INSERT INTO passage(id, version_id, sequence, char_start, char_end, text, text_sha256, language,"
                    " created_at) VALUES (?,?,?,?,?,?,?, 'en', ?)",
                    (f"p{n}{seq}", f"v{n}", seq, start, start + len(chunk), chunk, sha256_text(chunk), NOW))
    yield connection, rules
    connection.close()


def test_eligible_labels(conn):
    connection, rules = conn
    specs, excluded = eligible_labels(connection, load_rules(rules))
    assert {s.label for s in specs} == {"SSA", "Same-Sex Attraction", "Unwanted Same-Sex Attraction",
                                        "Example Ministry", "IFTCC"}
    assert excluded == [{"label": "Gay", "target": "term:gay", "reason": "context_required"}]


def test_run_match(conn):
    connection, rules = conn
    report = run_match(connection, rules_path=rules, log=lambda line: None)
    usages = {(r["passage_id"], r["quote"], r["concept_id"] or r["entity_id"]) for r in
              connection.execute("SELECT * FROM usage")}
    assert ("p11", "unwanted same-sex attraction", "term:ussa") in usages
    assert not any(q == "same-sex attraction" for _, q, _ in usages)         # inside the longer match
    assert ("p11", "SSA", "term:ssa") in usages and ("p21", "SSA", "term:ssa") in usages
    assert ("p31", "IFTCC", "organization:iftcc") in usages
    row = connection.execute("SELECT * FROM usage WHERE passage_id = 'p11' AND concept_id = 'term:ssa'").fetchone()
    passage = connection.execute("SELECT text FROM passage WHERE id = 'p11'").fetchone()[0]
    assert passage[row["char_start"]:row["char_end"]] == row["quote"] == "SSA"
    assert (row["date"], row["found_by"], row["stance"], row["language"]) == ("2019", "exact_match", "unknown", "en")
    # the footer repeats in 3 sources: furniture, counted apart, no evidence
    assert report["usages_in_furniture"] == 3
    states = dict(connection.execute("SELECT id, evidence_state FROM concept").fetchall())
    assert states == {"term:ssa": "recurrent", "term:ussa": "located", "term:gay": "unlocated",
                      "term:donate": "unlocated"}
    assert connection.execute("SELECT evidence_state FROM entity").fetchone()[0] == "located"
    first = connection.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    again = run_match(connection, rules_path=rules, log=lambda line: None)
    assert connection.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == first
    assert again["usages_replaced"] == first
    top = {r["id"]: r for r in build_match_report(connection)["top_concepts"]}
    assert top["term:ssa"]["sources"] == 2 and top["term:donate"]["furniture_usages"] == 3


def test_inside_url():
    from archive.match import inside_url
    text = "See jonahweb.org/library/model-for-healing-homosexuality.html (visited). Healing homosexuality is"
    first = text.index("healing-homosexuality")
    second = text.index("Healing homosexuality")
    assert inside_url(text, first, first + 21)
    assert not inside_url(text, second, second + 21)
    assert inside_url("at https://x.org/ssa-page now", 18, 21)
