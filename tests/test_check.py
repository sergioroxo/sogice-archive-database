import json

from archive import check, db, tag
from archive.config import RoleConfig
from tests.test_tag import FIRST, NOW, SECOND, SID, V1, FakeClient, conn, first_reply, second_reply  # noqa: F401

CHECKER = RoleConfig(name="trial_checker", model="checker-c", extra={"num_predict": 256})


def _tag_and_compare(conn):
    tag.tag_sources(conn, FakeClient([first_reply()]), FIRST, [SID], primary_model="reader-a", prompt=V1)
    tag.tag_sources(conn, FakeClient([second_reply()]), SECOND, [SID], primary_model="reader-a", prompt=V1)
    tag.compare(conn, [SID], primary_model="tagger-a", second_model="tagger-b")
    with conn:   # a second practice, so the decoy has one to ask about
        conn.execute("INSERT INTO concept(id, scheme, pref_label, definition, definition_source, created_at)"
                     " VALUES ('practice:exorcism', 'practice', 'Exorcism', 'Casting out spirits.', 'seed', ?)", (NOW,))


def test_check_asks_about_each_agreed_item_and_records_an_audit(conn):
    _tag_and_compare(conn)
    items = check.items_to_check(conn, [SID])
    # agreed and kept: the spiritual-healing practice (uses) and the testimony; tactics and terms are not checked
    assert [(i["table"], i["label"]) for i in items] == [("tag", "Spiritual Healing"), ("asset", "testimony")]
    states = conn.execute("SELECT id, review_state FROM tag ORDER BY id").fetchall()
    client = FakeClient([
        {"answer": "yes", "words": "for God to heal me", "reason": "He prays to be healed."},
        {"answer": "no", "words": "", "reason": "No exorcism."},                         # the decoy: Exorcism
        {"answer": "yes", "words": "words that are not in it", "reason": "A first-person account."},
        {"answer": "yes", "words": "I prayed every night", "reason": "It is a script."}])  # the decoy kind
    result = check.check(conn, client, CHECKER, [SID], decoys=True)
    assert result["items"] == 2 and result["calls"] == 4
    assert result["counts"] == {"practice:pass": 1, "practice:decoy_no": 1, "excerpt:uncertain": 1,
                                "excerpt:decoy_yes": 1}
    system, user = client.calls[0]["messages"]
    assert system["content"] == check.read.load_prompt(check.DEFAULT_PROMPT).text and check.DEFAULT_PROMPT == "check-v2"
    assert "do, offer or run this practice" in user["content"] and "I prayed every night for God to heal me." in user["content"]
    assert "Speaker: Alan Chambers; stance: describes" in user["content"]
    assert "Exorcism" in client.calls[1]["messages"][1]["content"]
    assert client.calls[0]["temperature"] == 0 and client.calls[0]["schema"] == check.SCHEMA
    assert client.unloaded == ["checker-c"]
    audits = conn.execute("SELECT target_table, audit_verdict, quote, reason FROM decision"
                          " WHERE actor_id = 'checker-c' AND action = 'audit' ORDER BY seq").fetchall()
    assert [(a[0], a[1], a[2]) for a in audits] == [("tag", "pass", "for God to heal me"), ("asset", "uncertain", None)]
    assert "not in the item" in audits[1][3]
    assert result["answers"][1]["decoy"]["label"] != "testimony"   # decoys are never decisions
    run = conn.execute("SELECT * FROM run WHERE role = 'audit'").fetchone()
    assert run["outcome"] == "succeeded" and run["prompt_version"] == "check-v2"
    assert json.loads(run["inputs"])["job"] == "check" and json.loads(run["inputs"])["calls"] == 4
    assert conn.execute("SELECT id, review_state FROM tag ORDER BY id").fetchall() == states   # items unchanged
    assert db.verify_decision_chain(conn).ok


def test_a_no_fails_and_a_broken_reply_is_uncertain():
    item = {"text": "I prayed every night for God to heal me."}
    assert check.verdict(item, {"answer": "no", "words": "", "reason": ""}) == ("fail", None)
    assert check.verdict(item, {"answer": "maybe"}) == ("uncertain", None)
    assert check.verdict(item, None) == ("uncertain", None)
    assert check.verdict(item, {"answer": "yes", "words": "heal me", "reason": ""}) == ("pass", "heal me")
    # pieces joined by an ellipsis count only when each is found, in order
    assert check.verdict(item, {"answer": "yes", "words": "I prayed... for God to heal", "reason": ""}) == \
        ("pass", "I prayed … for God to heal")
    assert check.verdict(item, {"answer": "yes", "words": "for God to heal… I prayed every", "reason": ""}) == \
        ("uncertain", None)


def test_check_v2_shows_the_title_and_the_passage_around_a_statement(conn):
    _tag_and_compare(conn)
    item = check.items_to_check(conn, [SID])[0]
    assert item["title"] == "A title" and item["context"].startswith("Alan Chambers, president of Exodus")
    v1 = check.build_messages(check.read.load_prompt("check-v1"), item)[1]["content"]
    v2 = check.build_messages(check.read.load_prompt("check-v2"), item)[1]["content"]
    assert "Source:" not in v1 and "Context" not in v1
    assert v2.startswith("Source: A title") and "Context (around the passage" in v2 and v2.endswith(
        "Passage:\n\"\"\"\nI prayed every night for God to heal me.\n\"\"\"")
    long = "x" * 3000 + "THE STATEMENT" + "y" * 3000
    cut = check._around(long, 3000, 3013)
    assert "THE STATEMENT" in cut and cut.startswith("…") and cut.endswith("…") and len(cut) <= check.CONTEXT_CHARS + 2


def test_a_trial_can_also_check_what_one_tagger_gave(conn):
    _tag_and_compare(conn)
    with conn:   # the second tagger's euphemism is held (one tagger); make it a practice the check covers
        conn.execute("UPDATE tag SET concept_id = 'practice:exorcism' WHERE concept_id = 'function:euphemism'")
    agreed = check.items_to_check(conn, [SID])
    both = check.items_to_check(conn, [SID], held=True)
    assert [i["held"] for i in both] == [False, True, False]   # agreed tags first, then held ones, then excerpts
    assert [i["id"] for i in both if not i["held"]] == [i["id"] for i in agreed]
    client = FakeClient([{"answer": "yes", "words": "heal me", "reason": ""},
                         {"answer": "no", "words": "", "reason": ""},
                         {"answer": "yes", "words": "I prayed every night", "reason": ""}])
    result = check.check(conn, client, CHECKER, [SID], held=True)
    assert result["counts"] == {"practice:pass": 1, "held_practice:fail": 1, "excerpt:pass": 1}


def test_chosen_records_of_any_kind_and_mode_can_be_checked(conn):
    _tag_and_compare(conn)
    euphemism = conn.execute("SELECT id FROM tag WHERE concept_id = 'function:euphemism'").fetchone()[0]
    erasure = [r[0] for r in conn.execute("SELECT id FROM tag WHERE concept_id = 'tactic:identity-erasure' ORDER BY mode")]
    items = check.items_by_id(conn, [euphemism, *erasure], [])
    assert [(i["kind"], i["mode"], i["held"]) for i in items] == [("function", "uses", True),
                                                                  ("tactic", "describes", True), ("tactic", "uses", True)]   # disputed: not agreed
    assert "Do the speaker's own words do this (Euphemism)?" in check.question(items[0])
    assert "without arguing it themselves" in check.question(items[1])
    client = FakeClient([{"answer": "yes", "words": "SSA coaching", "reason": ""}])
    result = check.check(conn, client, CHECKER, [], prompt=check.read.load_prompt("check-v3"),
                         only={"tag": [euphemism]})
    assert result["counts"] == {"held_function:pass": 1} and result["answers"][0]["mode"] == "uses"
    assert "A tagger gave the label below" in client.calls[0]["messages"][0]["content"]
