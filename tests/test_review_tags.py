"""Tags and excerpts in the review queue: everything two taggers did not settle is reviewed one by one."""
import json

from archive import check, db, review, tag
from tests.test_check import CHECKER
from tests.test_tag import FIRST, SECOND, SID, V1, FakeClient, conn, first_reply, second_reply  # noqa: F401

REVIEWER = "reviewer-model:x"


def _tagged_and_checked(conn, *, healing="no", testimony="no"):
    with conn:   # S2 by the same speaker as S1, so S1's stated role spreads to it
        conn.execute("UPDATE usage SET speaker = 'Alan Chambers, president of Exodus' WHERE id = 'use_s2'")
    tag.tag_sources(conn, FakeClient([first_reply()]), FIRST, [SID], primary_model="reader-a", prompt=V1)
    tag.tag_sources(conn, FakeClient([second_reply()]), SECOND, [SID], primary_model="reader-a", prompt=V1)
    tag.compare(conn, [SID], primary_model="tagger-a", second_model="tagger-b")
    answers = [{"answer": healing, "words": "for God to heal me" if healing == "yes" else "", "reason": "r"},
               {"answer": testimony, "words": "I prayed every night" if testimony == "yes" else "", "reason": "r"}]
    check.check(conn, FakeClient(answers), CHECKER, [SID])


def _keys(queue, kind=None):
    out = [json.loads(p.read_text()) for p in sorted((queue / "keys").glob("*.json"))]
    return [k for k in out if kind is None or k["kind"] == kind]


def _verdict(queue, key, **verdict):
    (queue / "verdicts" / f"{key['issue']}.json").write_text(json.dumps({"issue": key["issue"], **verdict}))


def _state(conn, table, record_id):
    return conn.execute(f"SELECT review_state, {'mode' if table == 'tag' else 'kind'} FROM {table} WHERE id = ?",
                        (record_id,)).fetchone()


def _concept(conn, key):
    return conn.execute("SELECT concept_id FROM tag WHERE id = ?", (key["labels"]["A"]["id"],)).fetchone()[0]


def test_every_unsettled_tag_and_flagged_item_gets_a_blind_packet(conn, tmp_path):
    _tagged_and_checked(conn)
    queue = tmp_path / "review"
    result = review.prepare(conn, base=queue)
    # identity erasure: uses vs describes; the leader type (one tagger, two statements: one issue) and the
    # euphemism (the other tagger); spiritual healing and the testimony, agreed but rejected by the check
    assert result["written"] == {"tag_dispute": 1, "tag_held": 2, "tag_checked": 1, "excerpt_checked": 1}
    actor = next(k for k in _keys(queue, "tag_held") if k["scheme"] == "actor_type")
    assert len(actor["labels"]["A"]["group"]) == 1 and set(actor["labels"]) == {"A"}
    packets = {p.name: p.read_text() for p in (queue / "open").glob("*.md")}
    assert sorted(name[0] for name in packets) == ["4", "4", "4", "4", "5"]   # all but the held euphemism first
    for text in packets.values():
        assert "tagger-a" not in text and "tagger-b" not in text and CHECKER.model not in text   # blind
        assert "## Your verdict" in text and "⟦" in text
    healing = next(t for t in packets.values() if "Kind: tag checked" in t)
    assert "**Spiritual Healing**" in healing and "Prayer for healing of orientation." in healing
    assert "first-hand" in healing and "“for God to heal me”" in healing and "a check flagged it" in healing
    leader = next(t for t in packets.values() if "this speaker this type" in t)
    assert "other statements in this source with this type (S2–S2)" in leader and "“president of Exodus”" in leader
    dispute = next(t for t in packets.values() if "Kind: tag dispute" in t)
    assert "## Tag A" in dispute and "## Tag B" in dispute
    excerpt = next(t for t in packets.values() if "Kind: excerpt checked" in t)
    assert "**testimony**" in excerpt and "Other kinds: prayer script" in excerpt
    assert review.prepare(conn, base=queue)["written"] == {}


def test_rulings_on_tags_are_applied_one_by_one_and_can_be_undone(conn, tmp_path):
    _tagged_and_checked(conn, testimony="yes")
    queue = tmp_path / "review"
    review.prepare(conn, base=queue)
    dispute = _keys(queue, "tag_dispute")[0]
    describes = next(label for label, info in dispute["labels"].items()
                     if _state(conn, "tag", info["id"])[1] == "describes")
    _verdict(queue, dispute, verdict=describes, quote="I am not ex-gay", confidence="high", reason="He rejects it.")
    held = {_concept(conn, k): k for k in _keys(queue, "tag_held")}
    actor, euphemism = held["actor_type:leader-or-founder"], held["function:euphemism"]
    _verdict(queue, actor, verdict="A", quote="president of Exodus", confidence="high", reason="Stated.")
    _verdict(queue, euphemism, verdict="neither", correct={"mode": "describes"}, quote="SSA coaching",
             confidence="medium", reason="The site reports the name.")
    checked = _keys(queue, "tag_checked")[0]
    _verdict(queue, checked, verdict="neither", quote="I prayed every night", confidence="high", reason="Not first-hand.")
    result = review.apply(conn, reviewer=REVIEWER, base=queue)
    assert result["problems"] == []
    assert result["counts"]["tag_held:confirmed"] == 1 and result["counts"]["tag_held:corrected"] == 1
    for info in dispute["labels"].values():
        assert _state(conn, "tag", info["id"])[0] == ("confirmed" if info["id"] == dispute["labels"][describes]["id"]
                                                      else "rejected")
    assert [_state(conn, "tag", i)[0] for i in [actor["labels"]["A"]["id"]] + [g["id"] for g in
                                                                            actor["labels"]["A"]["group"]]] == \
        ["confirmed", "confirmed"]                                     # the ruling covers each statement
    assert tuple(_state(conn, "tag", euphemism["labels"]["A"]["id"])) == ("confirmed", "describes")
    assert _state(conn, "tag", checked["labels"]["A"]["id"])[0] == "rejected"
    assert not [i for i in review.find_issues(conn) if i.table == "tag"]
    assert review.undo(conn, result["run_id"], reason="test") == 6   # 2 + 2 + 1 + 1 decisions
    assert tuple(_state(conn, "tag", euphemism["labels"]["A"]["id"])) == ("proposed", "uses")
    tag.compare(conn, [SID], primary_model="tagger-a", second_model="tagger-b")
    assert len([i for i in review.find_issues(conn) if i.table == "tag"]) == 4   # the issues return
    assert db.verify_decision_chain(conn).ok


def test_corrections_that_do_not_fit_a_tag_are_refused(conn, tmp_path):
    _tagged_and_checked(conn)
    queue = tmp_path / "review"
    review.prepare(conn, base=queue)
    actor = next(k for k in _keys(queue, "tag_held") if k["scheme"] == "actor_type")
    _verdict(queue, actor, verdict="neither", correct={"mode": "uses"}, quote="president of Exodus",
             confidence="high", reason="x")
    excerpt = _keys(queue, "excerpt_checked")[0]
    _verdict(queue, excerpt, verdict="neither", correct={"kind": "sermon"}, quote="I prayed every night",
             confidence="high", reason="x")
    problems = {p["issue"]: p["problem"] for p in review.apply(conn, reviewer=REVIEWER, base=queue)["problems"]}
    assert "an actor type has no other mode" in problems[actor["issue"]]
    assert "a new term has no other kind" in problems[excerpt["issue"]]
    with conn:   # the group changed since the packet was written: the verdict is stale
        conn.execute("UPDATE tag SET review_state = 'rejected' WHERE id = ?", (actor["labels"]["A"]["group"][0]["id"],))
    _verdict(queue, actor, verdict="A", quote="president of Exodus", confidence="high", reason="x")
    problems = {p["issue"]: p["problem"] for p in review.apply(conn, reviewer=REVIEWER, base=queue)["problems"]}
    assert "stale" in problems[actor["issue"]]


def test_held_tags_of_one_speaker_and_label_share_a_packet_and_each_statement_is_ruled_on(conn, tmp_path):
    second = second_reply()   # the second tagger alone calls both of the speaker's statements a euphemism
    for sid, cue in (("S1", "not ex-gay"), ("S2", "for God to heal me")):
        second["statements"][sid]["tags"].append({"tag": "function: Euphemism", "mode": "uses", "cue": cue})
    second["statements"]["S3"]["terms"] = []    # a term only one tagger answered, in a statement that reports
    with conn:
        conn.execute("UPDATE usage SET speaker = 'Alan Chambers, president of Exodus' WHERE id = 'use_s2'")
        conn.execute("INSERT INTO concept(id, scheme, pref_label, definition, definition_source, created_at) VALUES"
                     " ('function:pastoral-rhetoric', 'function', 'Pastoral Rhetoric', 'Church language.', 'seed', 'x')")
    tag.tag_sources(conn, FakeClient([first_reply()]), FIRST, [SID], primary_model="reader-a", prompt=V1)
    tag.tag_sources(conn, FakeClient([second]), SECOND, [SID], primary_model="reader-a", prompt=V1)
    tag.compare(conn, [SID], primary_model="tagger-a", second_model="tagger-b")
    queue = tmp_path / "review"
    review.prepare(conn, base=queue)
    assert not [k for k in _keys(queue) if k.get("scheme") == "term"]   # the stance decides: 'describes'
    group = next(k for k in _keys(queue, "tag_held") if len(k.get("statements", {})) == 2
                 and k["scheme"] == "function")
    text = next((queue / "open").glob(f"*-{group['issue']}.md")).read_text()
    assert "## The statements (S1–S2), each ruled on" in text and "the tagger's words: “not ex-gay”" in text
    assert "Other labels of this kind (for a correction): Pastoral Rhetoric." in text and "`except`" in text
    _verdict(queue, group, verdict="neither", correct={"label": "Pastoral Rhetoric"}, **{"except": ["S1"]},
             quote="for God to heal me", confidence="medium", reason="Church language, not a euphemism.")
    assert review.apply(conn, reviewer=REVIEWER, base=queue)["problems"] == []
    s1, s2 = group["statements"]["S1"], group["statements"]["S2"]
    assert _state(conn, "tag", s1)[0] == "rejected"
    assert tuple(conn.execute("SELECT review_state, concept_id FROM tag WHERE id = ?", (s2,)).fetchone()) == \
        ("confirmed", "function:pastoral-rhetoric")
    assert db.verify_decision_chain(conn).ok


def test_bad_exceptions_and_labels_are_refused(conn, tmp_path):
    _tagged_and_checked(conn)
    queue = tmp_path / "review"
    review.prepare(conn, base=queue)
    actor = next(k for k in _keys(queue, "tag_held") if k["scheme"] == "actor_type")
    checked = _keys(queue, "tag_checked")[0]
    _verdict(queue, actor, verdict="A", **{"except": ["S1", "S2"]}, quote="president of Exodus",
             confidence="high", reason="x")
    _verdict(queue, checked, verdict="neither", correct={"label": "Not A Practice"}, quote="I prayed every night",
             confidence="high", reason="x")
    problems = {p["issue"]: p["problem"] for p in review.apply(conn, reviewer=REVIEWER, base=queue)["problems"]}
    assert "use verdict 'neither'" in problems[actor["issue"]]
    assert "is not a practice in the lists" in problems[checked["issue"]]


def test_an_agreed_pro_change_argument_on_a_critical_statement_sends_the_stance_to_review(conn, tmp_path):
    with conn:
        conn.execute("INSERT INTO concept(id, scheme, pref_label, definition, definition_source, created_at) VALUES"
                     " ('cluster:policy-resistance', 'cluster', 'Policy-Resistance', 'Opposes bans.', 'seed', 'x')")
    first, second = first_reply(), second_reply()
    for reply in (first, second):   # both: S1 (stance criticizes) argues against a ban
        reply["statements"]["S1"]["tags"] = [{"tag": "cluster: Policy-Resistance", "mode": "uses", "cue": "never was"}]
    prompt = tag.read.load_prompt("tag-v3")
    tag.tag_sources(conn, FakeClient([first]), FIRST, [SID], primary_model="reader-a", prompt=prompt)
    tag.tag_sources(conn, FakeClient([second]), SECOND, [SID], primary_model="reader-a", prompt=prompt)
    tag.compare(conn, [SID], primary_model="tagger-a", second_model="tagger-b")
    queue = tmp_path / "review"
    review.prepare(conn, base=queue)
    key = _keys(queue, "stance_check")[0]
    assert key["table"] == "usage" and key["labels"]["A"]["id"] == "use_s1" and key["priority"] == 2
    text = next((queue / "open").glob(f"*-{key['issue']}.md")).read_text()
    assert "**Policy-Resistance**: Opposes bans. The taggers' words: “never was”." in text
    _verdict(queue, key, verdict="neither", quote="I never was", confidence="high", reason="x")
    assert "needs `correct` with the stance" in review.apply(conn, reviewer=REVIEWER, base=queue)["problems"][0]["problem"]
    _verdict(queue, key, verdict="A", quote="I am not ex-gay, I never was.", confidence="high", reason="He criticizes.")
    assert review.apply(conn, reviewer=REVIEWER, base=queue)["problems"] == []
    # the stance stands, so now the tag is in doubt
    review.prepare(conn, base=queue)
    flagged = [k for k in _keys(queue, "tag_checked")]
    assert len(flagged) == 1 and _concept(conn, flagged[0]) == "cluster:policy-resistance"
