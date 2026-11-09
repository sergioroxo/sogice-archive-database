import json

import pytest

from archive import db
from archive.seeds import (SEEDS, expands, load_all, normalize_label, parse_glossary_term, parse_lexicon,
                           parse_registry, split_labels)

LEXICON = """# Lexicon
## SECTION 1 — SSA-Rhetoric (C1)

**SSA** (Same-Sex Attraction)
- Cluster: SSA-Rhetoric | Function: Euphemism
- Definition: The foundational euphemism.
- Related: → USSA, → "Struggling with SSA", → Gender-Essentialism, → Missing Thing

**Pastoral Support (legislative loophole)**
- Cluster: Pastoral-Coercion | Function: Euphemism
- Definition: Loophole term.
- Related: → SSA

## SECTION 8 — SLURS AND HARMFUL TERMINOLOGY

**Maricón / Joto** — Anti-Gender | Slur. Regional Spanish-language slurs for gay men.

## SECTION 10 — SHORTHAND AND ACRONYMS

| Acronym | Full form | Context |
|---|---|---|
| USSA | Unwanted Same-Sex Attraction | SOGICE suppression |
| IFTCC | International Federation for Therapeutic and Counselling Choice | Pro-SOGICE body |

## SECTION 11 — MULTILINGUAL QUICK REFERENCE

| English | Norwegian | Italian |
|---|---|---|
| Conversion Therapy | konverteringsterapi | terapie di conversione |

**Italian:** Cristoterapia — "Christ therapy," rebranding (C2) · accompagnamento (T2, pastoral loophole, C2)

## SECTION 13 — GOV.UK

**Aromantic**
- Cluster: Non-SOGICE | Function: Unknown
- Definition: Ignored section.
"""

REGISTRY = """## PART 1 — ORGANIZATIONS

**IFTCC** (International Federation for Therapeutic and Counselling Choice)
- Type: pseudo-professional-body
- Country: UK
- Description: Umbrella body.

**HBRS** (Norwegian detrans organization)
- Country: Norway

**Alliance for Therapeutic Choice and Scientific Integrity**
- Country: USA
- Former names: [{name: "NARTH", from_year: 1992, to_year: 2014}]

## PART 2 — PERSONS

**Joseph Nicolosi†**
- Role: therapist

**Dr. Sybrand de Vaal**
- Role: psychiatrist
"""


def test_label_helpers():
    assert normalize_label("“Ex‑Gay”  Ministry.") == "ex gay ministry"
    assert expands("IFTCC", "International Federation for Therapeutic and Counselling Choice")
    assert not expands("HBRS", "Norwegian detrans organization")
    assert split_labels("Pastoral Support (legislative loophole)") == (["Pastoral Support"], None)
    assert split_labels("Accompagnamento (IT)") == (["Accompagnamento"], "it")
    assert split_labels("LGB Alliance (UK)") == (["LGB Alliance"], None)
    assert parse_glossary_term("CT / TK – ‘terapija ta’ konverżjoni’") == ["CT", "TK", "terapija ta’ konverżjoni"]
    assert parse_glossary_term("elettroshock (ES) / shock elettroconvulsivo (ECS)") == [
        "elettroshock", "shock elettroconvulsivo", "ES", "ECS"]
    assert parse_glossary_term("Former LGBT (ex‑gay / ex‑trans)") == ["Former LGBT"]


def test_parse_lexicon():
    parsed = parse_lexicon(LEXICON)
    entries = {e.labels[0]: e for e in parsed["entries"]}
    assert set(entries) == {"SSA", "Pastoral Support", "Maricón"}               # section 13 ignored
    assert entries["SSA"].labels == ["SSA", "Same-Sex Attraction"] and entries["SSA"].notation == "C1"
    assert entries["SSA"].related == ["USSA", "Struggling with SSA", "Gender-Essentialism", "Missing Thing"]
    assert entries["Maricón"].labels == ["Maricón", "Joto"] and entries["Maricón"].language == "es"
    assert entries["Maricón"].notation == "slur"
    assert parsed["acronyms"][0] == ("USSA", "Unwanted Same-Sex Attraction", "SOGICE suppression")
    assert parsed["translations"] == [{"en": "Conversion Therapy", "no": "konverteringsterapi",
                                       "it": "terapie di conversione"}]
    assert parsed["language_terms"] == [("Cristoterapia", "it"), ("accompagnamento", "it")]


def test_parse_registry():
    entries = {e.labels[0]: e for e in parse_registry(REGISTRY)}
    assert entries["IFTCC"].labels == ["IFTCC", "International Federation for Therapeutic and Counselling Choice"]
    assert entries["IFTCC"].country == "GB" and entries["IFTCC"].kind == "organization"
    assert entries["HBRS"].labels == ["HBRS"] and entries["HBRS"].country == "NO"
    assert entries["Alliance for Therapeutic Choice and Scientific Integrity"].former == [("NARTH", "1992", "2014")]
    assert entries["Joseph Nicolosi"].kind == "person"
    assert entries["Sybrand de Vaal"].labels == ["Sybrand de Vaal", "Dr. Sybrand de Vaal"]


@pytest.fixture
def seeds_dir(tmp_path):
    folder = tmp_path / "seeds"
    folder.mkdir()
    (folder / "SOGICE_Lexicon_v2.1.md").write_text(LEXICON)
    (folder / "Entity_Registry_v1.1.md").write_text(REGISTRY)
    (folder / "SOGICE_Ontology_v3.0.md").write_text("### Function Tags\n\n`Function: Slur` | `Function: Euphemism`\n\n")
    (folder / "sanity_seed_2026-05-05.json").write_text(json.dumps({
        "tacticEntry": [
            {"_id": "tactic-gender-essentialism-religious", "tactic": "Gender-Essentialism (religious)",
             "parentTactic": {"_ref": "tactic-gender-essentialism"}, "approvedBy": "researcher"},
            {"_id": "tactic-gender-essentialism", "tactic": "Gender-Essentialism", "approvedBy": "researcher",
             "definition": "Sex roles as fixed.", "primaryCluster": "Anti-Gender"}],
        "practiceEntry": [{"_id": "practice-deliverance", "practice": "Deliverance", "definition": "Prayer."}]}))
    (folder / "govuk_conversion_therapy_glossary_2021.json").write_text(json.dumps({"terms": [
        {"source_id": "govuk-2021-conversion-therapy", "source_term": "Conversion therapy",
         "canonical_term": "Conversion Therapy", "source_definition": "x"},
        {"source_id": "govuk-2021-asexual", "source_term": "Asexual", "canonical_term": "Asexual",
         "source_definition": "No sexual attraction."}]}))
    (folder / "sogice_glossary_2026-04-03.json").write_text(json.dumps({"entries": [
        {"term": "pastoral support", "language": "en"},
        {"term": "living chastely", "language": "en", "draft_definition": "Celibacy frame.",
         "proposed_category": "euphemism"},
        {"term": "SSA – azonos nemhez vonzódás", "language": "hu"},
        {"term": "self-centered way", "language": "en"}]}))
    (folder / "merge_map_2026-11-09.json").write_text(json.dumps({
        "decided_by": "claude-opus-5-5 (test)", "prompt_version": "seed-merge-v1",
        "new_concepts": {},
        "entries": {"SSA – azonos nemhez vonzódás": {"action": "label_of", "concept": "term:ssa",
                                                     "reason": "Hungarian SSA."},
                    "self-centered way": {"action": "skip", "reason": "Fragment."}}}))
    return folder


def test_load_all(tmp_path, seeds_dir):
    conn = db.connect(tmp_path / "a.db")
    db.init_db(conn)
    result = load_all(conn, seeds_dir)
    concept = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM concept")}
    assert concept["term:ssa"]["review_state"] == "confirmed" and concept["term:ssa"]["notation"] == "C1"
    assert concept["term:living-chastely"]["definition_source"] == "model"
    assert concept["tactic:gender-essentialism-religious"]["broader"] == "tactic:gender-essentialism"
    labels = {(r["concept_id"], r["label"], r["language"]) for r in conn.execute("SELECT * FROM concept_label")}
    assert ("term:ssa", "Same-Sex Attraction", "en") in labels
    assert ("term:ssa", "azonos nemhez vonzódás", "hu") in labels
    assert ("term:conversion-therapy", "terapie di conversione", "it") in labels
    assert ("term:ussa", "Unwanted Same-Sex Attraction", "en") in labels
    assert not any(label == "self-centered way" for _, label, _ in labels)
    related = {(r["concept_id"], r["target_concept_id"]) for r in conn.execute(
        "SELECT * FROM concept_relation WHERE relation = 'related'")}
    assert ("term:ssa", "tactic:gender-essentialism") in related           # resolved to a tactic
    assert ("term:ssa", "term:missing-thing") in related
    assert result["placeholders"] == ["Struggling with SSA", "Missing Thing"]   # no entries for these
    external = {r["concept_id"] for r in conn.execute("SELECT * FROM concept_relation WHERE relation = 'exactMatch'")}
    assert external == {"term:conversion-therapy", "term:asexual"}
    iftcc = conn.execute("SELECT * FROM entity WHERE id = 'organization:iftcc'").fetchone()
    assert iftcc["country"] == "GB"
    former = conn.execute("SELECT * FROM entity_variant WHERE kind = 'former_name'").fetchone()
    assert (former["label"], former["valid_from"], former["valid_to"]) == ("NARTH", "1992", "2014")
    decisions = conn.execute("SELECT * FROM decision").fetchall()
    assert len(decisions) == 1 and decisions[0]["actor_kind"] == "model"
    assert decisions[0]["reason"] == "Hungarian SSA." and decisions[0]["run_id"] == result["run_id"]
    assert db.verify_decision_chain(conn).ok
    again = load_all(conn, seeds_dir)
    assert {k: v for k, v in again["stats"].items() if v and not k.startswith("glossary_")} == {}
    assert conn.execute("SELECT COUNT(*) FROM decision").fetchone()[0] == 1
    conn.close()


def test_real_seed_files_load(tmp_path):
    conn = db.connect(tmp_path / "real.db")
    db.init_db(conn)
    result = load_all(conn, SEEDS)
    stats = result["stats"]
    assert stats["concepts_tactic"] == 44 and stats["concepts_practice"] == 14
    assert stats["concepts_term"] > 250 and stats["decisions"] > 100
    assert len(result["placeholders"]) < 10
    assert db.verify_decision_chain(conn).ok
    conn.close()


def test_merge_concepts_is_recorded_and_reversible(tmp_path):
    from archive.seeds import merge_concepts
    conn = db.connect(tmp_path / "m.db")
    db.init_db(conn)
    now = "2026-11-09T12:00:00Z"
    with conn:
        conn.execute("INSERT INTO concept(id, scheme, pref_label, created_at) VALUES ('term:a','term','GICE long',?),"
                     " ('term:b','term','GICE long',?)", (now, now))
        conn.execute("INSERT INTO concept_label(id, concept_id, label, normalized_label, language, kind, created_at)"
                     " VALUES ('la','term:a','GICE long','gice long','en','pref',?), ('lb1','term:b','GICE long',"
                     "'gice long','en','pref',?), ('lb2','term:b','GICE','gice','en','alt',?)", (now, now, now))
        run = "run_x"
        conn.execute("INSERT INTO run(id, role, model, started_at) VALUES (?, 'other', 'claude', ?)", (run, now))
        decisions = merge_concepts(conn, "term:b", "term:a", reason="duplicate", actor_kind="model",
                                   actor_id="claude", run_id=run)
    assert [d["action"] for d in decisions] == ["attach", "merge"]
    labels = {(r["concept_id"], r["label"], r["review_state"]) for r in conn.execute("SELECT * FROM concept_label")}
    assert ("term:a", "GICE", "proposed") in labels and ("term:b", "GICE", "superseded") in labels
    assert conn.execute("SELECT review_state FROM concept WHERE id='term:b'").fetchone()[0] == "superseded"
    assert db.verify_decision_chain(conn).ok
