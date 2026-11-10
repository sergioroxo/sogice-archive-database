import dataclasses
import json

import pytest

from archive import db, read, reconcile
from tests.test_read import ROLE, SID, TEXT_A, TEXT_B, FakeClient, add_source, reply, statement

SECOND = dataclasses.replace(ROLE, model="second-model:2b")
NOW = "2026-11-10T12:00:00Z"


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "a.db")
    db.init_db(connection)
    with connection:
        add_source(connection, "https://example.org/exodus", [TEXT_A, TEXT_B])
    yield connection
    connection.close()


def first_reply():
    return reply(
        statements=[statement("P1", "We are closing the ministry today.", "Alan Chambers", claim=True),
                    statement("P1", "local affiliated ministries will continue their work", "Exodus",
                              attribution="reported_speech"),
                    statement("P2", "a new generation is looking for change", "Tony Moore", stance="endorses")],
        relations=[{"subject": "Alan Chambers", "type": "employed_by", "object": "Exodus", "stated_by": "x",
                    "unit": "P1", "quote": "Alan Chambers, president of Exodus"}])


def second_reply():
    return reply(
        statements=[statement("P1", "We are closing the ministry", "Chambers, Exodus president", claim=True),
                    statement("P2", "a new generation is looking for change", "Exodus", stance="criticizes"),
                    statement("P2", "Exodus was founded in 1976.", "Exodus", stance="reports")],
        relations=[{"subject": "Alan Chambers", "type": "affiliated_with", "object": "Exodus", "stated_by": "x",
                    "unit": "P1", "quote": "Alan Chambers, president of Exodus"}])


def read_both(conn):
    read.read_sources(conn, FakeClient([first_reply()]), ROLE, [SID])
    read.read_sources(conn, FakeClient([second_reply()]), SECOND, [SID])
    return reconcile.reconcile(conn, [SID], primary_model=ROLE.model)


def assessment(conn, usage_quote, model):
    row = conn.execute("SELECT a.* FROM assessment a JOIN usage u ON u.id = a.target_id JOIN run r ON r.id = u.run_id"
                       " WHERE a.current = 1 AND a.target_table = 'usage' AND u.quote LIKE ? AND r.model = ?",
                       (usage_quote + "%", model)).fetchone()
    return dict(row) if row else None


def test_second_reading_is_a_layer_not_a_replacement(conn):
    read.read_sources(conn, FakeClient([first_reply()]), ROLE, [SID])
    results = read.read_sources(conn, FakeClient([second_reply()]), SECOND, [SID])
    assert results[0]["superseded_runs"] == 0                      # another model: nothing replaced
    again = read.read_sources(conn, FakeClient([first_reply()]), ROLE, [SID], again=True)
    assert again[0]["superseded_runs"] == 1                        # the same model reading again replaces itself
    assert len(reconcile.current_readings(conn, SID)) == 2


def test_agreement_disagreement_and_single_reader(conn):
    result = read_both(conn)
    # agree: same statement, the second reader names Chambers differently
    agree = assessment(conn, "We are closing", ROLE.model)
    assert agree["confidence"] == "two_readers_agree"
    assert json.loads(agree["proposed_by"]) == sorted([ROLE.model, SECOND.model])
    assert {r["model"] for r in json.loads(agree["readers"])} == {ROLE.model, SECOND.model}
    assert all(r["read_at"] and r["prompt"] for r in json.loads(agree["readers"]))
    folded = conn.execute("SELECT review_state FROM usage WHERE id = ?", (agree["counterpart_id"],)).fetchone()[0]
    assert folded == "superseded"                                  # counted once
    assert conn.execute("SELECT review_state FROM usage WHERE id = ?", (agree["target_id"],)).fetchone()[0] == "proposed"
    # the claim of the folded copy follows it; the primary's claim stands
    claims = dict(conn.execute("SELECT u.id, c.review_state FROM claim c JOIN claim_usage cu ON cu.claim_id = c.id"
                               " JOIN usage u ON u.id = cu.usage_id").fetchall())
    assert claims[agree["target_id"]] == "proposed" and claims[agree["counterpart_id"]] == "superseded"
    # disagree: different speakers and opposite stances -> both disputed, nobody asserted
    clash = assessment(conn, "a new generation", ROLE.model)
    assert clash["confidence"] == "readers_disagree"
    assert set(json.loads(clash["differences"])) >= {"speaker", "stance"}
    states = {r[0] for r in conn.execute("SELECT review_state FROM usage WHERE quote LIKE 'a new generation%'")}
    assert states == {"disputed"}
    # one reader: found only by the first or only by the second
    assert assessment(conn, "local affiliated", ROLE.model)["confidence"] == "one_reader"
    only_second = assessment(conn, "Exodus was founded", SECOND.model)
    assert only_second["confidence"] == "one_reader" and json.loads(only_second["proposed_by"]) == [SECOND.model]
    # relations: same names, different types -> disputed
    rel = {r[0] for r in conn.execute("SELECT review_state FROM relation")}
    assert rel == {"disputed"}
    assert result["counts"]["usage:readers_disagree"] == 2
    actions = {r[0] for r in conn.execute("SELECT action FROM decision WHERE actor_id = ?", (reconcile.ACTOR_ID,))}
    assert actions == {"merge", "dispute", "audit"}
    assert db.verify_decision_chain(conn).ok


def test_reconciling_again_gives_the_same_result(conn):
    first = read_both(conn)["counts"]
    second = reconcile.reconcile(conn, [SID], primary_model=ROLE.model)["counts"]
    assert first == second
    current = conn.execute("SELECT COUNT(*) FROM assessment WHERE current = 1").fetchone()[0]
    assert current == sum(first.values())
    assert conn.execute("SELECT COUNT(*) FROM assessment WHERE current = 0").fetchone()[0] == current


def test_researcher_decision_is_not_undone(conn):
    read_both(conn)
    target = assessment(conn, "local affiliated", ROLE.model)["target_id"]
    with conn:
        conn.execute("UPDATE usage SET review_state = 'rejected' WHERE id = ?", (target,))
        db.record_decision(conn, actor_kind="researcher", actor_id="sergio", action="reject", target_table="usage",
                           target_id=target, reason="not a statement")
    reconcile.reconcile(conn, [SID], primary_model=ROLE.model)
    assert conn.execute("SELECT review_state FROM usage WHERE id = ?", (target,)).fetchone()[0] == "rejected"


def test_single_reading_is_labelled(conn):
    read.read_sources(conn, FakeClient([first_reply()]), ROLE, [SID])
    counts = reconcile.reconcile(conn, [SID], primary_model=ROLE.model)["counts"]
    assert counts == {"relation:single_reading": 1, "usage:single_reading": 4}   # 3 statements + the relation's quote


def test_scorer_ignores_disputed_and_reports_confidence(conn):
    from archive import testset
    read_both(conn)
    view = testset.entry_view(conn, {"urls": ["https://example.org/exodus"]})
    assert not any(u["quote"].startswith("a new generation") for u in view.usages)
    assert view.disputed == 2 and len(view.runs) == 2
    data = {"version": "t", "gate": {"seconds_per_1000_chars": 15},
            "entries": [{"key": "e", "split": "dev", "urls": ["https://example.org/exodus"], "expect": []}]}
    m = testset.score(conn, data)["metrics"]
    assert m["disputed_statements"] == 2
    assert m["confidence"]["two_readers_agree"] > 0 and m["confidence"]["one_reader"] > 0


def test_difficulties_report_names_the_disagreements(conn):
    read_both(conn)
    report = reconcile.difficulties(conn)
    assert report["disagreements_by_field"]["speaker"] == 1
    assert report["disagreements_by_field"]["type"] == 1
    text = reconcile.difficulties_markdown(report)
    assert "a new generation is looking for change" in text and "Found by one reader only" in text


@pytest.mark.parametrize("a, b, same", [
    ("Alan Chambers", "Chambers, Exodus president", True),
    ("Mme Laurence Vanceunebrock, rapporteure", "Mme la rapporteure", False),   # v3: only with the source's aliases
    ("Mme Laurence Vanceunebrock, rapporteure", "M. Xavier Breton, rapporteur", False),  # a shared role is not a name
    ("Jack Sterling", "Christian Concern", False),
    ("unknown", "unknown", True),
    ("unknown", "Exodus", False),
])
def test_same_speaker(a, b, same):
    assert reconcile.same_speaker(a, b) is same


OLD = dataclasses.replace(ROLE, model="old-model:1b")


def test_only_the_named_readers_are_compared_and_old_changes_are_undone(conn):
    # an earlier reconciliation compared the primary with an old model
    read.read_sources(conn, FakeClient([second_reply()]), OLD, [SID])
    read.read_sources(conn, FakeClient([first_reply()]), ROLE, [SID])
    reconcile.reconcile(conn, [SID], primary_model=ROLE.model, models=[ROLE.model, OLD.model])
    old_run = reconcile.current_readings(conn, SID, [OLD.model])[0]["id"]
    changed = {r[0] for r in conn.execute("SELECT review_state FROM usage WHERE run_id = ?", (old_run,))}
    assert changed & {"disputed", "superseded"}
    # the second reader arrives; three models have read the source
    read.read_sources(conn, FakeClient([second_reply()]), SECOND, [SID])
    with pytest.raises(ValueError):
        reconcile.reconcile(conn, [SID], primary_model=ROLE.model)
    result = reconcile.reconcile(conn, [SID], primary_model=ROLE.model, models=[ROLE.model, SECOND.model])
    assert result["counts"]["undone"] > 0
    assert {r[0] for r in conn.execute("SELECT review_state FROM usage WHERE run_id = ?", (old_run,))} == {"proposed"}
    assert {r[0] for r in conn.execute("SELECT c.review_state FROM claim c JOIN claim_usage cu ON cu.claim_id = c.id"
                                       " JOIN usage u ON u.id = cu.usage_id WHERE u.run_id = ?", (old_run,))} == {"proposed"}
    readers = {r["model"] for r in json.loads(assessment(conn, "We are closing", ROLE.model)["readers"])}
    assert readers == {ROLE.model, SECOND.model}
    assert result["counts"]["usage:readers_disagree"] == 2
    assert db.verify_decision_chain(conn).ok
    report = reconcile.difficulties(conn)
    assert sum(c["n"] for c in report["corrections"]) == result["counts"]["undone"]
    assert "Corrections to earlier reconciliations" in reconcile.difficulties_markdown(report)


def test_a_superseded_run_takes_no_part_and_keeps_its_state(conn):
    read.read_sources(conn, FakeClient([second_reply()]), OLD, [SID])
    old_run = reconcile.current_readings(conn, SID)[0]["id"]
    with conn:  # another model's reading replaced it (as the reader did before readings became layers)
        conn.execute("UPDATE usage SET review_state = 'superseded' WHERE run_id = ?", (old_run,))
        db.record_decision(conn, actor_kind="model", actor_id=ROLE.model, run_id=old_run, action="supersede",
                           target_table="run", target_id=old_run, reason="read again")
    read_both(conn)
    assert old_run not in {r["id"] for r in reconcile.current_readings(conn, SID)}
    assert {r[0] for r in conn.execute("SELECT review_state FROM usage WHERE run_id = ?", (old_run,))} == {"superseded"}


def test_restore_returns_to_the_recorded_earlier_state(conn):
    read_both(conn)
    folded = assessment(conn, "We are closing", ROLE.model)["counterpart_id"]
    last = conn.execute("SELECT previous_state FROM decision WHERE target_id = ? AND action = 'merge'",
                        (folded,)).fetchone()[0]
    assert json.loads(last) == {"review_state": "proposed"}


def test_v3_resolves_a_role_only_label_to_the_one_person_with_that_role():
    labels = ["Mme Laurence Vanceunebrock, rapporteure", "Mme Naïma Moutchou, présidente", "M. Xavier Breton"]
    aliases = reconcile.speaker_aliases(labels)
    assert aliases == {"rapporteure": "Mme Laurence Vanceunebrock", "presidente": "Mme Naïma Moutchou"}
    a = {"speaker": "Mme la rapporteure", "stance": "criticizes", "intermediary": ""}
    b = {"speaker": "Mme Laurence Vanceunebrock, rapporteure", "stance": "criticizes",
         "intermediary": "assemblee-nationale.fr"}
    clash, differences = reconcile.compare(a, b, aliases=aliases, site=["assemblee-nationale.fr"])
    assert not clash and "intermediary" not in differences            # the hosting site says nothing
    assert reconcile.compare(a, b)[0]                                  # without the source's aliases: two labels
    two = reconcile.speaker_aliases(labels + ["M. Raphaël Gérard, rapporteure"])
    assert "rapporteure" not in two                                    # two people with the role: no alias


def test_v3_two_names_of_one_registry_entity_are_one_speaker():
    registry = {"american psychological association": "org:apa", "apa": "org:apa"}
    a = {"speaker": "APA", "stance": "criticizes", "intermediary": ""}
    b = {"speaker": "American Psychological Association", "stance": "criticizes", "intermediary": ""}
    assert not reconcile.compare(a, b, registry=registry)[0]
    assert reconcile.compare(a, b)[0]


def test_v3_one_person_spelt_two_ways_is_still_one_alias():
    aliases = reconcile.speaker_aliases(["Mme Laurence Vanceunebrock, rapporteure", "Laurence Vanceunebrock, rapporteure"])
    assert aliases == {"rapporteure": "Mme Laurence Vanceunebrock"}
