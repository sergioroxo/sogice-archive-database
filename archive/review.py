"""The review queue: records the readers could not settle, ruled on by a reviewer.

After ``reconcile``, three kinds of record wait for review:

- **statement_dispute**: the two readers recorded the same words with a
  different speaker or an opposite stance; both copies are ``disputed``.
- **relation_dispute**: the same two names with a different relation type;
  both copies are ``disputed``.
- **relation_held**: a relation only one reader proposed (assessment
  ``one_reader`` or ``single_reading``). It stays ``proposed`` but is *held*:
  not published until a reviewer confirms it (decided 10 Nov 2026; a
  relation links two named people or bodies, so one model's word is not
  enough).

``prepare`` writes one packet per issue to ``data/review/open/``: the source
text around the quotes (pages or times kept), the readings labelled A/B in a
random order (model names hidden), what differs, who speaks elsewhere in the
source (statements both readers agreed on) and the names and terms the
archive already knows in the passage (entity registry, lexicon definitions).
The key (which record and model is A) goes to ``data/review/keys/``, which the
reviewer must not open. File names start with a priority (1: consequential
relations, 2: speaker or stance disputes, 3: other relations), so the queue
is worked in order.

The reviewer, a Claude Code session following ``prompts/review-v2.md`` (the
``/review-queue`` command), writes one verdict per packet to
``data/review/verdicts/``. ``apply`` checks each verdict (the deciding quote
must be found in the source text, the records must still be in the state the
packet showed) and records it as decisions: the chosen record ``confirmed``,
the other ``rejected``; a low-confidence verdict or "needs_researcher" goes
to ``data/review/researcher/`` and changes nothing. A reviewer never marks
anything researcher-verified. ``undo`` reverses a whole review run.
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .db import canonical_json, record_decision, utc_now
from .read import ATTRIBUTIONS, STANCES, load_prompt, source_header
from .relationship_vocabulary import CONSEQUENTIAL_TYPES, RELATIONSHIP_TYPES
from .testset import norm

PROMPT = "review-v2"
KINDS = ("statement_dispute", "relation_dispute", "relation_held")
HELD_CONFIDENCE = ("one_reader", "single_reading")
VERDICTS = {"statement_dispute": ("A", "B", "neither", "needs_researcher"),
            "relation_dispute": ("A", "B", "neither", "needs_researcher"),
            "relation_held": ("A", "neither", "needs_researcher")}
CORRECTABLE = {"usage": ("speaker", "quoted_party", "intermediary", "attribution_mode", "stance"),
               "relation": ("type", "reverse")}
_ENDS = ("label", "entity_id", "concept_id")
_SETTABLE = {"usage": set(CORRECTABLE["usage"]),
             "relation": {"type", "resolution_status"} | {f"{side}_{x}" for side in ("subject", "object") for x in _ENDS},
             "source": {"title", "publisher"}}   # title repairs (archive/titles.py) are undone the same way
CONFIDENCE = ("high", "medium", "low")
CONTEXT_CHARS = 400
FOLDERS = ("open", "keys", "verdicts", "done", "researcher")


def folder(base: Path | None = None) -> Path:
    root = Path(base) if base else paths.data_dir() / "review"
    for name in FOLDERS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------- what is held

def held_relation_ids(conn: sqlite3.Connection, relation_ids: list[str] | None = None) -> set[str]:
    """Relations proposed by one reader only and not yet confirmed: not published."""
    marks = "', '".join(HELD_CONFIDENCE)
    sql = (f"SELECT r.id FROM relation r JOIN assessment a ON a.target_table = 'relation' AND a.target_id = r.id"
           f" AND a.current = 1 WHERE r.review_state = 'proposed' AND a.confidence IN ('{marks}')")
    if relation_ids is None:
        return {row[0] for row in conn.execute(sql)}
    found = set()
    for i in range(0, len(relation_ids), 500):
        chunk = relation_ids[i:i + 500]
        found |= {row[0] for row in conn.execute(sql + f" AND r.id IN ({','.join('?' for _ in chunk)})", chunk)}
    return found


# --------------------------------------------------------------------------- issues

@dataclass
class Issue:
    id: str
    kind: str
    table: str
    source_id: str
    records: list[dict]                 # one or two records (dicts of the row)
    confidence: str
    priority: int = 3
    labels: dict = field(default_factory=dict)   # "A"/"B" -> record


def issue_id(record_ids: list[str]) -> str:
    return "iss_" + hashlib.sha256("|".join(sorted(record_ids)).encode()).hexdigest()[:16]


def _source_filter(source_ids: list[str] | None) -> tuple[str, list]:
    if not source_ids:
        return "", []
    return f" AND a.source_id IN ({','.join('?' for _ in source_ids)})", list(source_ids)


def find_issues(conn: sqlite3.Connection, source_ids: list[str] | None = None) -> list[Issue]:
    conn.row_factory = sqlite3.Row
    where, args = _source_filter(source_ids)
    issues, seen = [], set()
    for table in ("usage", "relation"):
        rows = conn.execute(
            f"""SELECT a.target_id, a.counterpart_id, a.source_id FROM assessment a JOIN {table} t ON t.id = a.target_id
                WHERE a.current = 1 AND a.target_table = ? AND a.confidence = 'readers_disagree'
                AND t.review_state = 'disputed'{where} ORDER BY a.target_id""", [table, *args]).fetchall()
        for target, counterpart, sid in rows:
            pair = tuple(sorted([target, counterpart or ""]))
            if pair in seen or not counterpart:
                continue
            seen.add(pair)
            recs = [dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (x,)).fetchone()) for x in pair]
            if any(r["review_state"] != "disputed" for r in recs):
                left = [r for r in recs if r["review_state"] == "disputed"]
                if table == "relation" and len(left) == 1:   # a rule ruled on the other copy: this one alone
                    issues.append(Issue(issue_id([left[0]["id"]]), "relation_held", table, sid, left, "readers_disagree"))
                continue
            kind = "statement_dispute" if table == "usage" else "relation_dispute"
            issues.append(Issue(issue_id(list(pair)), kind, table, sid, recs, "readers_disagree"))
    marks = "', '".join(HELD_CONFIDENCE)
    for rid, sid, confidence in conn.execute(
            f"""SELECT r.id, a.source_id, a.confidence FROM relation r JOIN assessment a ON a.target_table = 'relation'
                AND a.target_id = r.id AND a.current = 1
                WHERE r.review_state = 'proposed' AND a.confidence IN ('{marks}'){where} ORDER BY r.id""", args):
        rec = dict(conn.execute("SELECT * FROM relation WHERE id = ?", (rid,)).fetchone())
        issues.append(Issue(issue_id([rid]), "relation_held", "relation", sid, [rec], confidence))
    for issue in issues:
        issue.priority = _priority(issue)
    return issues


def _priority(issue: Issue) -> int:
    if issue.table == "relation":
        return 1 if any(r["type"] in CONSEQUENTIAL_TYPES for r in issue.records) else 3
    return 2


# --------------------------------------------------------------------------- packets

def _evidence(conn: sqlite3.Connection, issue: Issue, record: dict) -> list[dict]:
    """The located usages a record rests on (a statement is its own evidence)."""
    if issue.table == "usage":
        return [record]
    return [dict(r) for r in conn.execute(
        """SELECT u.* FROM usage u JOIN relation_usage ru ON ru.usage_id = u.id
           WHERE ru.relation_id = ? AND u.passage_id IS NOT NULL ORDER BY u.char_start""", (record["id"],))]


def _passage(conn: sqlite3.Connection, passage_id: str) -> dict:
    return dict(conn.execute("SELECT * FROM passage WHERE id = ?", (passage_id,)).fetchone())


def _neighbour(conn: sqlite3.Connection, p: dict, step: int) -> dict | None:
    row = conn.execute("SELECT * FROM passage WHERE version_id = ? AND sequence = ?",
                       (p["version_id"], p["sequence"] + step)).fetchone()
    return dict(row) if row else None


def _where(p: dict) -> str:
    if p.get("page_start") is not None:
        end = p["page_start"] + p["text"].count("\f")
        return f"page {p['page_start']}" + (f"–{end}" if end != p["page_start"] else "")
    if p.get("time_start") is not None:
        s = int(p["time_start"])
        return f"at {s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}"
    return ""


def _marked(p: dict, spans: list[tuple[int, int]]) -> str:
    """Passage text with ⟦ ⟧ around the quoted spans and [page N] at page breaks."""
    text = p["text"]
    merged: list[list[int]] = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, pos = [], 0
    for s, e in merged:
        out += [text[pos:s], "⟦", text[s:e], "⟧"]
        pos = e
    out.append(text[pos:])
    body = "".join(out)
    if p.get("page_start") is not None:
        page = p["page_start"]
        parts = body.split("\f")
        body = parts[0] + "".join(f"\n[page {page + i}]\n{part}" for i, part in enumerate(parts[1:], 1))
    return body


def _speakers(conn: sqlite3.Connection, source_id: str, limit: int = 20) -> list[tuple[str, int]]:
    return conn.execute(
        """SELECT u.speaker, COUNT(*) n FROM assessment a JOIN usage u ON u.id = a.target_id
           WHERE a.current = 1 AND a.target_table = 'usage' AND a.source_id = ?
           AND (a.confidence = 'two_readers_agree' OR u.review_state = 'confirmed')
           AND u.review_state IN ('proposed', 'confirmed') AND u.speaker IS NOT NULL AND u.speaker <> ''
           GROUP BY u.speaker ORDER BY n DESC, u.speaker LIMIT ?""", (source_id, limit)).fetchall()


def _known(conn: sqlite3.Connection, passage_ids: list[str], names: list[str]) -> list[str]:
    """Entity-registry names and lexicon terms matched exactly in these passages, plus named parties."""
    from .seeds import normalize_label
    lines, seen = [], set()
    marks = ",".join("?" for _ in passage_ids)
    for row in conn.execute(
            f"""SELECT DISTINCT c.id, c.pref_label, c.definition FROM usage u JOIN concept c ON c.id = u.concept_id
                WHERE u.found_by = 'exact_match' AND u.passage_id IN ({marks}) ORDER BY c.pref_label""", passage_ids):
        if row[0] not in seen:
            seen.add(row[0])
            definition = (row[2] or "no definition yet").replace("\n", " ")
            lines.append(f"- term **{row[1]}**: {definition[:300]}")
    entity_ids = [r[0] for r in conn.execute(
        f"SELECT DISTINCT entity_id FROM usage WHERE found_by = 'exact_match' AND entity_id IS NOT NULL"
        f" AND passage_id IN ({marks})", passage_ids)]
    for name in names:
        key = normalize_label(name or "")
        if not key or key == "unknown":
            continue
        entity_ids += [r[0] for r in conn.execute(
            "SELECT id FROM entity WHERE normalized_label = ? UNION SELECT entity_id FROM entity_variant"
            " WHERE normalized_label = ?", (key, key))]
    for eid in dict.fromkeys(entity_ids):
        if eid in seen:
            continue
        seen.add(eid)
        row = conn.execute("SELECT label, kind FROM entity WHERE id = ?", (eid,)).fetchone()
        variants = [r[0] for r in conn.execute(
            "SELECT label FROM entity_variant WHERE entity_id = ? AND label <> ? LIMIT 4", (eid, row[0]))]
        lines.append(f"- {row[1]} **{row[0]}**" + (f" (also: {', '.join(variants)})" if variants else ""))
    return lines[:30]


def _describe(issue: Issue, record: dict, evidence: list[dict]) -> list[str]:
    if issue.table == "usage":
        claim = "yes" if record.get("_claim") else "no"
        return [f"- quote: “{record['quote']}”", f"- speaker: {record['speaker'] or ''}",
                f"- quoted party: {record['quoted_party'] or ''}", f"- intermediary: {record['intermediary'] or ''}",
                f"- attribution: {record['attribution_mode']}", f"- stance: {record['stance']}"] + (
                [f"- position on a ban: {record['ban_position']}"] if record.get("ban_position") else []) + [
                f"- recorded as a checkable claim: {claim}"]
    lines = [f"- relation: **{record['subject_label']}** `{record['type']}` **{record['object_label']}**",
             f"- stated by: {record['attribution'] or ''}"]
    lines += [f"- evidence quote: “{u['quote']}”" for u in evidence] or ["- evidence quote: (none located)"]
    return lines


def _differences(issue: Issue) -> list[str]:
    if len(issue.records) < 2:
        return []
    a, b = issue.labels["A"], issue.labels["B"]
    fields = ("speaker", "quoted_party", "intermediary", "attribution_mode", "stance") if issue.table == "usage" \
        else ("subject_label", "type", "object_label", "attribution")
    return [f"- {f}: A {a[f]!r} / B {b[f]!r}" for f in fields if norm(a[f]) != norm(b[f])]


QUESTIONS = {
    "statement_dispute": "The two readers recorded the same words differently. Which reading does the source "
                         "text support: A, B, neither (say what is right), or needs_researcher?",
    "relation_dispute": "The two readers name the same two parties but give a different relation. Which does "
                        "the text state: A, B, neither (say which type, or none), or needs_researcher?",
    "relation_held": "This relation is held from publication (only one reader proposed it, or the other "
                     "reader's version was settled by a rule). Does the text state it, as written (A), not at "
                     "all or differently (neither), or needs_researcher?",
}


def _quoted_block(text: str) -> list[str]:
    return ["> " + line if line.strip() else ">" for line in text.replace("\f", "\n").split("\n")]


def render(conn: sqlite3.Connection, issue: Issue) -> tuple[str, list[str], list[str]]:
    """The packet text, the passage ids it shows, and the neighbouring passages shown as context."""
    conn.row_factory = sqlite3.Row
    evidence = {r["id"]: _evidence(conn, issue, r) for r in issue.records}
    spans: dict[str, list[tuple[int, int]]] = {}
    for rows in evidence.values():
        for u in rows:
            if u["passage_id"] and u["char_start"] is not None:
                spans.setdefault(u["passage_id"], []).append((u["char_start"], u["char_end"]))
    if issue.table == "usage":
        for r in issue.records:
            r["_claim"] = bool(conn.execute("SELECT 1 FROM claim_usage WHERE usage_id = ?", (r["id"],)).fetchone())
    passages = [_passage(conn, pid) for pid in spans]
    names = [r.get(f) for r in issue.records for f in ("speaker", "quoted_party", "intermediary",
                                                       "subject_label", "object_label")]
    prompts = sorted({row[0] for r in issue.records for row in conn.execute(
        "SELECT r.prompt_version FROM usage u JOIN run r ON r.id = u.run_id WHERE u.id = ?",
        (evidence[r["id"]][0]["id"] if evidence[r["id"]] else "",)) if row[0]})
    lines = [f"# Review {issue.id}", "", f"Kind: {issue.kind.replace('_', ' ')}",
             f"Reading rules: {', '.join(f'prompts/{v}.md' for v in prompts) or 'see prompts/'}", "",
             f"**Question.** {QUESTIONS[issue.kind]}", "", "## Source", "", source_header(conn, issue.source_id), ""]
    speakers = _speakers(conn, issue.source_id)
    if speakers:
        lines += ["## Who speaks elsewhere in this source (statements both readers agreed on)", ""]
        lines += [f"- {name} ({n})" for name, n in speakers] + [""]
    known = _known(conn, [p["id"] for p in passages], names)
    if known:
        lines += ["## Names and terms the archive already knows here", ""] + known + [""]
    context: list[str] = []
    for p in passages:
        where = _where(p)
        lines += [f"## Source text{f' ({where})' if where else ''}; ⟦ ⟧ marks the quoted words", ""]
        before = _neighbour(conn, p, -1)
        if before:
            context.append(before["id"])
            lines += ["Before it (end of the previous passage, context):", ""]
            lines += _quoted_block("…" + before["text"][-CONTEXT_CHARS:]) + ["", "The passage:", ""]
        lines += [_marked(p, spans[p["id"]]), ""]
        after = _neighbour(conn, p, 1)
        if after:
            context.append(after["id"])
            lines += ["After it (start of the next passage, context):", ""]
            lines += _quoted_block(after["text"][:CONTEXT_CHARS] + "…") + [""]
    for label, record in issue.labels.items():
        lines += [f"## Reading {label}", ""] + _describe(issue, record, evidence[record["id"]]) + [""]
    if issue.kind == "relation_held":
        lines += ["The other reader " + ("read this source and recorded no matching relation."
                                         if issue.confidence == "one_reader"
                                         else "has not read this source yet."), ""]
    diffs = _differences(issue)
    if diffs:
        lines += ["## What differs", ""] + diffs + [""]
    allowed = ", ".join(VERDICTS[issue.kind])
    fields = ", ".join(CORRECTABLE[issue.table])
    lines += ["## Your verdict", "",
              f"Write `verdicts/{issue.id}.json`. verdict: one of {allowed}. With neither you may give "
              f"`correct` (fields: {fields}). quote: the words in the source text that decide it, copied "
              f"exactly. confidence: high, medium or low. See prompts/{PROMPT}.md.", ""]
    return "\n".join(lines), [p["id"] for p in passages], context


def prepare(conn: sqlite3.Connection, source_ids: list[str] | None = None, *, base: Path | None = None,
            seed: int = 0) -> dict:
    """Write packets for open issues; drop packets whose issue is gone. Idempotent."""
    root = folder(base)
    issues = find_issues(conn, source_ids)
    current = {i.id for i in issues}
    closed = {p.stem.split("-", 1)[1] for d in ("done", "researcher") for p in (root / d).glob("*.md")}
    existing = {p.stem.split("-", 1)[1]: p for p in (root / "open").glob("*.md")}
    dropped = 0
    scope = set(source_ids or [])
    for iid, path in existing.items():
        key_path = root / "keys" / f"{iid}.json"
        key = json.loads(key_path.read_text()) if key_path.exists() else {}
        in_scope = not scope or key.get("source_id") in scope
        if iid not in current and in_scope and not (root / "verdicts" / f"{iid}.json").exists():
            path.unlink()
            key_path.unlink(missing_ok=True)
            dropped += 1
    written = Counter()
    for issue in issues:
        if issue.id in existing or issue.id in closed:
            continue
        rng = random.Random(f"{seed}:{issue.id}")
        order = list(issue.records)
        rng.shuffle(order)
        issue.labels = dict(zip("AB", order))
        text, passage_ids, context_ids = render(conn, issue)
        (root / "open" / f"{issue.priority}-{issue.id}.md").write_text(text, encoding="utf-8")
        models = {r["id"]: _model_of(conn, issue.table, r["id"]) for r in issue.records}
        key = {"issue": issue.id, "kind": issue.kind, "table": issue.table, "source_id": issue.source_id,
               "priority": issue.priority, "confidence": issue.confidence, "passages": passage_ids,
               "context_passages": context_ids,
               "prepared_at": utc_now(), "packet_sha256": hashlib.sha256(text.encode()).hexdigest(),
               "labels": {label: {"id": r["id"], "state": r["review_state"], "model": models[r["id"]]}
                          for label, r in issue.labels.items()}}
        (root / "keys" / f"{issue.id}.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
        written[issue.kind] += 1
    return {"issues": len(issues), "written": dict(written), "dropped": dropped,
            "open": len(list((root / "open").glob("*.md")))}


def _model_of(conn: sqlite3.Connection, table: str, record_id: str) -> str | None:
    if table == "usage":
        row = conn.execute("SELECT r.model FROM usage u JOIN run r ON r.id = u.run_id WHERE u.id = ?",
                           (record_id,)).fetchone()
    else:
        row = conn.execute("""SELECT r.model FROM relation_usage ru JOIN usage u ON u.id = ru.usage_id
                              JOIN run r ON r.id = u.run_id WHERE ru.relation_id = ? LIMIT 1""", (record_id,)).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------- applying verdicts

class VerdictError(ValueError):
    pass


def check_verdict(conn: sqlite3.Connection, verdict: dict, key: dict) -> dict:
    """Validate one verdict against its key; returns it normalised. Raises VerdictError."""
    kind = key["kind"]
    choice = verdict.get("verdict")
    if choice not in VERDICTS[kind]:
        raise VerdictError(f"verdict must be one of {VERDICTS[kind]}, not {choice!r}")
    confidence = verdict.get("confidence", "medium")
    if confidence not in CONFIDENCE:
        raise VerdictError(f"confidence must be one of {CONFIDENCE}")
    reason = (verdict.get("reason") or "").strip()
    if not reason:
        raise VerdictError("a reason is required")
    quote = (verdict.get("quote") or "").strip()
    if choice != "needs_researcher":
        if not quote:
            raise VerdictError("a deciding quote is required")
        shown = key["passages"] + key.get("context_passages", [])
        texts = [norm(r[0]) for r in conn.execute(
            f"SELECT text FROM passage WHERE id IN ({','.join('?' for _ in shown)})", shown)]
        if not any(norm(quote) in t for t in texts):
            raise VerdictError("the deciding quote is not in the source text")
    correct = verdict.get("correct") or {}
    if correct and choice != "neither":
        raise VerdictError("corrections go with verdict 'neither'")
    for name, value in correct.items():
        if name not in CORRECTABLE[key["table"]]:
            raise VerdictError(f"cannot correct {name!r}")
        if name == "stance" and value not in STANCES:
            raise VerdictError(f"stance {value!r} is not in the list")
        if name == "attribution_mode" and value not in ATTRIBUTIONS:
            raise VerdictError(f"attribution {value!r} is not in the list")
        if name == "type" and value not in RELATIONSHIP_TYPES:
            raise VerdictError(f"relation type {value!r} is not in the vocabulary")
        if name == "reverse" and value is not True:
            raise VerdictError("reverse must be true (swap subject and object)")
    for label, info in key["labels"].items():
        state = conn.execute(f"SELECT review_state FROM {key['table']} WHERE id = ?", (info["id"],)).fetchone()
        if state is None or state[0] != info["state"]:
            raise VerdictError(f"reading {label} has changed since the packet was written (stale)")
    return {**verdict, "verdict": choice, "confidence": confidence, "reason": reason, "quote": quote,
            "correct": correct}


def _set(conn: sqlite3.Connection, table: str, record_id: str, values: dict) -> dict:
    """Set columns on a record (claims follow a usage's review state); returns the previous values."""
    allowed = _SETTABLE[table] | {"review_state"}
    values = {k: v for k, v in values.items() if k in allowed}
    row = conn.execute(f"SELECT {', '.join(values)} FROM {table} WHERE id = ?", (record_id,)).fetchone()
    before = dict(zip(values, row))
    conn.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                 [*values.values(), record_id])
    if table == "usage" and "review_state" in values:
        conn.execute("UPDATE claim SET review_state = ? WHERE id IN (SELECT claim_id FROM claim_usage WHERE usage_id = ?)",
                     (values["review_state"], record_id))
    return before


def apply(conn: sqlite3.Connection, *, reviewer: str, base: Path | None = None, prompt_version: str = PROMPT) -> dict:
    """Record every verdict in ``verdicts/`` as decisions, in one run; move the packets on."""
    root = folder(base)
    prompt = load_prompt(prompt_version)
    run_id = f"run_{uuid.uuid4().hex}"
    counts: Counter = Counter()
    problems, moves = [], []
    with conn:
        conn.execute("INSERT INTO run(id, role, model, prompt_version, inputs, started_at) VALUES (?, 'audit', ?, ?, ?, ?)",
                     (run_id, reviewer, prompt.version,
                      canonical_json({"job": "review", "prompt_sha256": prompt.sha256}), utc_now()))
        for path in sorted((root / "verdicts").glob("*.json")):
            iid = path.stem
            key_path = root / "keys" / f"{iid}.json"
            packet = next((root / "open").glob(f"*-{iid}.md"), None)
            if not key_path.exists() or packet is None:
                problems.append({"issue": iid, "problem": "no open packet for this verdict"})
                continue
            key = json.loads(key_path.read_text())
            try:
                verdict = check_verdict(conn, json.loads(path.read_text()), key)
            except (VerdictError, json.JSONDecodeError) as exc:
                problems.append({"issue": iid, "problem": str(exc)})
                counts["not_applied"] += 1
                continue
            outcome = _apply_one(conn, run_id, reviewer, key, verdict)
            counts[outcome] += 1
            counts[f"{key['kind']}:{outcome}"] += 1
            moves.append((outcome, packet, key_path, path))
        inputs = {"job": "review", "prompt_sha256": prompt.sha256, "counts": dict(sorted(counts.items())),
                  "problems": problems[:50]}
        conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ?, inputs = ? WHERE id = ?",
                     (utc_now(), canonical_json(inputs), run_id))
    for outcome, packet, key_path, verdict_path in moves:
        target = root / ("researcher" if outcome == "needs_researcher" else "done")
        shutil.move(str(packet), str(target / packet.name))
        shutil.move(str(key_path), str(target / f"{key_path.stem}.key.json"))
        shutil.move(str(verdict_path), str(target / f"{verdict_path.stem}.verdict.json"))
    return {"run_id": run_id, "counts": dict(sorted(counts.items())), "problems": problems}


def _apply_one(conn: sqlite3.Connection, run_id: str, reviewer: str, key: dict, verdict: dict) -> str:
    table, labels = key["table"], key["labels"]
    choice = verdict["verdict"]
    if verdict["confidence"] == "low" and choice != "needs_researcher":
        choice = "needs_researcher"
    note = f" [issue {key['issue']}; confidence {verdict['confidence']}"
    note += f"; pattern: {verdict['pattern']}]" if verdict.get("pattern") else "]"

    def decide(label, action, values, audit):
        record_id = labels[label]["id"]
        before = _set(conn, table, record_id, values) if values else {}
        record_decision(conn, actor_kind="model", actor_id=reviewer, run_id=run_id, action=action,
                        target_table=table, target_id=record_id, reason=verdict["reason"] + note,
                        quote=verdict["quote"] or None, audit_verdict=audit,
                        previous_state=before or None, new_state=values or None)

    if choice == "needs_researcher":
        for label in labels:
            decide(label, "audit", {}, "uncertain")
        return "needs_researcher"
    if choice in ("A", "B"):
        decide(choice, "confirm", {"review_state": "confirmed"}, "pass")
        if table == "relation":
            _fold_duplicate(conn, run_id, reviewer, key, labels[choice]["id"])
        for label in labels:
            if label != choice:
                decide(label, "reject", {"review_state": "rejected"}, "fail")
        return "confirmed"
    # neither
    if verdict["correct"]:
        values = dict(verdict["correct"])
        if values.pop("reverse", False):
            values.update(_reversed(conn, labels["A"]["id"]))
        decide("A", "edit", {**values, "review_state": "confirmed"}, "fail")
        if table == "relation":
            _fold_duplicate(conn, run_id, reviewer, key, labels["A"]["id"])
        for label in labels:
            if label != "A":
                decide(label, "reject", {"review_state": "rejected"}, "fail")
        return "corrected"
    for label in labels:
        decide(label, "reject", {"review_state": "rejected"}, "fail")
    return "rejected"


def _reversed(conn: sqlite3.Connection, relation_id: str) -> dict:
    row = conn.execute("SELECT * FROM relation WHERE id = ?", (relation_id,)).fetchone()
    values = {f"subject_{x}": row[f"object_{x}"] for x in _ENDS} | {f"object_{x}": row[f"subject_{x}"] for x in _ENDS}
    status = {"subject_resolved": "object_resolved", "object_resolved": "subject_resolved"}
    values["resolution_status"] = status.get(row["resolution_status"], row["resolution_status"])
    return values


def _fold_duplicate(conn: sqlite3.Connection, run_id: str, reviewer: str, key: dict, relation_id: str) -> None:
    """If the same relation (same names, same type) is already confirmed in this source, fold this one into it."""
    row = conn.execute("SELECT * FROM relation WHERE id = ?", (relation_id,)).fetchone()
    for other in conn.execute(
            """SELECT DISTINCT r.* FROM relation r JOIN relation_usage ru ON ru.relation_id = r.id
               JOIN usage u ON u.id = ru.usage_id JOIN passage p ON p.id = u.passage_id
               JOIN source_version v ON v.id = p.version_id
               WHERE v.source_id = ? AND r.id <> ? AND r.review_state = 'confirmed' AND r.type = ?""",
            (key["source_id"], relation_id, row["type"])):
        if norm(other["subject_label"]) == norm(row["subject_label"]) and norm(other["object_label"]) == norm(row["object_label"]):
            before = _set(conn, "relation", relation_id, {"review_state": "superseded"})
            record_decision(conn, actor_kind="model", actor_id=reviewer, run_id=run_id, action="merge",
                            target_table="relation", target_id=relation_id, audit_verdict="pass",
                            reason=f"Same relation as {other['id']}, already confirmed in this source; folded into "
                                   f"it so it is counted once. [issue {key['issue']}]",
                            previous_state=before, new_state={"review_state": "superseded", "merged_into": other["id"]})
            return


def undo(conn: sqlite3.Connection, run_id: str, *, reason: str, actor_kind: str = "researcher",
         actor_id: str = "sergio") -> int:
    """Reverse every decision of one review run (newest first); returns how many."""
    undone = 0
    with conn:
        rows = conn.execute("SELECT * FROM decision WHERE run_id = ? AND action <> 'undo' ORDER BY seq DESC",
                            (run_id,)).fetchall()
        for d in rows:
            if conn.execute("SELECT 1 FROM decision WHERE reverts_decision_id = ?", (d["id"],)).fetchone():
                continue
            previous = json.loads(d["previous_state"] or "null")
            current = None
            if previous:
                current = _set(conn, d["target_table"], d["target_id"], previous)
            record_decision(conn, actor_kind=actor_kind, actor_id=actor_id, run_id=None if actor_kind == "researcher"
                            else run_id, action="undo", target_table=d["target_table"], target_id=d["target_id"],
                            reason=reason, reverts_decision_id=d["id"], previous_state=current,
                            new_state=previous)
            undone += 1
    return undone


def status(base: Path | None = None) -> dict:
    root = folder(base)
    open_packets = sorted(p.name for p in (root / "open").glob("*.md"))
    waiting = {p.stem for p in (root / "verdicts").glob("*.json")}
    by_priority = Counter(name.split("-", 1)[0] for name in open_packets)
    kinds = Counter()
    for name in open_packets:
        key_path = root / "keys" / f"{name.split('-', 1)[1][:-3]}.json"
        if key_path.exists():
            kinds[json.loads(key_path.read_text())["kind"]] += 1
    return {"open": len(open_packets), "by_priority": dict(sorted(by_priority.items())), "by_kind": dict(kinds),
            "verdicts_waiting": len(waiting), "done": len(list((root / "done").glob("*.md"))),
            "for_researcher": len(list((root / "researcher").glob("*.md"))), "folder": str(root)}

