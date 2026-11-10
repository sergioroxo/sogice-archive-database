import json

import pytest

from archive import db, read, reconcile, review, testset
from tests.test_read import ROLE, SID, FakeClient
from tests.test_reconcile import SECOND, first_reply, read_both

REVIEWER = "reviewer-model:x"


@pytest.fixture
def conn(tmp_path):
    from tests.test_read import TEXT_A, TEXT_B, add_source
    connection = db.connect(tmp_path / "a.db")
    db.init_db(connection)
    with connection:
        add_source(connection, "https://example.org/exodus", [TEXT_A, TEXT_B])
    yield connection
    connection.close()


@pytest.fixture
def queue(tmp_path):
    return tmp_path / "review"


def keys(queue, kind=None):
    out = [json.loads(p.read_text()) for p in sorted((queue / "keys").glob("*.json"))]
    return [k for k in out if kind is None or k["kind"] == kind]


def state(conn, table, record_id):
    return conn.execute(f"SELECT review_state FROM {table} WHERE id = ?", (record_id,)).fetchone()[0]


def write_verdict(queue, key, **verdict):
    (queue / "verdicts" / f"{key['issue']}.json").write_text(json.dumps({"issue": key["issue"], **verdict}))


def label_of(conn, key, speaker_like):
    """The label (A/B) whose record has this speaker (tests may read the key; reviewers may not)."""
    for label, info in key["labels"].items():
        speaker = conn.execute("SELECT speaker FROM usage WHERE id = ?", (info["id"],)).fetchone()[0]
        if speaker_like in speaker:
            return label
    raise AssertionError(speaker_like)


def test_one_reader_relations_are_held_from_publication(conn):
    read.read_sources(conn, FakeClient([first_reply()]), ROLE, [SID])
    reconcile.reconcile(conn, [SID], primary_model=ROLE.model)
    rel = conn.execute("SELECT id FROM relation").fetchone()[0]
    assert review.held_relation_ids(conn) == {rel}
    view = testset.entry_view(conn, {"urls": ["https://example.org/exodus"]})
    assert view.relations == [] and view.held_relations == 1


def test_prepare_writes_blind_packets_once(conn, queue):
    read_both(conn)
    result = review.prepare(conn, base=queue)
    assert result["written"] == {"statement_dispute": 1, "relation_dispute": 1}
    packets = list((queue / "open").glob("*.md"))
    assert sorted(p.name[0] for p in packets) == ["1", "2"]          # employment relation first
    for p in packets:
        text = p.read_text()
        assert ROLE.model not in text and SECOND.model not in text   # blind
        assert "⟦" in text and "## Reading A" in text and "## Reading B" in text
    statement_key = keys(queue, "statement_dispute")[0]
    assert {i["model"] for i in statement_key["labels"].values()} == {ROLE.model, SECOND.model}
    assert review.prepare(conn, base=queue)["written"] == {}          # nothing new


def test_apply_confirms_the_chosen_reading_and_rejects_the_other(conn, queue):
    read_both(conn)
    review.prepare(conn, base=queue)
    key = keys(queue, "statement_dispute")[0]
    right = label_of(conn, key, "Tony Moore")
    write_verdict(queue, key, verdict=right, quote="Board member Tony Moore said", confidence="high",
                  reason="Moore says it; the text gives no stance against change.", pattern="speaker-to-organisation")
    result = review.apply(conn, reviewer=REVIEWER, base=queue)
    assert result["counts"]["confirmed"] == 1 and not result["problems"]
    chosen, other = key["labels"][right]["id"], key["labels"]["B" if right == "A" else "A"]["id"]
    assert state(conn, "usage", chosen) == "confirmed" and state(conn, "usage", other) == "rejected"
    actions = {r[0] for r in conn.execute("SELECT action FROM decision WHERE actor_id = ?", (REVIEWER,))}
    assert actions == {"confirm", "reject"}
    assert not list((queue / "open").glob(f"*{key['issue']}*"))
    assert (queue / "done" / f"{key['issue']}.verdict.json").exists()
    # reconciling again leaves the ruling alone, and the issue is gone
    reconcile.reconcile(conn, [SID], primary_model=ROLE.model, models=[ROLE.model, SECOND.model])
    assert state(conn, "usage", chosen) == "confirmed"
    assert key["issue"] not in {i.id for i in review.find_issues(conn)}
    assert db.verify_decision_chain(conn).ok


def test_a_quote_not_in_the_text_is_not_applied(conn, queue):
    read_both(conn)
    review.prepare(conn, base=queue)
    key = keys(queue, "statement_dispute")[0]
    write_verdict(queue, key, verdict="A", quote="words that are not there", confidence="high", reason="x")
    result = review.apply(conn, reviewer=REVIEWER, base=queue)
    assert "not in the source text" in result["problems"][0]["problem"]
    assert {state(conn, "usage", i["id"]) for i in key["labels"].values()} == {"disputed"}
    assert list((queue / "open").glob(f"*{key['issue']}*"))


def test_low_confidence_goes_to_the_researcher(conn, queue):
    read_both(conn)
    review.prepare(conn, base=queue)
    key = keys(queue, "statement_dispute")[0]
    write_verdict(queue, key, verdict="A", quote="Tony Moore", confidence="low", reason="unsure")
    review.apply(conn, reviewer=REVIEWER, base=queue)
    assert {state(conn, "usage", i["id"]) for i in key["labels"].values()} == {"disputed"}
    assert (queue / "researcher" / f"{key['issue']}.verdict.json").exists()
    verdicts = {r[0] for r in conn.execute("SELECT audit_verdict FROM decision WHERE actor_id = ?", (REVIEWER,))}
    assert verdicts == {"uncertain"}


def test_neither_with_a_correction_edits_reading_a(conn, queue):
    read_both(conn)
    review.prepare(conn, base=queue)
    key = keys(queue, "relation_dispute")[0]
    write_verdict(queue, key, verdict="neither", correct={"type": "employed_by"}, confidence="high",
                  quote="Alan Chambers, president of Exodus", reason="A stated role is employed_by.")
    review.apply(conn, reviewer=REVIEWER, base=queue)
    a, b = key["labels"]["A"]["id"], key["labels"]["B"]["id"]
    assert conn.execute("SELECT type, review_state FROM relation WHERE id = ?", (a,)).fetchone()[:] == ("employed_by", "confirmed")
    assert state(conn, "relation", b) == "rejected"


def test_bad_corrections_are_refused(conn, queue):
    read_both(conn)
    review.prepare(conn, base=queue)
    key = keys(queue, "relation_dispute")[0]
    write_verdict(queue, key, verdict="neither", correct={"type": "friend_of"}, confidence="high",
                  quote="Alan Chambers", reason="x")
    assert "vocabulary" in review.apply(conn, reviewer=REVIEWER, base=queue)["problems"][0]["problem"]


def test_held_relation_confirmed_is_published(conn, queue):
    read.read_sources(conn, FakeClient([first_reply()]), ROLE, [SID])
    reconcile.reconcile(conn, [SID], primary_model=ROLE.model)
    review.prepare(conn, base=queue)
    key = keys(queue, "relation_held")[0]
    write_verdict(queue, key, verdict="A", quote="Alan Chambers, president of Exodus", confidence="high",
                  reason="A stated role.")
    review.apply(conn, reviewer=REVIEWER, base=queue)
    assert review.held_relation_ids(conn) == set()
    assert len(testset.entry_view(conn, {"urls": ["https://example.org/exodus"]}).relations) == 1


def test_a_stale_verdict_is_not_applied(conn, queue):
    read_both(conn)
    review.prepare(conn, base=queue)
    key = keys(queue, "statement_dispute")[0]
    with conn:
        conn.execute("UPDATE usage SET review_state = 'rejected' WHERE id = ?", (key["labels"]["A"]["id"],))
    write_verdict(queue, key, verdict="B", quote="Tony Moore", confidence="high", reason="x")
    assert "stale" in review.apply(conn, reviewer=REVIEWER, base=queue)["problems"][0]["problem"]


def test_undo_reverses_a_review_run_and_the_issue_returns(conn, queue):
    read_both(conn)
    review.prepare(conn, base=queue)
    key = keys(queue, "statement_dispute")[0]
    write_verdict(queue, key, verdict="A", quote="Tony Moore", confidence="high", reason="x")
    run_id = review.apply(conn, reviewer=REVIEWER, base=queue)["run_id"]
    assert review.undo(conn, run_id, reason="test") == 2
    assert {state(conn, "usage", i["id"]) for i in key["labels"].values()} == {"disputed"}
    reconcile.reconcile(conn, [SID], primary_model=ROLE.model, models=[ROLE.model, SECOND.model])
    assert key["issue"] in {i.id for i in review.find_issues(conn)}
    assert review.undo(conn, run_id, reason="again") == 0             # nothing left to undo
    assert db.verify_decision_chain(conn).ok


def test_register_lists_rulings_and_held_relations(conn, queue):
    read_both(conn)
    review.prepare(conn, base=queue)
    key = keys(queue, "statement_dispute")[0]
    write_verdict(queue, key, verdict="A", quote="Tony Moore", confidence="high", reason="x")
    review.apply(conn, reviewer=REVIEWER, base=queue)
    report = reconcile.difficulties(conn)
    assert {(r["action"], r["n"]) for r in report["reviews"]} == {("confirm", 1), ("reject", 1)}
    assert "Rulings on the review queue" in reconcile.difficulties_markdown(report)


def test_a_quote_from_the_context_passage_is_accepted(conn, queue):
    read_both(conn)
    review.prepare(conn, base=queue)
    key = keys(queue, "statement_dispute")[0]                     # the dispute sits in the second passage
    assert key["context_passages"]
    write_verdict(queue, key, verdict="A", quote="We are closing the ministry today.", confidence="high",
                  reason="The previous passage shows who speaks.")
    assert not review.apply(conn, reviewer=REVIEWER, base=queue)["problems"]


def test_a_relation_can_be_reversed(conn, queue):
    read_both(conn)
    review.prepare(conn, base=queue)
    key = keys(queue, "relation_dispute")[0]
    write_verdict(queue, key, verdict="neither", correct={"type": "operated_by", "reverse": True}, confidence="high",
                  quote="Alan Chambers, president of Exodus", reason="Points the other way.")
    run_id = review.apply(conn, reviewer=REVIEWER, base=queue)["run_id"]
    a = key["labels"]["A"]["id"]
    row = conn.execute("SELECT subject_label, type, object_label FROM relation WHERE id = ?", (a,)).fetchone()
    assert tuple(row) == ("Exodus", "operated_by", "Alan Chambers")
    review.undo(conn, run_id, reason="test")
    row = conn.execute("SELECT subject_label, object_label, review_state FROM relation WHERE id = ?", (a,)).fetchone()
    assert tuple(row) == ("Alan Chambers", "Exodus", "disputed")


def test_a_second_identical_relation_is_folded_into_the_confirmed_one(conn, queue):
    from tests.test_reconcile import first_reply as base
    doubled = base()
    doubled["relations"].append({**doubled["relations"][0], "type": "affiliated_with"})
    read.read_sources(conn, FakeClient([doubled]), ROLE, [SID])
    reconcile.reconcile(conn, [SID], primary_model=ROLE.model)
    review.prepare(conn, base=queue)
    held = sorted(keys(queue, "relation_held"), key=lambda k: k["issue"])
    assert len(held) == 2
    for k in held:
        rel_type = conn.execute("SELECT type FROM relation WHERE id = ?", (k["labels"]["A"]["id"],)).fetchone()[0]
        if rel_type == "employed_by":
            write_verdict(queue, k, verdict="A", quote="Alan Chambers, president of Exodus", confidence="high", reason="A role.")
        else:
            write_verdict(queue, k, verdict="neither", correct={"type": "employed_by"}, confidence="high",
                          quote="Alan Chambers, president of Exodus", reason="A role is employed_by.")
    review.apply(conn, reviewer=REVIEWER, base=queue)
    states = sorted(r[0] for r in conn.execute("SELECT review_state FROM relation"))
    assert states == ["confirmed", "superseded"]
