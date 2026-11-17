"""scripts/tagging_report.py: one measurement for every tagging trial."""
import importlib.util
import json
from pathlib import Path

from archive import db, read, review, tag
from tests.test_review_tags import REVIEWER, _keys, _tagged_and_checked, _verdict
from tests.test_tag import FIRST, SECOND, SID, V1, FakeClient, conn, first_reply, second_reply  # noqa: F401

SPEC = importlib.util.spec_from_file_location(
    "tagging_report", Path(__file__).resolve().parent.parent / "scripts" / "tagging_report.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def test_agreement_and_queue_are_counted_per_kind(conn):
    _tagged_and_checked(conn)
    got = report.agreement(conn, [SID])
    assert got["practice"] == {"both": 1, "disputed": 0, "only": {}, "share_agreed": 1.0}
    assert got["tactic"]["disputed"] == 1 and got["tactic"]["both"] == 0
    assert got["function"]["only"] == {"tagger-b": 1} and got["function"]["share_agreed"] == 0.0
    assert got["term"]["both"] == 2
    # the leader type: two statements of one speaker, one tagger: 2 tags, 1 per speaker and type
    assert got["actor_type"]["only"] == {"tagger-a": 2}
    assert got["actor_type_per_speaker"] == {"both": 0, "disputed": 0, "only": {"tagger-a": 1}, "share_agreed": 0.0}
    q = report.queue(conn, [SID])
    assert q["by_kind"] == {"excerpt_checked": 1, "tag_checked": 1, "tag_dispute": 1, "tag_held": 2}
    assert q["packets"] == 5 and q["actor_type_packets"] == 1 and q["statements_in_held_tag_packets"] == 3
    assert q["lexicon_candidates"] == 1
    text = report.render({"agreement": got, "queue": q})
    assert "| practice | 1 | 0 | 0 | 100% |" in text and "actor-type packets: 1" in text


def test_a_new_trial_is_compared_with_the_calibration_rulings_and_a_sample_is_scored(conn, tmp_path):
    _tagged_and_checked(conn, healing="yes", testimony="yes")
    conn.commit()
    cal = db.connect(tmp_path / "calibration.db")   # the calibration copy: reviewers rule on it
    conn.backup(cal)
    queue = tmp_path / "cal-queue"
    review.prepare(cal, base=queue)
    held = _keys(queue, "tag_held")
    actor = next(k for k in held if k["scheme"] == "actor_type")
    euphemism = next(k for k in held if k["scheme"] == "function")
    _verdict(queue, actor, verdict="neither", quote="president of Exodus", reason="Not stated of him.", confidence="high")
    _verdict(queue, euphemism, verdict="A", quote="SSA coaching", reason="A euphemism.", confidence="high")
    run_id = review.apply(cal, reviewer=REVIEWER, base=queue)["run_id"]
    ruled = report.rulings(cal, [run_id])
    assert sorted((r["scheme"], r["verdict"], r["tagger"]) for r in ruled) == [
        ("actor_type", "wrong", "tagger-a"), ("actor_type", "wrong", "tagger-a"), ("function", "right", "tagger-b")]
    # the trial copy, unchanged: both are still for review
    same = report.against_rulings(conn, cal, [run_id])
    assert same["summary"] == {"right: still for review": 1, "wrong: still for review": 2}
    # a new run of the first tagger: it now gives the euphemism too, and no leader type
    reply = first_reply()
    reply["statements"]["S1"]["actor"] = reply["statements"]["S1"]["actor_words"] = ""
    reply["statements"]["S4"]["tags"] = [{"tag": "function: Euphemism", "mode": "uses", "cue": ""}]
    tag.tag_sources(conn, FakeClient([reply]), FIRST, [SID], primary_model="reader-a", prompt=V1, again=True)
    tag.compare(conn, [SID], primary_model="tagger-a", second_model="tagger-b")
    after = report.against_rulings(conn, cal, [run_id])
    assert after["summary"] == {"right: now agreed": 1, "wrong: no longer given": 2}
    assert after["by_kind"]["actor_type"] == {"wrong": {"not given": 2}}
    # a worked sample of agreed tags gives their error rate
    sample = tmp_path / "sample"
    review.prepare(conn, base=sample, sample_agreed=10)
    for key in _keys(sample, "tag_sample"):
        _verdict(sample, key, verdict="A", quote="for God to heal me" if key["scheme"] == "practice" else "SSA coaching",
                 reason="Shown.", confidence="high")
    review.apply(conn, reviewer=REVIEWER, base=sample)
    scored = report.sample_result(conn, sample)
    assert scored["all"]["ruled"] == 2 and scored["all"]["error_rate"] == 0.0 and set(scored) == {"all", "function", "practice"}
    built = report.build(conn, [SID], cal=cal, run_ids=[run_id], sample_folder=sample)
    assert "Against the calibration rulings" in report.render(built) and json.dumps(built)
    cal.close()


def test_tag_v4_says_what_the_reviewers_found(conn):
    v4 = read.load_prompt("tag-v4").text
    for phrase in ("Copy those words, not the name", "is not a ban", "first-person account",
                   "A function needs the words that do it", "A general mention", "Never words about someone else"):
        assert phrase in v4
    assert len(v4) <= len(read.load_prompt("tag-v3").text)      # not longer: reading the input is half of a run


def test_tag_v5_keeps_v4_and_brings_back_the_term_rules(conn):
    v5 = read.load_prompt("tag-v5").text
    for phrase in ("Copy those words, not the name", "Never words about someone else", "A general mention",
                   "plainly uses it", "never left out", "funds or orders change efforts",
                   "about sexuality or gender"):
        assert phrase in v5
    assert len(v5) <= len(read.load_prompt("tag-v3").text)      # not longer: reading the input is half of a run
    assert tag.PROMPT_LISTS["tag-v5"] == tag.PROMPT_LISTS["tag-v4"]
    assert "actor_rules" in tag.PROMPT_FEATURES["tag-v5"]
