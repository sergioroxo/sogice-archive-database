import json

import pytest

from archive import db, read, tag
from archive.config import RoleConfig
from archive.ollama import ChatResult
from archive import testset
from archive.passages import sha256_text
from archive.urls import source_id_for

NOW = "2026-11-12T12:00:00Z"
URL = "https://example.org/a"
SID = source_id_for(URL)
VID = "ver_test"
TEXT_1 = ("Alan Chambers, president of Exodus, said: “I am not ex-gay, I never was.” "
          "I prayed every night for God to heal me. ")
TEXT_2 = ("Later I learned to accept myself, and I left the ministry. "
          "Critics call it conversion therapy. The group used SSA coaching.")
FIRST = RoleConfig(name="tagging", model="tagger-a", temperature=0, extra={"num_predict": 512, "keep_alive": "1m"})
SECOND = RoleConfig(name="tagging", model="tagger-b", temperature=0, extra={"num_predict": 512})
V1 = read.load_prompt("tag-v1")   # the first prompt: 44 tactics, one actor type per statement


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.unloaded = []

    def chat_json(self, model, messages, **kwargs):
        self.calls.append({"model": model, "messages": messages, **kwargs})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if reply == "truncate":
            return ChatResult(None, '{"statements": {', model, 1.0, 100, 512, "length")
        return ChatResult(reply, json.dumps(reply), model, 2.0, 100, 50, "stop")

    def unload(self, model):
        self.unloaded.append(model)


def _usage(conn, uid, passage, quote, *, run=None, speaker=None, stance="describes", concept=None, state="proposed"):
    text = TEXT_1 if passage == "p1" else TEXT_2
    start = text.index(quote)
    conn.execute("INSERT INTO usage(id, passage_id, char_start, char_end, quote, quote_sha256, concept_id, speaker,"
                 " stance, found_by, run_id, created_at, review_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (uid, passage, start, start + len(quote), quote, sha256_text(quote), concept, speaker,
                  stance if not concept else "unknown", "exact_match" if concept else "model", run, NOW, state))


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "a.db")
    db.init_db(c)
    with c:
        c.execute("INSERT INTO source(id, url, title, created_at) VALUES (?, 'https://example.org/a', 'A title', ?)",
                  (SID, NOW))
        c.execute("INSERT INTO source_version(id, source_id, sha256, path, mime_type, acquisition_route, byte_size,"
                  " created_at) VALUES (?,?,?, 'x', 'text/html', 'live_fetch', 1, ?)", (VID, SID, sha256_text(VID), NOW))
        for n, (pid, text, start) in enumerate((("p1", TEXT_1, 0), ("p2", TEXT_2, len(TEXT_1))), 1):
            c.execute("INSERT INTO passage(id, version_id, sequence, char_start, char_end, text, text_sha256, language,"
                      " created_at) VALUES (?,?,?,?,?,?,?, 'en', ?)",
                      (pid, VID, n, start, start + len(text), text, sha256_text(text), NOW))
        for cid, scheme, label, definition in (
                ("term:ex-gay", "term", "ex-gay", None),
                ("term:conversion-therapy", "term", "conversion therapy", None),
                ("tactic:identity-erasure", "tactic", "Identity-Erasure", "Minimizing or denying LGBTQ+ identity."),
                ("practice:spiritual-healing", "practice", "Spiritual Healing", "Prayer for healing of orientation."),
                ("function:euphemism", "function", "Euphemism", None)):
            c.execute("INSERT INTO concept(id, scheme, pref_label, definition, definition_source, created_at)"
                      " VALUES (?,?,?,?,?,?)", (cid, scheme, label, definition, "seed" if definition else None, NOW))
            c.execute("INSERT INTO concept_label(id, concept_id, label, normalized_label, language, kind, created_at)"
                      " VALUES (?,?,?,?, 'en', 'pref', ?)", (f"lab_{cid}", cid, label, label.casefold(), NOW))
        for rid, model in (("run_read_a", "reader-a"), ("run_read_b", "reader-b")):
            c.execute("INSERT INTO run(id, role, model, prompt_version, inputs, started_at, outcome, finished_at)"
                      " VALUES (?, 'reading', ?, 'read-v4', ?, ?, 'succeeded', ?)",
                      (rid, model, json.dumps({"source_id": SID}), NOW, NOW))
        _usage(c, "use_s1", "p1", "I am not ex-gay, I never was.", run="run_read_a",
               speaker="Alan Chambers, president of Exodus", stance="criticizes")
        _usage(c, "use_s2", "p1", "I prayed every night for God to heal me.", run="run_read_a", speaker="Alan Chambers")
        _usage(c, "use_s3", "p2", "Critics call it conversion therapy.", run="run_read_a", speaker="example.org",
               stance="reports")
        _usage(c, "use_s4", "p2", "The group used SSA coaching.", run="run_read_b", speaker="example.org")
        _usage(c, "use_dup", "p1", "I am not ex-gay, I never was.", run="run_read_b", speaker="Alan Chambers",
               state="superseded")          # the second reader's copy of an agreed statement
        _usage(c, "use_m1", "p1", "ex-gay", concept="term:ex-gay")
        _usage(c, "use_m2", "p2", "conversion therapy", concept="term:conversion-therapy")
    yield c
    c.close()


def first_reply():
    return {
        "statements": {
            "S1": {"actor": "leader or founder", "actor_words": "president of Exodus",
                   "terms": [{"term": "ex-gay", "mode": "describes"}],
                   "tags": [{"tag": "tactic: Identity-Erasure", "mode": "uses", "cue": "words that are not there"}]},
            "S2": {"actor": "pastor or clergy", "actor_words": "pastor of the church",
                   "terms": [{"term": "healing", "mode": "uses"}],
                   "tags": [{"tag": "practice: Spiritual Healing", "mode": "uses", "cue": "for God to heal me"}]},
            "S3": {"actor": "", "actor_words": "", "terms": [{"term": "Conversion therapy", "mode": "describes"}],
                   "tags": [{"tag": "tactic: Made-Up", "mode": "uses", "cue": ""}]},
            "S4": {"actor": "", "actor_words": "", "terms": [], "tags": []}},
        "excerpts": [{"kind": "testimony", "unit": "P1", "first_words": "I prayed every night",
                      "last_words": "and I left the ministry."},
                     {"kind": "prayer_script", "unit": "P1", "first_words": "I prayed every night",
                      "last_words": "a closing line that is not in the text"}],
        "new_terms": [{"term": "SSA coaching", "unit": "P2", "quote": "The group used SSA coaching."},
                      {"term": "conversion therapy", "unit": "P2", "quote": "Critics call it conversion therapy."},
                      {"term": "pray-away", "unit": "P1", "quote": "a sentence that is not in the text"}]}


def second_reply():
    return {
        "statements": {
            "S1": {"actor": "", "actor_words": "", "terms": [{"term": "ex-gay", "mode": "describes"}],
                   "tags": [{"tag": "tactic: Identity-Erasure", "mode": "describes", "cue": ""}]},
            "S2": {"actor": "", "actor_words": "", "terms": [],
                   "tags": [{"tag": "practice: Spiritual Healing", "mode": "uses", "cue": ""}]},
            "S3": {"actor": "", "actor_words": "", "terms": [{"term": "conversion therapy", "mode": "describes"}],
                   "tags": []},
            "S4": {"actor": "", "actor_words": "", "terms": [],
                   "tags": [{"tag": "function: Euphemism", "mode": "uses", "cue": "SSA coaching"}]}},
        "excerpts": [{"kind": "testimony", "unit": "P1", "first_words": "I prayed every night",
                      "last_words": "accept myself,"}],
        "new_terms": [{"term": "SSA coaching", "unit": "P2", "quote": "The group used SSA coaching."}]}


def test_statements_for_takes_each_statement_once(conn):
    statements, runs = tag.statements_for(conn, SID, primary_model="reader-a")
    assert [s["id"] for s in statements] == ["use_s1", "use_s2", "use_s3", "use_s4"]
    assert runs == ["run_read_a", "run_read_b"]
    terms = tag.terms_in(conn, statements)
    assert [t["concept_id"] for t in terms["use_s1"]] == ["term:ex-gay"]
    assert [t["concept_id"] for t in terms["use_s3"]] == ["term:conversion-therapy"]
    assert terms["use_s2"] == [] and terms["use_s4"] == []


def test_lists_are_seeded_once_and_follow_the_prompt(conn):
    with conn:
        counts = tag.seed_lists(conn, actor_id="claude-test")
        assert counts == {"actor_types": 19, "clusters": 7, "function_definitions": 1}
        assert tag.seed_lists(conn, actor_id="claude-test") == {"actor_types": 0, "clusters": 0}   # idempotent
    euphemism = conn.execute("SELECT definition, definition_source FROM concept WHERE id = 'function:euphemism'").fetchone()
    assert euphemism["definition"].startswith("A softer word") and euphemism["definition_source"] == "model"
    edit = conn.execute("SELECT * FROM decision WHERE target_id = 'function:euphemism'").fetchone()
    assert edit["action"] == "edit" and edit["actor_id"] == "claude-test" and edit["run_id"]
    old = tag.vocabulary(conn, "tag-v1")
    assert old.tags["tactic: Identity-Erasure"] == "tactic:identity-erasure"
    vocab = tag.vocabulary(conn)                      # tag-v2: clusters, not tactics
    assert "cluster: SSA-Rhetoric" in vocab.tags and "cluster: Pro-Trans-SOGICE" in vocab.tags
    assert not any(label.startswith("tactic:") for label in vocab.tags)
    assert "function: Euphemism" in vocab.tags and "activist or campaigner" in vocab.actors
    assert "Does it rename or reframe homosexuality" in vocab.text and "A softer word" in vocab.text
    assert vocab.sha256 != old.sha256


def test_tag_v2_takes_two_stated_roles_and_clusters(conn):
    reply = {"statements": {
        "S1": {"actors": [{"type": "leader or founder", "words": "president of Exodus"},
                          {"type": "went through change efforts", "words": "I am not ex-gay"},
                          {"type": "journalist", "words": "president of Exodus"}],     # a third is not kept
               "terms": [{"term": "ex-gay", "mode": "uses"}],
               "tags": [{"tag": "cluster: SSA-Rhetoric", "mode": "uses", "cue": ""}]},
        "S2": {"actors": [], "terms": [], "tags": []}, "S3": {"actors": [], "terms": [], "tags": []},
        "S4": {"actors": [], "terms": [], "tags": []}}, "excerpts": [], "new_terms": []}
    client = FakeClient([reply])
    results = tag.tag_sources(conn, client, FIRST, [SID], primary_model="reader-a")
    assert results[0]["outcome"] == "succeeded" and results[0]["tags"] == 4
    item = client.calls[0]["schema"]["properties"]["statements"]["properties"]["S1"]
    assert item["properties"]["actors"]["maxItems"] == 2 and "actor" not in item["properties"]
    run = conn.execute("SELECT prompt_version FROM run WHERE role = 'extraction'").fetchone()
    assert run[0] == "tag-v2"
    concepts = {r[0] for r in conn.execute("SELECT concept_id FROM tag")}
    assert concepts == {"actor_type:leader-or-founder", "actor_type:went-through-change-efforts",
                        "term:ex-gay", "cluster:ssa-rhetoric"}


def test_tagging_keeps_what_software_can_find(conn):
    client = FakeClient([first_reply()])
    results = tag.tag_sources(conn, client, FIRST, [SID], primary_model="reader-a", prompt=V1)
    assert results[0]["outcome"] == "succeeded", results
    assert client.unloaded == ["tagger-a"]
    system, user = client.calls[0]["messages"]
    assert "practice: Spiritual Healing" in system["content"] and "[S4] (P2)" in user["content"]
    assert "Lexicon terms in it: ex-gay" in user["content"]
    schema = client.calls[0]["schema"]["properties"]["statements"]
    assert schema["required"] == ["S1", "S2", "S3", "S4"]
    r = results[0]
    assert r["tags"] == 5 and r["actor_unlocated"] == 1 and r["terms_not_listed"] == 1
    assert r["tags_invalid"] == 1 and r["cues_unlocated"] == 1
    assert r["excerpts"] == 1 and r["excerpts_unlocated"] == 1
    assert r["new_terms"] == 1 and r["new_terms_known"] == 1 and r["new_terms_unlocated"] == 1
    tags = {(t["usage_id"], t["concept_id"]): t for t in conn.execute("SELECT * FROM tag")}
    actor = tags[("use_s1", "actor_type:leader-or-founder")]
    assert actor["mode"] == "is" and actor["cue"] == "president of Exodus"
    assert tags[("use_s1", "term:ex-gay")]["mode"] == "describes" and tags[("use_s1", "term:ex-gay")]["cue"] == "ex-gay"
    assert tags[("use_s3", "term:conversion-therapy")]["cue"] == "conversion therapy"
    assert tags[("use_s1", "tactic:identity-erasure")]["cue"] is None       # unfound cue dropped, tag kept
    assert tags[("use_s2", "practice:spiritual-healing")]["cue"] == "for God to heal me"
    for t in tags.values():                       # every cue is the exact slice of its passage
        if t["cue"]:
            text = conn.execute("SELECT text FROM passage WHERE id = ?", (t["cue_passage_id"],)).fetchone()[0]
            assert text[t["cue_start"]:t["cue_end"]] == t["cue"]
    testimony = conn.execute("SELECT * FROM asset WHERE kind = 'testimony'").fetchone()
    whole = TEXT_1 + TEXT_2
    assert testimony["text"] == whole[testimony["char_start"]:testimony["char_end"]]
    assert testimony["text"].startswith("I prayed every night") and testimony["text"].endswith("left the ministry.")
    assert testimony["passage_id"] == "p1" and testimony["speaker"] == "Alan Chambers"
    term = conn.execute("SELECT * FROM asset WHERE kind = 'new_term'").fetchone()
    assert term["label"] == "SSA coaching" and whole[term["char_start"]:term["char_end"]] == "SSA coaching"
    decided = conn.execute("SELECT target_table, COUNT(*) FROM decision WHERE action = 'propose'"
                           " GROUP BY target_table").fetchall()
    assert dict((d[0], d[1]) for d in decided) == {"tag": 5, "asset": 2}
    run = conn.execute("SELECT * FROM run WHERE role = 'extraction'").fetchone()
    inputs = json.loads(run["inputs"])
    assert inputs["job"] == "tagging" and inputs["reading_runs"] == ["run_read_a", "run_read_b"]
    assert run["prompt_version"] == "tag-v1" and db.verify_decision_chain(conn).ok
    # the same prompt, vocabulary and readings: not tagged again
    assert tag.tag_sources(conn, FakeClient([]), FIRST, [SID], primary_model="reader-a", prompt=V1)[0]["outcome"] == "skipped"


def test_tagging_again_supersedes_the_earlier_run(conn):
    tag.tag_sources(conn, FakeClient([first_reply()]), FIRST, [SID], primary_model="reader-a", prompt=V1)
    tag.tag_sources(conn, FakeClient([first_reply()]), FIRST, [SID], primary_model="reader-a", again=True, prompt=V1)
    states = dict(conn.execute("SELECT review_state, COUNT(*) FROM tag GROUP BY review_state").fetchall())
    assert states == {"superseded": 5, "proposed": 5}
    assert conn.execute("SELECT COUNT(*) FROM decision WHERE target_table = 'run' AND action = 'supersede'"
                        ).fetchone()[0] == 1


def test_a_cut_off_reply_is_tagged_in_two_halves(conn):
    half = {"statements": {"S1": {"actor": "", "actor_words": "", "terms": [], "tags": []},
                           "S2": {"actor": "", "actor_words": "", "terms": [],
                                  "tags": [{"tag": "function: Euphemism", "mode": "uses", "cue": ""}]}},
            "excerpts": [], "new_terms": []}
    client = FakeClient(["truncate", half, half])
    results = tag.tag_sources(conn, client, FIRST, [SID], primary_model="reader-a", prompt=V1)
    assert results[0]["outcome"] == "succeeded" and len(client.calls) == 3
    second_half = client.calls[2]["messages"][1]["content"]
    assert "[S1] (P2)" in second_half and "Critics call it" in second_half
    tagged = {r[0] for r in conn.execute("SELECT usage_id FROM tag")}
    assert tagged == {"use_s2", "use_s4"}          # S2 of each half: the 2nd and the 4th statement


def test_failed_source_is_marked_and_nothing_written(conn):
    results = tag.tag_sources(conn, FakeClient([RuntimeError("down"), RuntimeError("down")]), FIRST, [SID],
                              primary_model="reader-a", prompt=V1)
    assert results[0]["outcome"] == "failed"
    assert conn.execute("SELECT outcome FROM run WHERE role = 'extraction'").fetchone()[0] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM tag").fetchone()[0] == 0


def test_compare_agrees_disputes_and_holds(conn):
    tag.tag_sources(conn, FakeClient([first_reply()]), FIRST, [SID], primary_model="reader-a", prompt=V1)
    tag.tag_sources(conn, FakeClient([second_reply()]), SECOND, [SID], primary_model="reader-a", prompt=V1)
    outcome = tag.compare(conn, [SID], primary_model="tagger-a", second_model="tagger-b")
    counts = outcome["counts"]
    assert counts["tag:two_readers_agree"] == 6           # ex-gay, conversion therapy, spiritual healing, x2
    assert counts["tag:readers_disagree"] == 2            # identity erasure: uses vs describes
    assert counts["tag:one_reader"] == 2                  # the actor type (first), the euphemism (second)
    assert counts["asset:two_readers_agree"] == 4         # the testimony and the new term, x2

    def state(model, concept, usage):
        return conn.execute("SELECT t.review_state FROM tag t JOIN run r ON r.id = t.run_id"
                            " WHERE r.model = ? AND t.concept_id = ? AND t.usage_id = ?",
                            (model, concept, usage)).fetchone()[0]
    assert state("tagger-a", "term:ex-gay", "use_s1") == "proposed"
    assert state("tagger-b", "term:ex-gay", "use_s1") == "superseded"
    assert state("tagger-a", "tactic:identity-erasure", "use_s1") == "disputed"
    assert state("tagger-b", "tactic:identity-erasure", "use_s1") == "disputed"
    assert state("tagger-b", "function:euphemism", "use_s4") == "proposed"
    assets = dict(conn.execute("SELECT r.model || ':' || a.kind, a.review_state FROM asset a JOIN run r"
                               " ON r.id = a.run_id").fetchall())
    assert assets == {"tagger-a:testimony": "proposed", "tagger-a:new_term": "proposed",
                      "tagger-b:testimony": "superseded", "tagger-b:new_term": "superseded"}
    # comparing again gives the same states; earlier assessments are no longer current
    before = conn.execute("SELECT id, review_state FROM tag ORDER BY id").fetchall()
    again = tag.compare(conn, [SID], primary_model="tagger-a", second_model="tagger-b")
    assert again["counts"] == counts
    assert conn.execute("SELECT id, review_state FROM tag ORDER BY id").fetchall() == before
    current = conn.execute("SELECT COUNT(*) FROM assessment WHERE current = 1 AND target_table = 'tag'").fetchone()[0]
    assert current == 10
    assert db.verify_decision_chain(conn).ok


def test_version_slice_needs_the_whole_span(conn):
    assert tag.version_slice(conn, VID, 0, 5) == TEXT_1[:5]
    assert tag.version_slice(conn, VID, len(TEXT_1) - 3, len(TEXT_1) + 5) == (TEXT_1 + TEXT_2)[len(TEXT_1) - 3:len(TEXT_1) + 5]
    assert tag.version_slice(conn, VID, 0, len(TEXT_1) + len(TEXT_2) + 1) is None


def test_build_messages_for_the_dry_run(conn):
    with conn:
        tag.seed_lists(conn, actor_id="claude-test")
    statements, _ = tag.statements_for(conn, SID, primary_model="reader-a")
    sections, unplaced = tag.sections_for(conn, SID, statements, tag.terms_in(conn, statements))
    assert unplaced == 0 and len(sections) == 1 and [s["sid"] for s in sections[0].statements] == ["S1", "S2", "S3", "S4"]
    messages = tag.build_messages(read.load_prompt("tag-v2"), tag.vocabulary(conn), "Source URL: x", sections[0], (1, 1))
    assert messages[0]["content"].startswith("You tag statements")
    assert "speaker: Alan Chambers, president of Exodus; stance: criticizes" in messages[1]["content"]


def test_tag_expectations_score_what_would_be_published(conn, tmp_path):
    tag.tag_sources(conn, FakeClient([first_reply()]), FIRST, [SID], primary_model="reader-a", prompt=V1)
    tag.tag_sources(conn, FakeClient([second_reply()]), SECOND, [SID], primary_model="reader-a", prompt=V1)
    tag.compare(conn, [SID], primary_model="tagger-a", second_model="tagger-b")
    base = tmp_path / "testset.json"
    base.write_text(json.dumps({"version": "t", "entries": [
        {"key": "a", "urls": [URL], "split": "dev", "expect": []}]}), encoding="utf-8")
    tags = tmp_path / "tags.json"
    expect = [
        {"id": "healing", "check": "tag", "anchor": "God to heal me", "tag_any": ["practice: Spiritual Healing"],
         "mode": "uses"},
        {"id": "erasure", "check": "tag", "anchor": "I never was", "tag_any": ["tactic: Identity-Erasure*"]},
        {"id": "leader", "check": "actor", "anchor": "I never was", "actor_any": ["leader or founder"]},
        {"id": "testimony", "check": "excerpt", "anchor": "accept myself", "kind_any": ["testimony"]},
        {"id": "no-prayer", "check": "excerpt", "anchor": "accept myself", "kind_any": ["prayer_script"],
         "must": "none"},
        {"id": "no-tactic", "check": "tag", "anchor": "Critics call it", "tag_any": ["tactic:*"], "must": "none"},
        {"id": "ex-gay", "check": "tag", "anchor": "not ex-gay", "tag_any": ["term: ex-gay"], "mode": "describes"}]
    tags.write_text(json.dumps({"version": "tags-t", "entries": [{"key": "a", "expect": expect}]}), encoding="utf-8")
    data = testset.load_with_tags(base, tags)
    both = {r["id"]: r["status"] for r in testset.score(conn, data)["results"]}
    assert both == {"healing": "pass", "erasure": "fail", "leader": "fail", "testimony": "pass",
                    "no-prayer": "pass", "no-tactic": "pass", "ex-gay": "pass"}   # disputed and held do not count
    first = {r["id"]: r["status"] for r in testset.score(conn, data, tagger="tagger-a")["results"]}
    assert first["erasure"] == first["leader"] == "pass"                   # the first tagger's own proposals
    report = testset.score(conn, data, tagger="tagger-b")
    assert report["metrics"]["view"] == "tagger-b" and "Tag expectations" in testset.to_markdown(report)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"entries": [{"key": "a", "expect": [{"id": "u", "check": "usage", "anchor": "x"}]}]}))
    with pytest.raises(testset.TestsetError):
        testset.load_with_tags(base, bad)
