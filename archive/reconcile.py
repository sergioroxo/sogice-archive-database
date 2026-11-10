"""Two readings, one record: compare the models' readings of a source.

Each source is read by two models (roles ``reading`` and ``reading_second``),
independently and with the same prompt. Their readings are layers: neither
replaces the other. This module compares them and records, for every
statement and relation, how sure we can be and who looked at it.

Statements are paired when their located quotes overlap in the same passage
(at least ``MIN_OVERLAP`` of the shorter quote). For each pair:

- **two_readers_agree**: same speaker (one name contains the other, or they
  share a surname-length word) and no opposite stance. The primary reader's
  usage stays; the second one is folded into it (``superseded``, a 'merge'
  decision), so nothing is counted twice. Smaller differences (attribution
  mode, quoted party) are kept in ``differences``.
- **readers_disagree**: different speakers, or opposite stances (endorses
  against criticizes/denies/minimizes). Both usages become ``disputed`` (a
  'dispute' decision each) and wait for review; neither is asserted.
- **one_reader**: only one model recorded the statement. It stays, and the
  assessment says so: both models read the passage, one proposed it.

Relations are paired by subject, type and object (names compared loosely);
the same two names with different types is a disagreement. Claims follow
their usage. A source read by one model only gets ``single_reading``.

Only the two configured readers are compared (``models``), and only runs
that have not been superseded: an old reading by another model, kept in the
database for the record, never takes part.

Every assessment lists the readers (model, run, prompt, when) and which of
them proposed the record. Re-running is safe: states set by an earlier
reconciliation are restored first (to the state recorded before it); a
researcher's decision is never undone. Records of runs no longer compared
that an earlier reconciliation changed get an 'undo' decision back to the
state their run implies (superseded run: superseded).
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter, defaultdict

from .db import canonical_json, record_decision, utc_now

RULE = "reconcile/v2"
ACTOR_ID = f"rule:{RULE}"
ACTOR_PREFIX = "rule:reconcile/"   # earlier versions of this rule too
MIN_OVERLAP = 0.3
STANCE_POLE = {"endorses": "+", "criticizes": "-", "denies": "-", "minimizes": "-"}
_STOP = {"the", "and", "of", "for", "mme", "mrs", "miss", "president", "director", "member", "board",
         "chair", "founder", "pastor", "interviewer", "host", "guest"}


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def same_speaker(a: str | None, b: str | None) -> bool:
    x, y = _norm(a), _norm(b)
    if x == y:
        return True
    if not x or not y or "unknown" in (x, y):
        return False
    if x in y or y in x:
        return True
    words = lambda s: {w for w in s.split() if len(w) >= 4 and w not in _STOP}
    return bool(words(x) & words(y))


def same_name(a: str | None, b: str | None) -> bool:
    return same_speaker(a, b)


def stances_clash(a: str, b: str) -> bool:
    return {STANCE_POLE.get(a), STANCE_POLE.get(b)} == {"+", "-"}


# --------------------------------------------------------------------------- what to compare

def _readings_query(models: list[str] | None) -> tuple[str, list]:
    where = f" AND r.model IN ({','.join('?' for _ in models)})" if models else ""
    return ("""SELECT * FROM run r WHERE role = 'reading' AND outcome = 'succeeded'
           AND json_extract(inputs, '$.source_id') = ?
           AND NOT EXISTS (SELECT 1 FROM decision d WHERE d.target_table = 'run' AND d.target_id = r.id
                           AND d.action = 'supersede')
           AND NOT EXISTS (SELECT 1 FROM run later WHERE later.role = 'reading' AND later.outcome = 'succeeded'
                           AND later.model = r.model AND later.started_at > r.started_at
                           AND json_extract(later.inputs, '$.source_id') = json_extract(r.inputs, '$.source_id'))"""
            + where + " ORDER BY model", list(models or []))


def current_readings(conn: sqlite3.Connection, source_id: str, models: list[str] | None = None) -> list[sqlite3.Row]:
    """The latest succeeded, not superseded reading run of each model (of ``models``, if given)."""
    sql, extra = _readings_query(models)
    return conn.execute(sql, [source_id, *extra]).fetchall()


def _last_reconcile_decision(conn: sqlite3.Connection, table: str, record_id: str):
    last = conn.execute("SELECT * FROM decision WHERE target_table = ? AND target_id = ? ORDER BY seq DESC LIMIT 1",
                        (table, record_id)).fetchone()
    if last and last["actor_id"].startswith(ACTOR_PREFIX) and last["action"] in ("merge", "dispute"):
        return last
    return None


def _set_claims(conn: sqlite3.Connection, usage_id: str, state: str) -> None:
    conn.execute("UPDATE claim SET review_state = ? WHERE id IN (SELECT claim_id FROM claim_usage WHERE usage_id = ?)",
                 (state, usage_id))


def _restore(conn: sqlite3.Connection, table: str, ids: list[str]) -> None:
    """Undo states an earlier reconciliation set on these records (researcher decisions stay)."""
    for record_id in ids:
        last = _last_reconcile_decision(conn, table, record_id)
        if last:
            before = json.loads(last["previous_state"] or "{}").get("review_state", "proposed")
            conn.execute(f"UPDATE {table} SET review_state = ? WHERE id = ?", (before, record_id))
            if table == "usage":  # its claims followed it
                _set_claims(conn, record_id, before)


def _run_records(conn: sqlite3.Connection, run_id: str) -> tuple[list[str], list[str]]:
    usages = [r[0] for r in conn.execute("SELECT id FROM usage WHERE run_id = ?", (run_id,))]
    relations = [r[0] for r in conn.execute(
        "SELECT DISTINCT ru.relation_id FROM relation_usage ru JOIN usage u ON u.id = ru.usage_id WHERE u.run_id = ?",
        (run_id,))]
    return usages, relations


def _release_others(conn: sqlite3.Connection, source_id: str, compared: set[str], run_id: str) -> int:
    """Records of reading runs not compared now, changed by an earlier reconciliation: undo that change."""
    undone = 0
    for other in conn.execute("""SELECT r.id, EXISTS (SELECT 1 FROM decision d WHERE d.target_table = 'run'
                                        AND d.target_id = r.id AND d.action = 'supersede') AS superseded
                                 FROM run r WHERE role = 'reading' AND json_extract(inputs, '$.source_id') = ?""",
                              (source_id,)).fetchall():
        if other["id"] in compared:
            continue
        state = "superseded" if other["superseded"] else "proposed"
        usages, relations = _run_records(conn, other["id"])
        for table, ids in (("usage", usages), ("relation", relations)):
            for record_id in ids:
                last = _last_reconcile_decision(conn, table, record_id)
                if not last:
                    continue
                current = conn.execute(f"SELECT review_state FROM {table} WHERE id = ?", (record_id,)).fetchone()[0]
                conn.execute(f"UPDATE {table} SET review_state = ? WHERE id = ?", (state, record_id))
                if table == "usage":
                    _set_claims(conn, record_id, state)
                record_decision(conn, actor_kind="model", actor_id=ACTOR_ID, run_id=run_id, action="undo",
                                target_table=table, target_id=record_id, reverts_decision_id=last["id"],
                                reason=f"This record's run ({other['id']}) is not one of the readings compared now"
                                       f"{' (it was superseded)' if other['superseded'] else ''}; the earlier "
                                       f"'{last['action']}' is undone.",
                                previous_state={"review_state": current}, new_state={"review_state": state})
                undone += 1
    return undone


def _statements(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT * FROM usage WHERE run_id = ? AND entity_id IS NULL AND evidence_state <> 'unlocated'
           AND review_state NOT IN ('rejected', 'superseded', 'disputed')""", (run_id,))]


def _relations(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT DISTINCT rel.* FROM relation rel JOIN relation_usage ru ON ru.relation_id = rel.id
           JOIN usage u ON u.id = ru.usage_id WHERE u.run_id = ?
           AND rel.review_state NOT IN ('rejected', 'superseded', 'disputed')""", (run_id,))]


def _overlap(a: dict, b: dict) -> float:
    if a["passage_id"] != b["passage_id"]:
        return 0.0
    shared = min(a["char_end"], b["char_end"]) - max(a["char_start"], b["char_start"])
    shortest = min(a["char_end"] - a["char_start"], b["char_end"] - b["char_start"])
    return shared / shortest if shared > 0 and shortest > 0 else 0.0


def pair_statements(first: list[dict], second: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Greedy one-to-one pairing by overlap. Returns (pairs, only_first, only_second)."""
    candidates = sorted(((_overlap(a, b), i, j) for i, a in enumerate(first) for j, b in enumerate(second)),
                        reverse=True)
    used_a, used_b, pairs = set(), set(), []
    for score, i, j in candidates:
        if score < MIN_OVERLAP:
            break
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((first[i], second[j]))
    return (pairs, [a for i, a in enumerate(first) if i not in used_a],
            [b for j, b in enumerate(second) if j not in used_b])


def compare(a: dict, b: dict) -> tuple[bool, dict]:
    """(material disagreement?, differences)."""
    differences = {}
    for field in ("speaker", "quoted_party", "intermediary", "attribution_mode", "stance"):
        if _norm(a.get(field)) != _norm(b.get(field)):
            differences[field] = [a.get(field), b.get(field)]
    clash = (not same_speaker(a["speaker"], b["speaker"])) or stances_clash(a["stance"], b["stance"])
    return clash, differences


# --------------------------------------------------------------------------- reconcile one source

def reconcile_source(conn: sqlite3.Connection, source_id: str, *, primary_model: str, run_id: str,
                     models: list[str] | None = None) -> Counter:
    """Compare the current readings of one source by ``models`` (default: every model; caller commits)."""
    runs = current_readings(conn, source_id, models)
    counts: Counter = Counter()
    undone = _release_others(conn, source_id, {r["id"] for r in runs}, run_id)
    if undone:
        counts["undone"] += undone
    conn.execute("UPDATE assessment SET current = 0 WHERE source_id = ? AND current = 1", (source_id,))
    if not runs:
        return counts
    for r in runs:  # restore first, then read the records' states
        usages, relations = _run_records(conn, r["id"])
        _restore(conn, "usage", usages)
        _restore(conn, "relation", relations)
    readers = [{"model": r["model"], "run_id": r["id"], "prompt": r["prompt_version"],
                "read_at": r["finished_at"] or r["started_at"]} for r in runs]
    runs = sorted(runs, key=lambda r: r["model"] != primary_model)       # primary first
    statements = {r["id"]: _statements(conn, r["id"]) for r in runs}
    relations = {r["id"]: _relations(conn, r["id"]) for r in runs}
    model_of = {r["id"]: r["model"] for r in runs}
    now = utc_now()

    def assess(table, record, confidence, *, counterpart=None, differences=None, proposed_by):
        conn.execute(
            "INSERT INTO assessment(id, run_id, source_id, target_table, target_id, counterpart_id, confidence,"
            " readers, proposed_by, differences, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"asm_{uuid.uuid4().hex[:20]}", run_id, source_id, table, record["id"], counterpart and counterpart["id"],
             confidence, canonical_json(readers), canonical_json(sorted(proposed_by)),
             canonical_json(differences) if differences else None, now))
        counts[f"{table}:{confidence}"] += 1

    def set_state(table, record, state, action, reason, *, verdict="not_audited", new_state=None):
        conn.execute(f"UPDATE {table} SET review_state = ? WHERE id = ?", (state, record["id"]))
        if table == "usage":  # a claim stands or falls with its usage
            _set_claims(conn, record["id"], state)
        record_decision(conn, actor_kind="model", actor_id=ACTOR_ID, run_id=run_id, action=action,
                        target_table=table, target_id=record["id"], reason=reason, quote=record.get("quote"),
                        audit_verdict=verdict, previous_state={"review_state": record["review_state"]},
                        new_state={"review_state": state, **(new_state or {})})

    if len(runs) == 1:
        only = runs[0]
        for u in statements[only["id"]]:
            assess("usage", u, "single_reading", proposed_by=[only["model"]])
        for x in relations[only["id"]]:
            assess("relation", x, "single_reading", proposed_by=[only["model"]])
        return counts

    if len(runs) > 2:
        raise ValueError(f"{source_id}: {len(runs)} models' readings; name the two to compare (models=…)")
    first, second = runs[0], runs[1]
    pairs, only_first, only_second = pair_statements(statements[first["id"]], statements[second["id"]])
    both = [first["model"], second["model"]]
    for a, b in pairs:
        clash, differences = compare(a, b)
        if clash:
            for record, other in ((a, b), (b, a)):
                assess("usage", record, "readers_disagree", counterpart=other, differences=differences,
                       proposed_by=both)
                set_state("usage", record, "disputed", "dispute",
                          f"The two readers disagree on {', '.join(differences) or 'this statement'}: "
                          + "; ".join(f"{k}: {v[0]!r} vs {v[1]!r}" for k, v in differences.items()),
                          verdict="fail", new_state={"counterpart": other["id"]})
        else:
            assess("usage", a, "two_readers_agree", counterpart=b, differences=differences, proposed_by=both)
            assess("usage", b, "two_readers_agree", counterpart=a, differences=differences, proposed_by=both)
            record_decision(conn, actor_kind="model", actor_id=ACTOR_ID, run_id=run_id, action="audit",
                            target_table="usage", target_id=a["id"], quote=a["quote"], audit_verdict="pass",
                            reason=f"Both readers ({', '.join(both)}) recorded this statement with the same "
                                   f"speaker and no opposite stance.")
            set_state("usage", b, "superseded", "merge", f"Same statement as {a['id']} from the other reader; "
                                                        "folded into it so it is counted once.",
                      verdict="pass", new_state={"merged_into": a["id"]})
    for rows, run in ((only_first, first), (only_second, second)):
        for u in rows:
            assess("usage", u, "one_reader", proposed_by=[run["model"]])

    # relations: subject, type, object
    left, right = relations[first["id"]], relations[second["id"]]
    matched_right = set()
    for x in left:
        partner = next((y for y in right if y["id"] not in matched_right and same_name(x["subject_label"], y["subject_label"])
                        and same_name(x["object_label"], y["object_label"])), None)
        if partner is None:
            assess("relation", x, "one_reader", proposed_by=[first["model"]])
            continue
        matched_right.add(partner["id"])
        if partner["type"] == x["type"]:
            assess("relation", x, "two_readers_agree", counterpart=partner, proposed_by=both)
            assess("relation", partner, "two_readers_agree", counterpart=x, proposed_by=both)
            set_state("relation", partner, "superseded", "merge", f"Same relation as {x['id']} from the other "
                                                                  "reader; folded into it.",
                      verdict="pass", new_state={"merged_into": x["id"]})
        else:
            differences = {"type": [x["type"], partner["type"]]}
            for record, other in ((x, partner), (partner, x)):
                assess("relation", record, "readers_disagree", counterpart=other, differences=differences,
                       proposed_by=both)
                set_state("relation", record, "disputed", "dispute",
                          f"The two readers give different relation types: {x['type']} vs {partner['type']}.",
                          verdict="fail", new_state={"counterpart": other["id"]})
    for y in right:
        if y["id"] not in matched_right:
            assess("relation", y, "one_reader", proposed_by=[second["model"]])
    return counts


def reconcile(conn: sqlite3.Connection, source_ids: list[str], *, primary_model: str,
              models: list[str] | None = None) -> dict:
    run_id = f"run_{uuid.uuid4().hex}"
    totals: Counter = Counter()
    with conn:
        conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                     (run_id, canonical_json({"job": "reconcile", "rule": RULE, "primary_model": primary_model,
                                              "models": models, "sources": len(source_ids)}), utc_now()))
        for sid in source_ids:
            totals += reconcile_source(conn, sid, primary_model=primary_model, run_id=run_id, models=models)
        conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ? WHERE id = ?", (utc_now(), run_id))
    return {"run_id": run_id, "sources": len(source_ids), "counts": dict(sorted(totals.items()))}


# --------------------------------------------------------------------------- difficulties

def difficulties(conn: sqlite3.Connection, source_ids: list[str] | None = None, *, examples: int = 40) -> dict:
    """What the readers found hard: disagreements by field, single-reader records, failed or cut-off runs."""
    where, args = "", []
    if source_ids:
        where = f" AND a.source_id IN ({','.join('?' for _ in source_ids)})"
        args = list(source_ids)
    rows = conn.execute(f"""SELECT a.*, COALESCE(u.quote, r.subject_label || ' ' || r.type || ' ' || r.object_label) AS what,
                                   s.url FROM assessment a
                            LEFT JOIN usage u ON a.target_table = 'usage' AND u.id = a.target_id
                            LEFT JOIN relation r ON a.target_table = 'relation' AND r.id = a.target_id
                            JOIN source s ON s.id = a.source_id
                            WHERE a.current = 1{where}""", args).fetchall()
    confidence = Counter((r["target_table"], r["confidence"]) for r in rows)
    fields: Counter = Counter()
    by_field: dict[str, list] = defaultdict(list)
    seen_pairs = set()
    for r in rows:
        if r["confidence"] != "readers_disagree":
            continue
        pair = tuple(sorted([r["target_id"], r["counterpart_id"] or ""]))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        diffs = json.loads(r["differences"] or "{}")
        material = [f for f in ("speaker", "stance", "type") if f in diffs]
        for f in material or list(diffs):
            fields[f] += 1
            if len(by_field[f]) < examples:
                by_field[f].append({"url": r["url"], "quote": (r["what"] or "")[:160], "values": diffs.get(f)})
    one_reader = Counter()
    for r in rows:
        if r["confidence"] == "one_reader":
            one_reader[(r["target_table"], json.loads(r["proposed_by"])[0])] += 1
    sid_filter = f" AND json_extract(inputs, '$.source_id') IN ({','.join('?' for _ in source_ids)})" if source_ids else ""
    runs = conn.execute(f"""SELECT model, outcome, COUNT(*) n,
                                   SUM(COALESCE(json_extract(inputs, '$.counts.unlocated'), 0)) unlocated,
                                   SUM(COALESCE(json_extract(inputs, '$.counts.salvaged_sections'), 0)) salvaged,
                                   SUM(COALESCE(json_extract(inputs, '$.gap_calls'), 0)) gap_calls
                            FROM run WHERE role = 'reading'{sid_filter} GROUP BY model, outcome""",
                        source_ids or []).fetchall()
    corrections = conn.execute(
        "SELECT target_table, COUNT(*) n, MIN(created_at) first, MAX(created_at) last FROM decision"
        " WHERE action = 'undo' AND actor_id LIKE ? GROUP BY target_table", (ACTOR_PREFIX + "%",)).fetchall()
    return {"confidence": {f"{t}:{c}": n for (t, c), n in sorted(confidence.items())},
            "corrections": [dict(r) for r in corrections],
            "disagreements_by_field": dict(fields.most_common()),
            "examples": {k: v for k, v in by_field.items()},
            "one_reader": {f"{t}:{m}": n for (t, m), n in sorted(one_reader.items())},
            "runs": [dict(r) for r in runs]}


def difficulties_markdown(report: dict) -> str:
    conf = report["confidence"]
    statements = {k.split(":")[1]: v for k, v in conf.items() if k.startswith("usage:")}
    total = sum(statements.values()) or 1
    lines = ["# What the readers found hard", "",
             "Generated by `python -m archive reconcile --report`. Each statement and relation the models",
             "proposed has an assessment: which models read the source, which proposed the record, and",
             "whether they agreed. Disagreements are disputed and wait for review; they are not published",
             "as findings.", "", "## How sure we are (statements)", "",
             "| Confidence | Statements | Share |", "|---|---:|---:|"]
    for name in ("two_readers_agree", "one_reader", "readers_disagree", "single_reading"):
        if name in statements:
            lines.append(f"| {name.replace('_', ' ')} | {statements[name]} | {statements[name] / total:.0%} |")
    rel = {k.split(":")[1]: v for k, v in conf.items() if k.startswith("relation:")}
    if rel:
        lines += ["", "Relations: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in rel.items())]
    lines += ["", "## Where the readers disagreed", ""]
    for field, n in report["disagreements_by_field"].items():
        lines += [f"### {field} ({n})", ""]
        for ex in report["examples"].get(field, [])[:12]:
            a, b = ex["values"] or ["", ""]
            lines.append(f"- “{ex['quote']}” — {a!r} vs {b!r} ({ex['url'][:70]})")
        lines.append("")
    lines += ["## Found by one reader only", ""]
    lines += [f"- {k.replace(':', ' by ')}: {v}" for k, v in report["one_reader"].items()]
    if report.get("corrections"):
        lines += ["", "## Corrections to earlier reconciliations", "",
                  "Changes an earlier reconciliation made to records it should not have compared, undone",
                  "with a recorded 'undo' decision each:", ""]
        lines += [f"- {c['target_table']}: {c['n']} ({c['first'][:10]}–{c['last'][:10]})" for c in report["corrections"]]
    lines += ["", "## Reading runs", "", "| Model | Outcome | Runs | Unlocated quotes | Cut-off sections kept | Gap passes |",
              "|---|---|---:|---:|---:|---:|"]
    for r in report["runs"]:
        lines.append(f"| {r['model']} | {r['outcome']} | {r['n']} | {r['unlocated']} | {r['salvaged']} | {r['gap_calls']} |")
    return "\n".join(lines) + "\n"
