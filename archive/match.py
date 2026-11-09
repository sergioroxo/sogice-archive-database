"""Exact and variant matching of seed labels across all passages (no model).

Every label of a term concept and every entity name is searched in the
full-text index, then confirmed in the passage text itself. A confirmed hit
becomes a usage (found_by 'exact_match'): the exact passage slice, its
offsets, the passage language and the source date. Speaker and stance stay
'unknown' — that is Phase 3's work.

What counts as a match
* the label's words in order, any case, separated by spaces, line or page
  breaks, hyphens or dashes ('ex-gay' = 'ex gay'); an apostrophe may stand
  between word parts ("you're"); letters must be the same (no accent folding);
* acronyms keep their capitals ('SSA', not 'ssa');
* not inside a web address ('…/healing-homosexuality.html' is a link, not a use);
* not inside a longer word, and not inside a longer match: when 'SSA' sits in
  'unwanted same-sex attraction', only the longer match is kept.

Not matched (``seeds/match_rules.json``): labels that need context (ordinary
words, identity words, ambiguous names) and acronyms of three characters or
fewer unless allowed.

Repeated blocks: a passage whose text appears in three or more sources is
page furniture (menus, footers, adverts). Its usages are kept but counted
apart, and they do not raise a concept's evidence state.

Re-running replaces earlier exact matches that nobody has reviewed or linked,
so rule changes take effect; reviewed or linked usages are never deleted.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .db import canonical_json, utc_now
from .passages import sha256_text
from .seeds import SEEDS, is_acronym, normalize_label
from .urls import short_hash

FURNITURE_SOURCES = 3
GATE_CONCEPTS = 20
GATE_SOURCES = 3
_SEPARATOR = r"(?:[\s\u00ad\-\u2010-\u2015_/]+|['\u2019])"
_FOLD = {**{ord(c): "-" for c in "‐‑‒–—―−"}, **{ord(c): "'" for c in "‘’`´"}}


@dataclass(frozen=True)
class LabelSpec:
    label: str
    concept_id: str | None
    entity_id: str | None
    case_sensitive: bool

    @property
    def target(self) -> str:
        return self.concept_id or self.entity_id


def load_rules(path: Path | None = None) -> dict:
    data = json.loads((path or SEEDS / "match_rules.json").read_text(encoding="utf-8"))
    return {"allowed": set(data.get("allowed_short_acronyms", {})),
            "context": {normalize_label(k) for k in data.get("context_required", {})}}


def tokens(label: str, case_sensitive: bool) -> list[str]:
    text = unicodedata.normalize("NFKC", label).translate(_FOLD)
    if not case_sensitive:
        text = text.lower()
    return re.findall(r"\w+", text)


def label_pattern(spec: LabelSpec) -> re.Pattern:
    parts = [re.escape(t) for t in tokens(spec.label, spec.case_sensitive)]
    flags = 0 if spec.case_sensitive else re.IGNORECASE
    return re.compile(r"(?<!\w)" + _SEPARATOR.join(parts) + r"(?!\w)", flags)


def fts_query(spec: LabelSpec) -> str:
    return '"' + " ".join(tokens(spec.label, False)) + '"'


def eligible_labels(conn: sqlite3.Connection, rules: dict) -> tuple[list[LabelSpec], list[dict]]:
    rows = [(r["label"], r["concept_id"], None) for r in conn.execute("""
        SELECT l.label, l.concept_id FROM concept_label l JOIN concept c ON c.id = l.concept_id
        WHERE c.scheme = 'term' AND c.review_state NOT IN ('rejected', 'superseded')
          AND l.review_state NOT IN ('rejected', 'superseded')""")]
    # A former name that is also another entity's own name (NARTH) names that entity.
    rows += [(r["label"], None, r["entity_id"]) for r in conn.execute("""
        SELECT v.label, v.entity_id FROM entity_variant v JOIN entity e ON e.id = v.entity_id
        WHERE e.review_state NOT IN ('rejected', 'superseded')
          AND v.review_state NOT IN ('rejected', 'superseded')
          AND NOT (v.kind = 'former_name' AND EXISTS (
                SELECT 1 FROM entity_variant o WHERE o.normalized_label = v.normalized_label
                AND o.entity_id <> v.entity_id AND o.kind <> 'former_name'))""")]
    specs, excluded, seen = [], [], set()
    for label, concept_id, entity_id in rows:
        normalized = normalize_label(label)
        acronym = is_acronym(label)
        letters = sum(ch.isalnum() for ch in label)
        reason = None
        if normalized in rules["context"]:
            reason = "context_required"
        elif acronym and letters <= 3 and label.strip() not in rules["allowed"]:
            reason = "short_acronym"
        elif not acronym and len(normalized) < 4:
            reason = "too_short"
        elif not tokens(label, False):
            reason = "no_words"
        if reason:
            excluded.append({"label": label, "target": concept_id or entity_id, "reason": reason})
            continue
        spec = LabelSpec(label.strip(), concept_id, entity_id, acronym)
        key = (tuple(tokens(spec.label, acronym)), acronym, spec.target)
        if key not in seen:
            seen.add(key)
            specs.append(spec)
    return specs, excluded


def furniture_hashes(conn: sqlite3.Connection, sources: int = FURNITURE_SOURCES) -> set:
    return {row[0] for row in conn.execute("""
        SELECT p.text_sha256 FROM passage p JOIN source_version v ON v.id = p.version_id
        GROUP BY p.text_sha256 HAVING COUNT(DISTINCT v.source_id) >= ?""", (sources,))}


_URL_TOKEN = re.compile(r"://|^www\.|\.(?:html?|php|aspx?)\b", re.IGNORECASE)


def inside_url(text: str, start: int, end: int) -> bool:
    """Is the match part of a web address (a slug is not someone using the term)?"""
    left = max(text.rfind(c, 0, start) for c in " \n\t\f(<\"") + 1
    rights = [i for i in (text.find(c, end) for c in " \n\t\f)>\"") if i != -1]
    token = text[left:min(rights) if rights else len(text)]
    return bool(_URL_TOKEN.search(token))


def find_matches(conn: sqlite3.Connection, specs: list[LabelSpec]) -> dict[str, list]:
    """passage id -> [(start, end, spec)], longest-match-wins applied."""
    found: dict[str, list] = defaultdict(list)
    for spec in specs:
        pattern = label_pattern(spec)
        try:
            rows = conn.execute(
                "SELECT p.id, p.text FROM passage_fts f JOIN passage p ON p.pk = f.rowid"
                " WHERE passage_fts MATCH ?", (fts_query(spec),)).fetchall()
        except sqlite3.OperationalError:
            continue                                    # a label FTS cannot parse
        for row in rows:
            for hit in pattern.finditer(row["text"]):
                if not inside_url(row["text"], hit.start(), hit.end()):
                    found[row["id"]].append((hit.start(), hit.end(), spec))
    kept: dict[str, list] = {}
    for passage_id, hits in found.items():
        hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
        chosen: list = []
        for start, end, spec in hits:
            if any(s <= start and end <= e and (s, e) != (start, end) for s, e, _ in chosen):
                continue                                # inside a longer match
            if any((s, e) == (start, end) and other.target == spec.target for s, e, other in chosen):
                continue                                # same span, same target, other label
            chosen.append((start, end, spec))
        kept[passage_id] = sorted(chosen, key=lambda h: h[0])
    return kept


def clear_unreviewed(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("""
        DELETE FROM usage WHERE found_by = 'exact_match' AND review_state = 'proposed'
          AND evidence_state IN ('located', 'recurrent')
          AND NOT EXISTS (SELECT 1 FROM relation_usage r WHERE r.usage_id = usage.id)
          AND NOT EXISTS (SELECT 1 FROM claim_usage c WHERE c.usage_id = usage.id)
          AND NOT EXISTS (SELECT 1 FROM decision d WHERE d.target_table = 'usage' AND d.target_id = usage.id)""")
    return cursor.rowcount


def _update_evidence(conn: sqlite3.Connection, furniture: set) -> None:
    """Evidence state from usages outside page furniture: 1 source located, 2+ recurrent."""
    for table, column in (("concept", "concept_id"), ("entity", "entity_id")):
        counts = dict(conn.execute(f"""
            SELECT u.{column}, COUNT(DISTINCT v.source_id) FROM usage u
            JOIN passage p ON p.id = u.passage_id JOIN source_version v ON v.id = p.version_id
            WHERE u.{column} IS NOT NULL AND u.review_state NOT IN ('rejected', 'superseded')
              AND p.text_sha256 NOT IN (SELECT value FROM json_each(?))
            GROUP BY u.{column}""", (json.dumps(sorted(furniture)),)).fetchall())
        for row in conn.execute(f"SELECT id, evidence_state FROM {table}").fetchall():
            if row["evidence_state"] == "researcher_verified":
                continue
            n = counts.get(row["id"], 0)
            state = "recurrent" if n >= 2 else "located" if n == 1 else "unlocated"
            if state != row["evidence_state"]:
                conn.execute(f"UPDATE {table} SET evidence_state = ? WHERE id = ?", (state, row["id"]))


def run_match(conn: sqlite3.Connection, *, rules_path: Path | None = None, log=print) -> dict:
    rules = load_rules(rules_path)
    specs, excluded = eligible_labels(conn, rules)
    furniture = furniture_hashes(conn)
    run_id = f"run_{uuid.uuid4().hex}"
    now = utc_now()
    with conn:
        conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'matching', ?, ?)",
                     (run_id, canonical_json({"job": "exact_match", "labels": len(specs),
                                              "excluded": len(excluded)}), now))
    log(f"{len(specs)} labels to match, {len(excluded)} need context or are too short")
    matches = find_matches(conn, specs)
    meta = {row["id"]: row for row in conn.execute("""
        SELECT p.id, p.language, p.text_sha256, s.published_date, s.published_date_certainty
        FROM passage p JOIN source_version v ON v.id = p.version_id JOIN source s ON s.id = v.source_id""")}
    inserted = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        removed = clear_unreviewed(conn)
        for passage_id, hits in matches.items():
            info = meta[passage_id]
            text = conn.execute(
                "SELECT text FROM passage WHERE id = ?", (passage_id,)).fetchone()[0]
            for start, end, spec in hits:
                quote = text[start:end]
                usage_id = "use_" + short_hash(f"{passage_id}|{start}|{end}|{spec.target}")
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO usage(id, passage_id, char_start, char_end, quote, quote_sha256,"
                    " concept_id, entity_id, language, date, date_certainty, found_by, run_id, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?, 'exact_match', ?, ?)",
                    (usage_id, passage_id, start, end, quote, sha256_text(quote), spec.concept_id,
                     spec.entity_id, info["language"], info["published_date"],
                     info["published_date_certainty"] if info["published_date"] else None, run_id, now))
                inserted += cursor.rowcount
        _update_evidence(conn, furniture)
        conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ? WHERE id = ?", (utc_now(), run_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    report = build_match_report(conn, furniture)
    report.update(run_id=run_id, labels_matched=len(specs), usages_replaced=removed, usages_now=inserted,
                  excluded=Counter(e["reason"] for e in excluded), excluded_labels=excluded)
    return report


def build_match_report(conn: sqlite3.Connection, furniture: set | None = None) -> dict:
    furniture = furniture_hashes(conn) if furniture is None else furniture
    rows = conn.execute("""
        SELECT u.concept_id, u.entity_id, p.language, p.text_sha256, v.source_id
        FROM usage u JOIN passage p ON p.id = u.passage_id JOIN source_version v ON v.id = p.version_id
        WHERE u.found_by = 'exact_match' AND u.review_state NOT IN ('rejected', 'superseded')""").fetchall()
    body = [r for r in rows if r["text_sha256"] not in furniture]
    per_target: dict[str, dict] = defaultdict(lambda: {"usages": 0, "sources": set(), "languages": Counter(),
                                                       "en_sources": set(), "furniture": 0})
    for row in rows:
        target = row["concept_id"] or row["entity_id"]
        if row["text_sha256"] in furniture:
            per_target[target]["furniture"] += 1
            continue
        item = per_target[target]
        item["usages"] += 1
        item["sources"].add(row["source_id"])
        item["languages"][row["language"]] += 1
        if row["language"] == "en":
            item["en_sources"].add(row["source_id"])
    notations = dict(conn.execute("SELECT id, notation FROM concept").fetchall())
    labels = dict(conn.execute("SELECT id, pref_label FROM concept").fetchall())
    labels.update(dict(conn.execute("SELECT id, label FROM entity").fetchall()))

    def table(prefixes, reference=None):
        out = [{"id": k, "label": labels.get(k, k), "notation": notations.get(k),
                "sources": len(v["sources"]), "usages": v["usages"],
                "furniture_usages": v["furniture"], "languages": dict(v["languages"].most_common(4))}
               for k, v in per_target.items() if k.split(":")[0] in prefixes
               and (reference is None or (notations.get(k) == "reference") == reference)]
        return sorted(out, key=lambda r: (-r["sources"], -r["usages"]))

    # Reference terms (identity words, GOV.UK glossary) are matched but do not count for the gate.
    gate_concepts = sorted(k for k, v in per_target.items()
                           if k.startswith("term:") and notations.get(k) != "reference"
                           and len(v["en_sources"]) >= GATE_SOURCES)
    return {
        "usages": len(rows), "usages_in_furniture": len(rows) - len(body),
        "sources_with_usages": len({r["source_id"] for r in body}),
        "concepts_with_usages": sum(1 for k in per_target if k.startswith("term:")),
        "entities_with_usages": sum(1 for k in per_target if not k.startswith("term:")),
        "gate": {"english_concepts_in_3_sources": len(gate_concepts), "target": GATE_CONCEPTS,
                 "passed": len(gate_concepts) >= GATE_CONCEPTS},
        "top_concepts": table({"term"}, reference=False)[:40],
        "reference_terms": table({"term"}, reference=True)[:15],
        "top_entities": table({"person", "organization", "law", "event", "publication"})[:25],
    }


def to_markdown(report: dict) -> str:
    gate = report["gate"]
    lines = ["# Phase 2 matching report", "",
             f"**Gate:** {gate['english_concepts_in_3_sources']} English concepts (reference terms excluded) have usages from at least "
             f"3 sources (target {gate['target']}): {'passed' if gate['passed'] else 'not yet'}.", "",
             f"Usages: {report['usages']} ({report['usages_in_furniture']} in repeated page blocks, counted apart). "
             f"Concepts with usages: {report['concepts_with_usages']}; entities: {report['entities_with_usages']}.",
             ""]
    if "excluded" in report:
        lines += [f"Labels matched: {report['labels_matched']}; left for context reading: "
                  + ", ".join(f"{k} {v}" for k, v in report["excluded"].items()), ""]
    lines += ["## Change-effort vocabulary by number of sources", "",
              "| Concept | Cluster | Sources | Usages | Repeated-block | Languages |", "|---|---|---:|---:|---:|---|"]
    for row in report["top_concepts"]:
        langs = ", ".join(f"{k} {v}" for k, v in row["languages"].items())
        lines.append(f"| {row['label']} | {row['notation'] or ''} | {row['sources']} | {row['usages']} | "
                     f"{row['furniture_usages']} | {langs} |")
    lines += ["", "## Reference terms (matched, not counted for the gate)", "", "| Term | Sources | Usages |",
              "|---|---:|---:|"]
    lines += [f"| {r['label']} | {r['sources']} | {r['usages']} |" for r in report.get("reference_terms", [])]
    lines += ["", "## Entities by number of sources", "", "| Entity | Sources | Usages |", "|---|---:|---:|"]
    lines += [f"| {r['label']} | {r['sources']} | {r['usages']} |" for r in report["top_entities"]]
    return "\n".join(lines) + "\n"
