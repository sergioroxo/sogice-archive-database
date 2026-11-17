"""One report for every tagging trial (docs/TAGGING.md, docs/ORCHESTRATION.md S5). No model, read-only.

    python scripts/tagging_report.py --db data/scratch/tag-dev4.db --split dev
    python scripts/tagging_report.py --db COPY --split dev \
        --calibration data/scratch/review-tags-dev2.db --runs RUN_ID RUN_ID      # compare with reviewers' rulings
    python scripts/tagging_report.py --db COPY --sample-folder data/scratch/sample-dev4   # agreed tags, after 'review apply'

It prints:

- agreement between the two taggers per kind of tag (actor types also per speaker and type), from the
  current comparison (table ``assessment``); tags a software rule rejected are left out;
- the review queue the trial would give, by kind (``review.find_issues``; nothing is written);
- with ``--calibration``: what became of each tag the calibration reviewers ruled on. Statements keep
  their ids across copies of one trial database, so a tag is the same (statement, label) in both;
- with ``--sample-folder``: the error rate of agreed tags from a worked sample (``review prepare
  --sample-agreed N``, then blind reviewers, then ``review apply``).

``--split dev`` keeps the dev sources of the test set (keys and URLs only; no expectation is read).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from archive import review, tag  # noqa: E402

RULE_REJECTED = ("EXISTS (SELECT 1 FROM decision d WHERE d.target_table = 'tag' AND d.target_id = t.id"
                 " AND d.action = 'reject' AND d.actor_id LIKE 'rule:%')")


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _filter(source_ids, column="a.source_id"):
    if not source_ids:
        return "", []
    return f" AND {column} IN ({','.join('?' for _ in source_ids)})", list(source_ids)


def _rows(conn, source_ids):
    where, args = _filter(source_ids)
    return conn.execute(
        f"""SELECT t.id, t.usage_id, t.concept_id, t.mode, a.source_id, a.confidence, a.counterpart_id, a.proposed_by,
                   c.scheme, u.speaker
            FROM tag t JOIN assessment a ON a.target_table = 'tag' AND a.target_id = t.id AND a.current = 1
            JOIN concept c ON c.id = t.concept_id JOIN usage u ON u.id = t.usage_id
            WHERE NOT (t.review_state = 'rejected' AND {RULE_REJECTED}){where}""", args).fetchall()


def _share(row: dict) -> dict:
    total = row["both"] + row["disputed"] + sum(row["only"].values())
    row["share_agreed"] = round(row["both"] / total, 3) if total else None
    return row


def agreement(conn: sqlite3.Connection, source_ids=None) -> dict:
    """Per kind: tags both taggers gave (same mode), disputed (opposite modes), and given by one tagger only."""
    out: dict = {}
    speakers: dict = {}
    for r in _rows(conn, source_ids):
        row = out.setdefault(r["scheme"], {"both": 0, "disputed": 0, "only": Counter()})
        models = json.loads(r["proposed_by"] or "[]")
        paired_once = r["counterpart_id"] is None or r["id"] < r["counterpart_id"]
        if r["confidence"] == "two_readers_agree":
            row["both"] += paired_once
        elif r["confidence"] == "readers_disagree":
            row["disputed"] += paired_once
        else:
            row["only"][models[0] if models else "?"] += 1
        if r["scheme"] == "actor_type":
            key = (r["source_id"], tag._speaker_key(r["speaker"]) or r["usage_id"], r["concept_id"])
            speakers.setdefault(key, set()).update(models)
    result = {scheme: _share({**row, "only": dict(row["only"])}) for scheme, row in sorted(out.items())}
    per_speaker = {"both": 0, "disputed": 0, "only": Counter()}
    for models in speakers.values():
        if len(models) > 1:
            per_speaker["both"] += 1
        else:
            per_speaker["only"][next(iter(models), "?")] += 1
    if speakers:
        result["actor_type_per_speaker"] = _share({**per_speaker, "only": dict(per_speaker["only"])})
    return result


def queue(conn: sqlite3.Connection, source_ids=None) -> dict:
    """The tag side of the review queue this database would give, by kind (nothing is written)."""
    kinds: Counter = Counter()
    statements = actor_packets = 0
    for issue in review.tag_issues(conn, source_ids):
        kinds[issue.kind] += 1
        if issue.kind == "tag_held":
            statements += 1 + len(issue.group)
            actor_packets += issue.records[0].get("scheme") == "actor_type"
    return {"packets": sum(kinds.values()), "by_kind": dict(sorted(kinds.items())),
            "statements_in_held_tag_packets": statements, "actor_type_packets": actor_packets,
            "lexicon_candidates": len(review.lexicon_candidates(conn, source_ids))}


VERDICT = {"confirm": "right", "reject": "wrong", "edit": "corrected"}


def rulings(cal: sqlite3.Connection, run_ids: list[str]) -> list[dict]:
    """What the reviewers ruled on each tag, as it was given: (statement, label, mode, tagger) -> verdict."""
    marks = ",".join("?" for _ in run_ids)
    out = {}
    for d in cal.execute(
            f"""SELECT d.target_id, d.action, d.previous_state, t.usage_id, t.concept_id, t.mode, r.model, u.speaker,
                       a.confidence
                FROM decision d JOIN tag t ON t.id = d.target_id JOIN run r ON r.id = t.run_id
                JOIN usage u ON u.id = t.usage_id
                LEFT JOIN assessment a ON a.target_table = 'tag' AND a.target_id = t.id AND a.current = 1
                WHERE d.run_id IN ({marks}) AND d.target_table = 'tag' AND d.action IN ('confirm', 'reject', 'edit')
                  AND NOT EXISTS (SELECT 1 FROM decision x WHERE x.reverts_decision_id = d.id)
                ORDER BY d.seq""", run_ids):
        before = json.loads(d["previous_state"] or "{}")
        concept_id = before.get("concept_id") or d["concept_id"]      # as the tagger gave it, before a correction
        mode = before.get("mode") or d["mode"]
        scheme = cal.execute("SELECT scheme FROM concept WHERE id = ?", (concept_id,)).fetchone()
        out[d["target_id"]] = {"usage_id": d["usage_id"], "concept_id": concept_id, "mode": mode,
                               "scheme": scheme[0] if scheme else "?", "tagger": d["model"], "speaker": d["speaker"],
                               "was": d["confidence"] or "?", "verdict": VERDICT[d["action"]]}
    return list(out.values())


def status_now(conn: sqlite3.Connection, usage_id: str, concept_id: str, mode: str) -> str:
    """What the trial database says of this (statement, label) now."""
    rows = conn.execute(
        """SELECT t.mode, a.confidence FROM tag t JOIN assessment a ON a.target_table = 'tag' AND a.target_id = t.id
           AND a.current = 1 WHERE t.usage_id = ? AND t.concept_id = ? AND t.review_state <> 'rejected'""",
        (usage_id, concept_id)).fetchall()
    if not rows:
        ruled = conn.execute(
            f"SELECT 1 FROM tag t WHERE t.usage_id = ? AND t.concept_id = ? AND t.review_state = 'rejected' AND {RULE_REJECTED}",
            (usage_id, concept_id)).fetchone()
        return "dropped by a rule" if ruled else "not given"
    confidences = {r["confidence"] for r in rows}
    if "two_readers_agree" in confidences:
        return "agreed" if any(r["mode"] == mode for r in rows) else "agreed, other mode"
    if "readers_disagree" in confidences:
        return "disputed"
    return "one tagger" if any(r["mode"] == mode for r in rows) else "one tagger, other mode"


def against_rulings(conn: sqlite3.Connection, cal: sqlite3.Connection, run_ids: list[str]) -> dict:
    """For each tag the calibration reviewers ruled on: where it stands in this trial.

    Good: a tag ruled wrong is no longer given (or dropped by a rule); a tag ruled right is now agreed.
    Bad: a tag ruled wrong is now agreed (it would be published); a tag ruled right is no longer given.
    """
    table: dict = {}
    for r in rulings(cal, run_ids):
        now = status_now(conn, r["usage_id"], r["concept_id"], r["mode"])
        table.setdefault(r["scheme"], {}).setdefault(r["verdict"], Counter())[now] += 1
    summary = Counter()
    for by_verdict in table.values():
        for verdict, counts in by_verdict.items():
            for now, n in counts.items():
                gone = now in ("not given", "dropped by a rule")
                if verdict == "wrong":
                    summary["wrong: no longer given" if gone else "wrong: now agreed" if now.startswith("agreed")
                            else "wrong: still for review"] += n
                else:
                    summary[f"{verdict}: lost" if gone else f"{verdict}: now agreed" if now == "agreed"
                            else f"{verdict}: still for review"] += n
    return {"by_kind": {s: {v: dict(c) for v, c in sorted(bv.items())} for s, bv in sorted(table.items())},
            "summary": dict(sorted(summary.items()))}


def sample_result(conn: sqlite3.Connection, folder: Path) -> dict:
    """Error rate of agreed tags from a worked sample: the ``tag_sample`` packets in ``folder``/done."""
    out: dict = {}
    for path in sorted((Path(folder) / "done").glob("*.key.json")):
        key = json.loads(path.read_text())
        if key.get("kind") != "tag_sample":
            continue
        last = conn.execute(
            """SELECT action FROM decision WHERE target_table = 'tag' AND target_id = ?
               AND action IN ('confirm', 'reject', 'edit') ORDER BY seq DESC LIMIT 1""", (key["labels"]["A"]["id"],)).fetchone()
        row = out.setdefault(key.get("scheme") or "?", Counter())
        row[VERDICT[last[0]] if last else "not ruled"] += 1
    result = {}
    for scheme, row in sorted(out.items()):
        ruled = row["right"] + row["wrong"] + row["corrected"]
        result[scheme] = {**dict(row), "ruled": ruled,
                          "error_rate": round((row["wrong"] + row["corrected"]) / ruled, 3) if ruled else None}
    total = Counter()
    for row in out.values():
        total.update(row)
    ruled = total["right"] + total["wrong"] + total["corrected"]
    result["all"] = {**dict(total), "ruled": ruled,
                     "error_rate": round((total["wrong"] + total["corrected"]) / ruled, 3) if ruled else None}
    return result


def dev_sources(conn: sqlite3.Connection, split: str, file: Path) -> list[str]:
    from archive import read, testset
    data = testset.load_testset(file)
    return sorted({read.keeper(conn, s) for e in data["entries"] if e["split"] == split
                   for s in testset.entry_sources(e)})


def render(report: dict) -> str:
    lines = ["# Tagging report", "", "## Agreement between the taggers", "",
             "| Kind | Both | Mode disputed | One tagger only | Share agreed |", "|---|---:|---:|---|---:|"]
    for scheme, row in report["agreement"].items():
        only = ", ".join(f"{m}: {n}" for m, n in sorted(row["only"].items())) or "0"
        share = "–" if row["share_agreed"] is None else f"{row['share_agreed']:.0%}"
        lines.append(f"| {scheme.replace('_', ' ')} | {row['both']} | {row['disputed']} | {only} | {share} |")
    q = report["queue"]
    lines += ["", "## The review queue (tags and excerpts)", "", f"- packets: {q['packets']}"]
    lines += [f"- {kind.replace('_', ' ')}: {n}" for kind, n in q["by_kind"].items()]
    lines += [f"- statements in held-tag packets: {q['statements_in_held_tag_packets']}",
              f"- actor-type packets: {q['actor_type_packets']}",
              f"- new terms on the lexicon candidates list (not packets): {q['lexicon_candidates']}"]
    if "calibration" in report:
        lines += ["", "## Against the calibration rulings", ""]
        lines += [f"- {k}: {n}" for k, n in report["calibration"]["summary"].items()]
        for scheme, by_verdict in report["calibration"]["by_kind"].items():
            for verdict, counts in by_verdict.items():
                lines.append(f"- {scheme.replace('_', ' ')}, ruled {verdict}: "
                             + ", ".join(f"{now} {n}" for now, n in sorted(counts.items())))
    if "sample" in report:
        lines += ["", "## Agreed tags, sampled", ""]
        for scheme, row in report["sample"].items():
            rate = "–" if row["error_rate"] is None else f"{row['error_rate']:.0%}"
            lines.append(f"- {scheme.replace('_', ' ')}: {row['ruled']} ruled, {row.get('right', 0)} right, "
                         f"{row.get('wrong', 0)} wrong, {row.get('corrected', 0)} corrected: error rate {rate}")
    return "\n".join(lines) + "\n"


def build(conn, source_ids=None, *, cal=None, run_ids=None, sample_folder=None) -> dict:
    report = {"agreement": agreement(conn, source_ids), "queue": queue(conn, source_ids)}
    if cal is not None and run_ids:
        report["calibration"] = against_rulings(conn, cal, run_ids)
    if sample_folder:
        report["sample"] = sample_result(conn, Path(sample_folder))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, required=True, help="a trial database (opened read-only)")
    ap.add_argument("--split", choices=["dev"], help="only the dev sources of the test set")
    ap.add_argument("--file", type=Path, default=ROOT / "evaluation" / "testset_2026-11-10.json")
    ap.add_argument("--calibration", type=Path, help="the database that holds the reviewers' rulings")
    ap.add_argument("--runs", nargs="+", help="with --calibration: the review run ids")
    ap.add_argument("--sample-folder", type=Path, help="a worked queue folder with tag_sample packets")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    conn = _open(args.db)
    sources = dev_sources(conn, args.split, args.file) if args.split else None
    cal = _open(args.calibration) if args.calibration else None
    report = build(conn, sources, cal=cal, run_ids=args.runs, sample_folder=args.sample_folder)
    print(json.dumps(report, indent=1, ensure_ascii=False) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
