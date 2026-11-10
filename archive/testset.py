"""The Phase 3 test set: 20 sources with expected reading behaviour.

The test set file (``evaluation/testset_*.json``, public) lists each entry's
URLs, optional files from the Transfer package, and expectations. It holds
short anchor phrases only, never source texts.

``prepare`` queues the URLs, attaches package files (checked by SHA-256) and
fetches and indexes what is missing. ``score`` checks the expectations against
the database and computes the Phase 3 gate. Each expectation ends as:

- ``pass`` / ``fail``  decided by software from stored usages, claims, relations;
- ``pending``          the source has no reading run yet;
- ``judge``            a criterion software cannot decide; a Claude agent reads
                       the results and writes a verdict to a judgements file;
- ``error``            the test set itself is wrong (anchor not in the text,
                       source not indexed): fix the test set, not the model.

Conventions the reading stage must keep so it can be scored: a reading or
audit run stores ``source_id`` in ``run.inputs``; a quote the software could
not locate is kept as a usage with ``evidence_state = 'unlocated'``.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .match import furniture_hashes
from .queue_import import file_sha256
from .urls import source_id_for

PACKAGE_ROOT = (Path.home() / "surviving-sogice-ingest" / "Transfer"
                / "SurvivingSOGICE_Complete_Package")

CHECKS = {
    "usage":             {"anchor"},
    "same_speaker":      {"anchors"},
    "no_relation":       {"pages", "types"},
    "claims_attributed": {"pages"},
    "separate":          {"page_groups"},
    "furniture":         set(),
    "no_model_usages":   set(),
    "language":          {"code"},
    "pages":             set(),
    "timecodes":         set(),
    "same_text":         set(),
    "judged":            {"criterion"},
}
WHERE_KEYS = {"speaker_any", "speaker_not", "quoted_party_any", "intermediary_any",
              "stance_any", "stance_not", "attribution_any", "concept_any", "entity_any", "date_any"}
UNFILLED_SPEAKERS = {"", "unknown", "none", "n/a"}
DEFAULT_WINDOW = 200


class TestsetError(ValueError):
    pass


# --------------------------------------------------------------------------- loading

def load_testset(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(data)
    return data


def validate(data: dict) -> None:
    problems = []
    keys, ids = set(), set()
    for entry in data.get("entries", []):
        key = entry.get("key")
        if not key or key in keys:
            problems.append(f"entry key missing or repeated: {key!r}")
        keys.add(key)
        if not entry.get("urls"):
            problems.append(f"{key}: no urls")
        if entry.get("split") not in ("dev", "holdout"):
            problems.append(f"{key}: split must be dev or holdout")
        for item in entry.get("package_files", []):
            if item.get("use") not in ("attach", "reference") or not item.get("path") or not item.get("sha256"):
                problems.append(f"{key}: package file needs path, sha256 and use attach|reference")
        for exp in entry.get("expect", []):
            eid, check = exp.get("id"), exp.get("check")
            if not eid or eid in ids:
                problems.append(f"{key}: expectation id missing or repeated: {eid!r}")
            ids.add(eid)
            if check not in CHECKS:
                problems.append(f"{eid}: unknown check {check!r}")
                continue
            missing = CHECKS[check] - set(exp)
            if missing:
                problems.append(f"{eid}: {check} needs {sorted(missing)}")
            where = exp.get("where", {})
            for option in (where if isinstance(where, list) else [where]):
                unknown = set(option) - WHERE_KEYS
                if unknown:
                    problems.append(f"{eid}: unknown where keys {sorted(unknown)}")
            if exp.get("must", "some") not in ("some", "none"):
                problems.append(f"{eid}: must is some|none")
    if problems:
        raise TestsetError("; ".join(problems))


def entry_sources(entry: dict) -> list[str]:
    from .urls import parse_wayback
    out = []
    for url in entry["urls"]:
        ref = parse_wayback(url)
        sid = source_id_for(ref.original_url if ref else url)
        if sid not in out:
            out.append(sid)
    return out


# --------------------------------------------------------------------------- text helpers

def norm(text: str | None) -> str:
    """Case-, accent-, quote- and space-insensitive form for comparing names and anchors."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    """norm() of text, plus the original offset of each normalised character."""
    out, where = [], []
    space = False
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(c):
                continue
            c = {"’": "'", "‘": "'", "“": '"', "”": '"'}.get(c, c)
            if c.isspace():
                if space or not out:
                    continue
                c, space = " ", True
            else:
                space = False
            for lower in c.casefold():
                out.append(lower)
                where.append(i)
    return "".join(out), where


def _fits(value: str | None, options: list[str]) -> bool:
    value = norm(value)
    return bool(value) and any(norm(o) in value for o in options)


# --------------------------------------------------------------------------- database view

@dataclass
class Located:
    passage_id: str
    start: int            # anchor span inside the passage
    end: int
    page: int | None


@dataclass
class EntryView:
    """What the database holds for one test-set entry."""
    sources: list[str]
    passages: list[sqlite3.Row] = field(default_factory=list)
    usages: list[sqlite3.Row] = field(default_factory=list)       # model usages
    runs: list[sqlite3.Row] = field(default_factory=list)         # reading/audit runs
    claims: list[dict] = field(default_factory=list)              # with usage pages
    disputed: int = 0                                             # statements the readers disagreed on
    relations: list[dict] = field(default_factory=list)

    @property
    def read(self) -> bool:
        return any(r["role"] == "reading" and r["outcome"] == "succeeded" for r in self.runs)

    def find(self, anchor: str) -> list[Located]:
        target = norm(anchor)
        found = []
        for p in self.passages:
            text, where = _norm_with_map(p["text"])
            for m in re.finditer(re.escape(target), text):
                start = where[m.start()]
                page = p["page_start"] + p["text"].count("\f", 0, start) if p["page_start"] is not None else None
                found.append(Located(p["id"], start, where[m.end() - 1] + 1, page))
        return found

    def page_of(self, usage: sqlite3.Row) -> int | None:
        """The page a located usage starts on (page breaks are form feeds in the passage)."""
        if usage["page_start"] is None or usage["char_start"] is None:
            return None
        text = next((p["text"] for p in self.passages if p["id"] == usage["passage_id"]), "")
        return usage["page_start"] + text.count("\f", 0, usage["char_start"])

    def near(self, located: Located, window: int) -> list[sqlite3.Row]:
        return [u for u in self.usages if u["passage_id"] == located.passage_id
                and u["char_start"] is not None
                and u["char_start"] < located.end + window and u["char_end"] > located.start - window]


def _with_merged(conn: sqlite3.Connection, sources: list[str]) -> list[str]:
    """The entry's sources plus every copy merged with them (same work)."""
    from .dedupe import keeper_of
    merged = keeper_of(conn)
    keepers = {merged.get(s, s) for s in sources}
    out = list(sources)
    for sid in sorted(keepers | {d for d, k in merged.items() if k in keepers}):
        if sid not in out:
            out.append(sid)
    return out


def entry_view(conn: sqlite3.Connection, entry: dict) -> EntryView:
    conn.row_factory = sqlite3.Row
    sources = _with_merged(conn, entry_sources(entry))
    view = EntryView(sources)
    marks = ",".join("?" for _ in sources)
    view.passages = conn.execute(
        f"""SELECT p.*, v.source_id, v.mime_type FROM passage p JOIN source_version v ON v.id = p.version_id
            WHERE v.source_id IN ({marks}) ORDER BY v.source_id, p.version_id, p.sequence""",
        sources).fetchall()
    view.usages = conn.execute(
        f"""SELECT u.*, p.page_start, p.page_end, p.text_sha256 AS passage_sha, v.source_id
            FROM usage u JOIN passage p ON p.id = u.passage_id JOIN source_version v ON v.id = p.version_id
            WHERE v.source_id IN ({marks}) AND u.found_by = 'model'
            AND u.review_state NOT IN ('superseded', 'rejected', 'disputed')""", sources).fetchall()
    view.usages += conn.execute(  # unlocated quotes have no passage; the run names the source
        f"""SELECT u.*, NULL AS page_start, NULL AS page_end, NULL AS passage_sha,
                   json_extract(r.inputs, '$.source_id') AS source_id
            FROM usage u JOIN run r ON r.id = u.run_id
            WHERE u.passage_id IS NULL AND json_extract(r.inputs, '$.source_id') IN ({marks})
            AND u.review_state NOT IN ('superseded', 'rejected', 'disputed')""",
        sources).fetchall()
    # The current reading of each source by each model: its latest succeeded run (a model's earlier
    # runs are superseded; another model's reading is a second layer, not an earlier version).
    view.runs = conn.execute(
        f"""SELECT * FROM run r WHERE role IN ('reading', 'audit') AND outcome = 'succeeded'
            AND json_extract(inputs, '$.source_id') IN ({marks})
            AND NOT EXISTS (SELECT 1 FROM run later WHERE later.role = r.role AND later.outcome = 'succeeded'
                            AND later.model IS r.model
                            AND json_extract(later.inputs, '$.source_id') = json_extract(r.inputs, '$.source_id')
                            AND later.started_at > r.started_at)""", sources).fetchall()
    # Disputed and disputed-out records are not findings: they wait for review.
    view.disputed = conn.execute(
        f"""SELECT COUNT(*) FROM usage u JOIN passage p ON p.id = u.passage_id JOIN source_version v ON v.id = p.version_id
            WHERE v.source_id IN ({marks}) AND u.found_by = 'model' AND u.review_state = 'disputed'""",
        sources).fetchone()[0]
    usage_ids = [u["id"] for u in view.usages]
    pages = {u["id"]: view.page_of(u) for u in view.usages}
    if usage_ids:
        umarks = ",".join("?" for _ in usage_ids)
        grouped = defaultdict(list)
        for row in conn.execute(f"SELECT claim_id, usage_id FROM claim_usage WHERE usage_id IN ({umarks})",
                                usage_ids):
            grouped[row["claim_id"]].append(row["usage_id"])
        for claim_id, uids in grouped.items():
            claim = dict(conn.execute("SELECT * FROM claim WHERE id = ?", (claim_id,)).fetchone())
            if claim["review_state"] in ("superseded", "rejected", "disputed"):
                continue
            claim["pages"] = sorted({pages[u] for u in uids if pages[u] is not None})
            view.claims.append(claim)
        grouped = defaultdict(list)
        for row in conn.execute(f"SELECT relation_id, usage_id FROM relation_usage WHERE usage_id IN ({umarks})",
                                usage_ids):
            grouped[row["relation_id"]].append(row["usage_id"])
        for relation_id, uids in grouped.items():
            relation = dict(conn.execute("SELECT * FROM relation WHERE id = ?", (relation_id,)).fetchone())
            if relation["review_state"] in ("superseded", "rejected", "disputed"):
                continue
            relation["pages"] = sorted({pages[u] for u in uids if pages[u] is not None})
            view.relations.append(relation)
    return view


# --------------------------------------------------------------------------- checks

def _concept_names(conn: sqlite3.Connection, concept_id: str | None) -> list[str]:
    if not concept_id:
        return []
    return [concept_id] + [r[0] for r in conn.execute(
        "SELECT label FROM concept_label WHERE concept_id = ?", (concept_id,))]


def _usage_fits(conn: sqlite3.Connection, usage: sqlite3.Row, where: dict | list) -> bool:
    """``where`` is one set of conditions, or a list of alternative sets (any may fit)."""
    if isinstance(where, list):
        return any(_usage_fits(conn, usage, option) for option in where)
    if "speaker_any" in where and not _fits(usage["speaker"], where["speaker_any"]):
        return False
    if "speaker_not" in where and _fits(usage["speaker"], where["speaker_not"]):
        return False
    if "quoted_party_any" in where and not _fits(usage["quoted_party"], where["quoted_party_any"]):
        return False
    if "intermediary_any" in where and not _fits(usage["intermediary"], where["intermediary_any"]):
        return False
    if "stance_any" in where and usage["stance"] not in where["stance_any"]:
        return False
    if "stance_not" in where and usage["stance"] in where["stance_not"]:
        return False
    if "attribution_any" in where and usage["attribution_mode"] not in where["attribution_any"]:
        return False
    if "date_any" in where and not any((usage["date"] or "").startswith(d) for d in where["date_any"]):
        return False
    if "concept_any" in where:
        names = {norm(n) for n in _concept_names(conn, usage["concept_id"])}
        if not names & {norm(c) for c in where["concept_any"]}:
            return False
    if "entity_any" in where:
        label = conn.execute("SELECT label FROM entity WHERE id = ?", (usage["entity_id"],)).fetchone()
        if not _fits(label[0] if label else None, where["entity_any"]):
            return False
    return True


def _in_pages(pages: list, span: list) -> bool:
    return any(span[0] <= p <= span[-1] for p in pages)


def check(conn: sqlite3.Connection, view: EntryView, exp: dict, judgements: dict) -> tuple[str, str]:
    kind = exp["check"]
    if not view.passages and kind not in ("judged",):
        return "error", "source not indexed (no passages)"

    # Checks on the preserved text: decidable before any reading.
    if kind == "language":
        chars = Counter()
        for p in view.passages:
            chars[p["language"] or "und"] += len(p["text"])
        share = chars[exp["code"]] / max(1, sum(chars.values()))
        ok = share >= exp.get("share", 0.8)
        return ("pass" if ok else "fail"), f"{exp['code']} share {share:.2f}; top {chars.most_common(3)}"
    if kind == "pages":
        missing = sum(1 for p in view.passages if p["page_start"] is None)
        return ("pass" if not missing else "fail"), f"{missing} of {len(view.passages)} passages without a page"
    if kind == "timecodes":  # caption text only; a video's description has no times
        captions = [p for p in view.passages if p["mime_type"] == "text/vtt"]
        missing = sum(1 for p in captions if p["time_start"] is None)
        ok = captions and not missing
        return ("pass" if ok else "fail"), f"{missing} of {len(captions)} caption passages without a time"
    if kind == "usage":  # the anchor must exist even before reading
        located = view.find(exp["anchor"])
        if not located:
            return "error", f"anchor not found in text: {exp['anchor']!r}"
        if "page" in exp and not any(l.page == exp["page"] for l in located):
            return "error", f"anchor found on pages {sorted({l.page for l in located})}, not {exp['page']}"
    if kind == "same_speaker":
        for anchor in exp["anchors"]:
            if not view.find(anchor):
                return "error", f"anchor not found in text: {anchor!r}"

    if kind == "judged":
        verdict = judgements.get(exp["id"])
        if not verdict:
            return ("judge" if view.read else "pending"), exp["criterion"]
        return verdict["verdict"], verdict.get("reason", "")

    if kind == "same_text":
        if len(view.sources) < 2:
            return "error", "same_text needs two sources"
        texts = defaultdict(set)
        for p in view.passages:
            texts[p["source_id"]].add(p["text_sha256"])
        shared = set.intersection(*(texts[s] for s in view.sources)) if all(texts[s] for s in view.sources) else set()
        merged = conn.execute(
            f"SELECT 1 FROM source WHERE merged_into IS NOT NULL AND id IN "
            f"({','.join('?' for _ in view.sources)})", view.sources).fetchone() if any(
            r[1] == "merged_into" for r in conn.execute("PRAGMA table_info(source)")) else None
        if not shared and not merged:
            return "fail", "the copies share no passage text and no merge is recorded"
        if not view.read:
            return "pending", f"{len(shared)} passages shared; reading not run"
        read_sources = {u["source_id"] for u in view.usages}
        ok = len(read_sources) <= 1
        return ("pass" if ok else "fail"), f"model usages on {len(read_sources)} copies (expected at most 1)"

    if not view.read:
        return "pending", "no reading run for this source"

    if kind == "usage":
        window = exp.get("window", DEFAULT_WINDOW)
        hits = [u for loc in view.find(exp["anchor"]) for u in view.near(loc, window)]
        fitting = [u for u in hits if _usage_fits(conn, u, exp.get("where", {}))]
        if exp.get("must", "some") == "none":
            return ("pass" if not fitting else "fail"), f"{len(fitting)} forbidden usage(s) near anchor"
        if fitting:
            return "pass", f"{len(fitting)} of {len(hits)} usages near anchor fit"
        seen = sorted({(u['speaker'], u['stance'], u['attribution_mode']) for u in hits}, key=str)[:4]
        return "fail", f"{len(hits)} usages near anchor, none fit; seen {seen}"
    if kind == "same_speaker":
        speakers = []
        for anchor in exp["anchors"]:
            near = {norm(u["speaker"]) for loc in view.find(anchor) for u in view.near(loc, exp.get("window", 0))}
            near -= UNFILLED_SPEAKERS
            if not near:
                return "fail", f"no attributed usage at {anchor!r}"
            speakers.append(near)
        ok = bool(set.intersection(*speakers))
        return ("pass" if ok else "fail"), f"speakers {[sorted(s) for s in speakers]}"
    if kind == "no_relation":
        bad = [r for r in view.relations if r["layer"] == "asserted" and r["type"] in exp["types"]
               and _in_pages(r["pages"], exp["pages"])]
        return ("pass" if not bad else "fail"), (
            f"{len(bad)} forbidden relation(s): " + "; ".join(
                f"{r['subject_label']} {r['type']} {r['object_label']}" for r in bad[:3]))
    if kind == "claims_attributed":
        claims = [c for c in view.claims if _in_pages(c["pages"], exp["pages"])]
        if len(claims) < exp.get("min", 1):
            return "fail", f"{len(claims)} claims on pages {exp['pages']}"
        forbidden = set(exp.get("factual_status_not", ["corroborated", "refuted"]))
        bad = [c for c in claims if norm(c["claimant_label"]) in UNFILLED_SPEAKERS
               or c["factual_status"] in forbidden]
        return ("pass" if not bad else "fail"), f"{len(bad)} of {len(claims)} claims unattributed or ruled on"
    if kind == "separate":
        groups = exp["page_groups"]
        mixed = [x for x in view.claims + view.relations
                 if sum(1 for g in groups if _in_pages(x["pages"], g)) > 1]
        return ("pass" if not mixed else "fail"), f"{len(mixed)} claim(s)/relation(s) mix the groups"
    if kind == "furniture":
        furniture = furniture_hashes(conn)
        bad = [u for u in view.usages if u["passage_sha"] in furniture and u["evidence_state"] == "located"]
        return ("pass" if not bad else "fail"), f"{len(bad)} usages in repeated page blocks counted as body"
    if kind == "no_model_usages":  # optionally only from versions with a quality flag
        located = [u for u in view.usages if u["evidence_state"] != "unlocated"]
        if exp.get("flag"):
            flagged = {r[0] for r in conn.execute(
                "SELECT p.id FROM passage p JOIN source_version v ON v.id = p.version_id, json_each(v.quality_flags) j"
                f" WHERE j.value = ? AND v.source_id IN ({','.join('?' for _ in view.sources)})",
                [exp["flag"], *view.sources])}
            located = [u for u in located if u["passage_id"] in flagged]
        return ("pass" if not located else "fail"), f"{len(located)} model usages"
    raise TestsetError(f"unhandled check {kind}")


# --------------------------------------------------------------------------- scoring

def score(conn: sqlite3.Connection, data: dict, judgements: dict | None = None,
          split: str | None = None) -> dict:
    judgements = judgements or {}
    gate = data.get("gate", {})
    results, all_usages, seconds, chars, all_disputed = [], [], {}, {}, 0
    for entry in data["entries"]:
        if split and entry["split"] != split:
            continue
        view = entry_view(conn, entry)
        all_usages += view.usages
        all_disputed += view.disputed
        for model in {r["model"] for r in view.runs if r["role"] == "reading"}:
            runs = [r for r in view.runs if r["role"] == "reading" and r["model"] == model]
            key = (entry["key"], model)
            seconds[key] = sum(r["seconds"] or 0 for r in runs)
            read_chars = [json.loads(r["inputs"]).get("chars") for r in runs]
            chars[key] = (sum(read_chars) if None not in read_chars
                          else sum(len(p["text"]) for p in view.passages))
        for exp in entry.get("expect", []):
            status, detail = check(conn, view, exp, judgements)
            results.append({"entry": entry["key"], "split": entry["split"], "id": exp["id"],
                            "case": exp.get("case", ""), "check": exp["check"],
                            "status": status, "detail": detail})
    located = [u for u in all_usages if u["evidence_state"] != "unlocated"]
    statements = [u for u in located if u["entity_id"] is None]
    # Time scales with length: the gate is seconds per 1,000 characters read (a fixed limit per
    # source is kept for old test-set files that still give one).
    rates = {k: round(1000 * seconds[k] / chars[k], 2) for k in seconds if chars.get(k) and seconds[k]}
    if "seconds_per_1000_chars" in gate:  # per reader: each model's reading is budgeted on its own
        over_time = sorted({k[0] for k, r in rates.items() if r > gate["seconds_per_1000_chars"]})
    else:
        per_entry = Counter()
        for (entry_key, _model), s in seconds.items():
            per_entry[entry_key] += s
        over_time = [k for k, s in per_entry.items() if s > gate.get("seconds_per_source", 300)]

    def share(rows, test):
        return round(sum(1 for r in rows if test(r)) / len(rows), 3) if rows else None

    metrics = {
        "model_usages": len(all_usages),
        "quotes_located": share(all_usages, lambda u: u["evidence_state"] != "unlocated"),
        # Speaker and stance are measured on statements; an entity mention (a name) has no voice of its own.
        "speaker_filled": share(statements, lambda u: norm(u["speaker"]) not in UNFILLED_SPEAKERS),
        "stance_filled": share(statements, lambda u: u["stance"] != "unknown"),
        "entity_mentions": len(located) - len(statements),
        "disputed_statements": all_disputed,
        "confidence": _confidence(conn, [u["id"] for u in statements]),
        "max_seconds_per_source": max(seconds.values()) if seconds else None,
        "max_seconds_per_1000_chars": max(rates.values()) if rates else None,
        "sources_over_time": sorted(over_time),
    }
    counts = Counter(r["status"] for r in results)
    checks = []
    for name in ("quotes_located", "speaker_filled", "stance_filled"):
        if metrics[name] is not None and name in gate:
            checks.append(metrics[name] >= gate[name])
    gate_passed = (bool(checks) and all(checks) and not metrics["sources_over_time"]
                   and counts["fail"] == counts["error"] == counts["pending"] == counts["judge"] == 0)
    return {"testset": data.get("version"), "split": split or "all", "metrics": metrics,
            "counts": dict(sorted(counts.items())), "gate": gate, "gate_passed": gate_passed,
            "results": results}


def _confidence(conn: sqlite3.Connection, usage_ids: list[str]) -> dict:
    """Share of the counted statements by reconciliation outcome (none if never reconciled)."""
    if not usage_ids or not conn.execute("SELECT name FROM sqlite_master WHERE name = 'assessment'").fetchone():
        return {}
    found = Counter()
    for i in range(0, len(usage_ids), 500):
        chunk = usage_ids[i:i + 500]
        for row in conn.execute(f"SELECT confidence FROM assessment WHERE current = 1 AND target_table = 'usage'"
                                f" AND target_id IN ({','.join('?' for _ in chunk)})", chunk):
            found[row[0]] += 1
    found["not_reconciled"] = len(usage_ids) - sum(found.values())
    return {k: round(v / len(usage_ids), 3) for k, v in sorted(found.items()) if v}


def to_markdown(report: dict) -> str:
    m = report["metrics"]
    lines = [f"# Test set {report['testset']} ({report['split']})", "",
             f"Gate passed: **{'yes' if report['gate_passed'] else 'no'}**", "",
             f"- Model usages: {m['model_usages']}",
             f"- Quotes located: {m['quotes_located']} (gate {report['gate'].get('quotes_located')})",
             f"- Speaker filled: {m['speaker_filled']} (gate {report['gate'].get('speaker_filled')})",
             f"- Stance filled: {m['stance_filled']} (gate {report['gate'].get('stance_filled')})",
             f"- Slowest source: {m['max_seconds_per_source']} s; slowest per 1,000 characters: "
             f"{m.get('max_seconds_per_1000_chars')} s (gate {report['gate'].get('seconds_per_1000_chars')});"
             f" over limit: {m['sources_over_time'] or 'none'}",
             f"- Disputed statements (readers disagree, waiting for review): {m.get('disputed_statements', 0)}",
             f"- Confidence of counted statements: {m.get('confidence') or 'not reconciled'}",
             f"- Expectations: {report['counts']}", "",
             "| Entry | Expectation | Case | Status | Detail |", "|---|---|---|---|---|"]
    for r in report["results"]:
        detail = r["detail"].replace("|", "/").replace("\n", " ")[:160]
        lines.append(f"| {r['entry']} | {r['id']} | {r['case']} | {r['status']} | {detail} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- preparing

def prepare(conn: sqlite3.Connection, depot, data: dict, *, package_root: Path = PACKAGE_ROOT,
            origin: str | None = None) -> dict:
    """Queue the entries' URLs and attach package files. Fetching is the caller's step."""
    from .attach import FileMatch, attach
    from .queue_import import add_urls
    from .db import canonical_json, utc_now
    import uuid

    urls = [u for e in data["entries"] for u in e["urls"]]
    added = add_urls(conn, urls, origin=origin or f"testset-{data.get('version')}")
    run_id = f"run_{uuid.uuid4().hex}"
    attached, problems = [], []
    files = [(e, f) for e in data["entries"] for f in e.get("package_files", [])]
    if any(f["use"] == "attach" for _, f in files):
        with conn:
            conn.execute("INSERT INTO run(id, role, inputs, outcome, started_at, finished_at)"
                         " VALUES (?, 'other', ?, 'succeeded', ?, ?)",
                         (run_id, canonical_json({"job": "testset_package_files"}), utc_now(), utc_now()))
    for entry, item in files:
        path = Path(package_root) / item["path"]
        if not path.is_file():
            problems.append(f"{entry['key']}: missing {path}")
            continue
        if file_sha256(path) != item["sha256"]:
            problems.append(f"{entry['key']}: checksum differs for {item['path']}")
            continue
        if item["use"] != "attach":
            continue
        target = added["targets"][entry["urls"][0]]
        match = FileMatch(path, "matched", "testset_package", target["source_id"], target["target_id"],
                          note=f"Transfer package: {item['path']}")
        attached.append({"entry": entry["key"], "file": item["path"],
                         "status": attach(conn, depot, match, run_id).status})
    return {"new_sources": added["new_sources"], "new_targets": added["new_targets"],
            "targets": added["targets"], "attached": attached, "problems": problems}


def unindexed_versions(conn: sqlite3.Connection, source_ids: list[str]) -> list[sqlite3.Row]:
    marks = ",".join("?" for _ in source_ids)
    return conn.execute(
        f"""SELECT v.*, s.url AS source_url FROM source_version v JOIN source s ON s.id = v.source_id
            WHERE v.text_sha256 IS NULL AND v.source_id IN ({marks})""", source_ids).fetchall()


def show(conn: sqlite3.Connection, entry: dict, *, grep: str | None = None, width: int | None = None) -> str:
    """The entry's passages with their locators, for writing and checking expectations."""
    view = entry_view(conn, entry)
    out = []
    for p in view.passages:
        if grep and norm(grep) not in norm(p["text"]):
            continue
        where = (f"p.{p['page_start']}" + (f"-{p['page_end']}" if p["page_end"] != p["page_start"] else "")
                 if p["page_start"] is not None else
                 f"t={p['time_start']:.0f}s" if p["time_start"] is not None else "")
        text = p["text"] if not width else p["text"][:width]
        out.append(f"--- {p['id']} {p['source_id']} {where} [{p['language']}]\n{text}")
    return "\n".join(out)
