import json
import uuid

import pytest

from archive import db, review, rules, testset
from archive.db import record_decision
from archive.passages import sha256_text
from archive.urls import source_id_for
from tests.test_read import NOW, add_source

LIST = ("PERSONNES ENTENDUES PAR LA RAPPORTEURE\n"
        "● Délégation interministérielle à la lutte contre le racisme, l’antisémitisme\n"
        "et la haine anti-LGBT (DILCRAH)\n"
        " Mme Claire Fontaine, déléguée interministérielle\n"
        "● Association française des avocats LGBT\n"
        " Me Hélène Marchand, avocate\n"
        "● Centre de lutte contre les manipulations mentales\n"
        " Mme Odile Perrin, membre fondateur du collectif des victimes et\n"
        "familles de victimes du psycho-spirituel\n"
        " Mme Lucie Bernard, membre du bureau national\n"
        "● David et Jonathan\n"
        " M. Paul de Vernon, vice-président\n")
SITTING = ("Amendement CL34 de M. Bastien Lachaud.\n"
           "Mme Laurence Vanceunebrock, rapporteure. Avis défavorable, au nom de l’Assemblée nationale.\n"
           "Mme Laurence\nVanceunebrock,\nrapporteure. Je retire l’amendement.\n")
SIGNED = ("Signed by\n\nMark Winstanley, Chief\nExecutive Rethink\nMental Illness\n\n"
          "Richard Cohen, a psychotherapist and author, said he helps men. A plain list follows.")
URL = "https://www.assemblee-nationale.fr/rapport-4501.pdf"


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "a.db")
    db.init_db(connection)
    with connection:
        add_source(connection, URL, [LIST, SITTING, SIGNED])
        connection.execute("INSERT INTO run(id, role, model, prompt_version, inputs, started_at, outcome)"
                           " VALUES ('run_r', 'reading', 'reader:1', 'read-v4', ?, ?, 'succeeded')",
                           (json.dumps({"source_id": SID}), NOW))
    yield connection
    connection.close()


SID = source_id_for(URL)


def passage(conn, n):
    return conn.execute("SELECT id, text FROM passage WHERE sequence = ?", (n,)).fetchone()


def relation(conn, n, quote, subject, rtype, obj, *, state="proposed", object_kind=None):
    """A reader's relation with its evidence usage, held (one reader) like the real ones."""
    pid, text = passage(conn, n)
    start = text.index(quote)
    uid, rid = f"use_{uuid.uuid4().hex[:12]}", f"rel_{uuid.uuid4().hex[:12]}"
    object_id = None
    if object_kind:
        object_id = f"ent_{uuid.uuid4().hex[:8]}"
        conn.execute("INSERT INTO entity(id, kind, label, normalized_label, created_at) VALUES (?,?,?,?,?)",
                     (object_id, object_kind, obj, obj.lower(), NOW))
    conn.execute("INSERT INTO usage(id, passage_id, char_start, char_end, quote, quote_sha256, speaker,"
                 " attribution_mode, stance, found_by, run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (uid, pid, start, start + len(quote), quote, sha256_text(quote), "Assemblée nationale",
                  "unknown", "not_applicable", "model", "run_r", NOW))
    conn.execute("INSERT INTO relation(id, subject_label, type, object_label, object_entity_id, attributed_source_id,"
                 " created_at, evidence_state, review_state) VALUES (?,?,?,?,?,?,?, 'located', ?)",
                 (rid, subject, rtype, obj, object_id, SID, NOW, state))
    conn.execute("INSERT INTO relation_usage(relation_id, usage_id) VALUES (?,?)", (rid, uid))
    conn.execute("INSERT INTO assessment(id, run_id, source_id, target_table, target_id, confidence, readers,"
                 " proposed_by, created_at) VALUES (?, 'run_r', ?, 'relation', ?, 'one_reader', '[]', ?, ?)",
                 (f"asm_{rid}", SID, rid, '["reader:1"]', NOW))
    record_decision(conn, actor_kind="model", actor_id="reader:1", run_id="run_r", action="propose",
                    target_table="relation", target_id=rid, reason="read-v4: test relation", quote=quote)
    return rid


def row(conn, rid):
    return conn.execute("SELECT * FROM relation WHERE id = ?", (rid,)).fetchone()


def rule_of(conn, rid):
    d = conn.execute("SELECT new_state FROM decision WHERE target_id = ? AND actor_id = ? ORDER BY seq DESC",
                     (rid, rules.ACTOR_ID)).fetchone()
    return json.loads(d[0])["rule"] if d else None


def test_role_lines_under_a_heading_are_employment(conn):
    with conn:
        vice = relation(conn, 1, "M. Paul de Vernon, vice-président", "M. Paul de Vernon",
                        "affiliated_with", "David et Jonathan")
        bureau = relation(conn, 1, "Mme Lucie Bernard, membre du bureau national", "Mme Lucie Bernard",
                          "employed_by", "Centre de lutte contre les manipulations mentales")
        portfolio = relation(conn, 1, "Mme Claire Fontaine, déléguée interministérielle", "Mme Claire Fontaine",
                             "employed_by", "Délégation interministérielle à la lutte contre le racisme, "
                             "l’antisémitisme et la haine anti-LGBT (DILCRAH)")
    result = rules.apply_rules(conn, [SID])
    assert row(conn, vice)["type"] == "employed_by" and row(conn, vice)["review_state"] == "confirmed"
    assert row(conn, bureau)["review_state"] == "confirmed"          # the bureau is part of the heading's body
    assert row(conn, portfolio)["review_state"] == "confirmed"       # a heading over two lines
    assert result["counts"]["role_line:confirm"] == 3 and result["counts"]["role_line:edit"] == 1
    assert review.held_relation_ids(conn) == set()
    assert db.verify_decision_chain(conn).ok


def test_a_profession_alone_states_nothing_and_another_body_is_left_to_review(conn):
    with conn:
        lawyer = relation(conn, 1, "Me Hélène Marchand, avocate", "Me Hélène Marchand", "affiliated_with",
                          "Association française des avocats LGBT")
        other = relation(conn, 1, "Mme Odile Perrin, membre fondateur du collectif des victimes et",
                         "Mme Odile Perrin", "affiliated_with", "Centre de lutte contre les manipulations mentales")
    rules.apply_rules(conn, [SID])
    assert row(conn, lawyer)["review_state"] == "rejected" and rule_of(conn, lawyer) == "profession_only"
    assert row(conn, other)["review_state"] == "proposed"            # names another body: the queue decides
    assert other in review.held_relation_ids(conn)


def test_a_speaker_label_is_not_a_relation_but_a_sentence_naming_the_body_may_be(conn):
    with conn:
        label = relation(conn, 2, "Mme Laurence\nVanceunebrock,\nrapporteure", "Mme Laurence Vanceunebrock",
                         "employed_by", "Assemblée nationale")
    rules.apply_rules(conn, [SID])
    assert row(conn, label)["review_state"] == "rejected" and rule_of(conn, label) == "speaker_label"


def test_signature_blocks_and_ends_that_are_not_names(conn):
    with conn:
        signed = relation(conn, 3, "Mark Winstanley, Chief\nExecutive Rethink\nMental Illness", "Mark Winstanley",
                          "employed_by", "Rethink Mental Illness")
        title = relation(conn, 3, "Richard Cohen, a psychotherapist", "Richard Cohen", "employed_by", "Psychotherapist")
        nowhere = relation(conn, 3, "Richard Cohen, a psychotherapist", "Richard Cohen", "affiliated_with",
                           "International Healing Foundation")
        descr = relation(conn, 3, "A plain list follows.", "Richard Cohen", "cites", "a plain list")
        document = relation(conn, 3, "A plain list follows.", "Assemblée nationale", "cites", "Rethink Mental Illness")
    rules.apply_rules(conn, [SID])
    assert row(conn, signed)["review_state"] == "confirmed"
    assert row(conn, title)["review_state"] == "rejected" and rule_of(conn, title) == "end_not_a_name"
    assert row(conn, nowhere)["review_state"] == "rejected" and rule_of(conn, nowhere) == "end_not_in_text"
    assert row(conn, descr)["review_state"] == "rejected"
    assert row(conn, document)["review_state"] == "proposed"          # the document itself, named by its site


@pytest.mark.parametrize("label, why", [
    ("grand rabbin de France", True), ("Psychotherapist", True), ("organisation évangélique", True),
    ("Mme la rapporteure", True), ("commission", True), ("President Obama", False), ("code pénal", False),
    ("commission des lois", False), ("mission flash", True), ("Exodus International", False),
])
def test_what_is_not_a_name(label, why):
    assert bool(rules.not_a_name(label)) is why


def test_an_amendment_credited_to_a_person_is_authorship_the_right_way_round(conn):
    with conn:
        amendment = relation(conn, 2, "Amendement CL34 de M. Bastien Lachaud", "Bastien Lachaud", "co_signatory_of",
                             "Amendement CL34")
    run_id = rules.apply_rules(conn, [SID])["run_id"]
    r = row(conn, amendment)
    assert (r["subject_label"], r["type"], r["object_label"], r["review_state"]) == (
        "Amendement CL34", "authored_by", "Bastien Lachaud", "confirmed")
    assert review.undo(conn, run_id, reason="test") == 2               # edit and confirm, undone together
    r = row(conn, amendment)
    assert (r["subject_label"], r["type"], r["review_state"]) == ("Bastien Lachaud", "co_signatory_of", "proposed")


def test_two_signers_are_not_a_signature(conn):
    with conn:
        pair = relation(conn, 2, "Amendement CL34 de M. Bastien Lachaud", "M. Bastien Lachaud", "co_signatory_of",
                        "Laurence Vanceunebrock", object_kind="person")
    rules.apply_rules(conn, [SID])
    assert row(conn, pair)["review_state"] == "rejected" and rule_of(conn, pair) == "signer_to_signer"


def test_rules_leave_rulings_alone_and_run_once(conn):
    with conn:
        ruled = relation(conn, 3, "Richard Cohen, a psychotherapist", "Richard Cohen", "employed_by", "Psychotherapist")
        record_decision(conn, actor_kind="researcher", actor_id="sergio", action="confirm", target_table="relation",
                        target_id=ruled, reason="checked")
        vice = relation(conn, 1, "M. Paul de Vernon, vice-président", "M. Paul de Vernon",
                        "employed_by", "David et Jonathan")
    rules.apply_rules(conn, [SID])
    assert row(conn, ruled)["review_state"] == "proposed"             # the researcher's ruling stands
    again = rules.apply_rules(conn, [SID])
    assert again["counts"] == {"kept_settled": 1} and row(conn, vice)["review_state"] == "confirmed"


def test_a_confirmed_role_folds_the_other_readers_copy(conn):
    with conn:
        first = relation(conn, 1, "M. Paul de Vernon, vice-président", "M. Paul de Vernon",
                         "employed_by", "David et Jonathan", state="disputed")
        second = relation(conn, 1, "M. Paul de Vernon, vice-président", "Paul de Vernon",
                          "affiliated_with", "David et Jonathan", state="disputed")
    result = rules.apply_rules(conn, [SID])
    states = sorted(row(conn, x)["review_state"] for x in (first, second))
    assert states == ["confirmed", "superseded"] and result["counts"]["folded"] == 1


def test_the_register_lists_rulings_by_rule(conn):
    from archive import reconcile
    with conn:
        relation(conn, 1, "Me Hélène Marchand, avocate", "Me Hélène Marchand", "affiliated_with",
                 "Association française des avocats LGBT")
    rules.apply_rules(conn, [SID])
    report = reconcile.difficulties(conn)
    assert [(r["rule"], r["action"], r["n"]) for r in report["rules"]] == [("profession_only", "reject", 1)]
    assert "Relations decided by rule" in reconcile.difficulties_markdown(report)


def test_a_disputed_relation_whose_partner_a_rule_settled_goes_to_the_queue(conn, tmp_path):
    with conn:
        left = relation(conn, 3, "Richard Cohen, a psychotherapist", "Richard Cohen", "employed_by", "Psychotherapist",
                        state="disputed")
        right = relation(conn, 3, "Richard Cohen, a psychotherapist", "Richard Cohen", "affiliated_with",
                         "International Healing Foundation", state="disputed")
        conn.execute("UPDATE assessment SET confidence = 'readers_disagree', counterpart_id = ? WHERE target_id = ?",
                     (right, left))
        conn.execute("UPDATE assessment SET confidence = 'readers_disagree', counterpart_id = ? WHERE target_id = ?",
                     (left, right))
        # only the first is decided by a rule; the second stays disputed
        rules._record(conn, "run_r", left, "reject", {}, "test", "Richard Cohen", "end_not_a_name")
    issues = review.find_issues(conn, [SID])
    assert [(i.kind, [r["id"] for r in i.records]) for i in issues] == [("relation_held", [right])]
