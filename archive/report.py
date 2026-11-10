"""Phase 1 report: how much of the corpus is preserved, and what failed where.

Gate (docs/PLAN.md): 90% of text sources stored with text, language and
passages. A *text source* is a source with at least one target that is not
on hold and whose kind is a page or document (webpage, pdf, docx, social,
unknown). It *passes* when one of its versions has passages and at least one
passage with a detected language (not 'und').

Target status: stored (an attempt stored a version), failed (attempts, none
stored), pending (never tried), held.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

from .preserve import target_host

TEXT_KINDS = ("webpage", "pdf", "docx", "social", "unknown")
GATE = 0.90
# Review list: big HTML pages with little extracted text. Usually the text is
# rendered by JavaScript (e.g. page builders that keep it in a script blob);
# sometimes the page really is short. Not fixed automatically.
THIN_PAGE_BYTES = 150_000
THIN_PAGE_CHARS = 800


def _target_status(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("""
        SELECT t.id, COALESCE(s.merged_into, t.source_id) AS source_id, t.kind_hint, t.hold, t.url, t.route,
               SUM(a.outcome = 'stored') AS stored, COUNT(a.id) AS attempts
        FROM source_target t JOIN source s ON s.id = t.source_id
        LEFT JOIN fetch_attempt a ON a.target_id = t.id
        GROUP BY t.id""").fetchall()
    status = {}
    for row in rows:
        if row["hold"]:
            value = "held"
        elif row["stored"]:
            value = "stored"
        elif row["attempts"]:
            value = "failed"
        else:
            value = "pending"
        status[row["id"]] = {**dict(row), "status": value}
    return status


def _last_failure(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """target id -> (first failure outcome, last failure outcome)."""
    out: dict[str, list] = defaultdict(list)
    for row in conn.execute("SELECT target_id, outcome FROM fetch_attempt WHERE outcome <> 'stored'"
                            " ORDER BY created_at"):
        out[row["target_id"]].append(row["outcome"])
    return {k: (v[0], v[-1]) for k, v in out.items()}


def build_report(conn: sqlite3.Connection) -> dict:
    targets = _target_status(conn)
    failures = _last_failure(conn)

    by_kind: dict[str, Counter] = defaultdict(Counter)
    for t in targets.values():
        by_kind[t["kind_hint"]][t["status"]] += 1

    text_sources = {t["source_id"] for t in targets.values()
                    if t["kind_hint"] in TEXT_KINDS and t["status"] != "held"}
    passing = {row[0] for row in conn.execute("""
        SELECT DISTINCT COALESCE(s.merged_into, v.source_id) FROM source_version v
        JOIN source s ON s.id = v.source_id JOIN passage p ON p.version_id = v.id
        WHERE p.language <> 'und' AND NOT EXISTS (
            SELECT 1 FROM json_each(v.quality_flags) j WHERE j.value = 'challenge_page')""")}
    gate_done = len(text_sources & passing)
    attempted_text = {t["source_id"] for t in targets.values()
                      if t["source_id"] in text_sources and t["status"] in ("stored", "failed")}

    failed = [t for t in targets.values() if t["status"] == "failed"]
    first_outcomes = Counter(failures.get(t["id"], ("?", "?"))[0] for t in failed)
    final_outcomes = Counter(failures.get(t["id"], ("?", "?"))[1] for t in failed)
    domains: dict[str, Counter] = defaultdict(Counter)
    for t in failed:
        domains[target_host(t["url"])][failures.get(t["id"], ("?", "?"))[0]] += 1
    top_domains = sorted(domains.items(), key=lambda item: -sum(item[1].values()))[:30]

    flags = Counter()
    for (value,) in conn.execute("SELECT j.value FROM source_version v, json_each(v.quality_flags) j"):
        flags[value] += 1
    routes = Counter(dict(conn.execute(
        "SELECT acquisition_route, COUNT(*) FROM source_version GROUP BY 1").fetchall()))
    languages = Counter(dict(conn.execute(
        "SELECT COALESCE(language, 'none'), COUNT(*) FROM source WHERE merged_into IS NULL AND id IN"
        " (SELECT source_id FROM source_version) GROUP BY 1").fetchall()))
    passage_count = conn.execute("SELECT COUNT(*) FROM passage").fetchone()[0]
    media_files = conn.execute("""
        SELECT COUNT(DISTINCT target_id) FROM fetch_attempt WHERE outcome = 'unsupported_type'
        AND (content_type LIKE 'video/%' OR content_type LIKE 'audio/%')""").fetchone()[0]
    youtube = Counter(dict(conn.execute("""
        SELECT json_extract(detail, '$.part') || ':' || outcome, COUNT(*) FROM fetch_attempt a
        JOIN source_target t ON t.id = a.target_id WHERE t.kind_hint = 'youtube'
        AND json_extract(detail, '$.part') IS NOT NULL GROUP BY 1""").fetchall()))

    thin = [dict(r) for r in conn.execute(f"""
        SELECT s.id AS source_id, s.url, v.byte_size, SUM(length(p.text)) AS text_chars
        FROM source_version v JOIN source s ON s.id = v.source_id JOIN passage p ON p.version_id = v.id
        WHERE v.mime_type LIKE '%html%' AND v.byte_size > {THIN_PAGE_BYTES}
        GROUP BY v.id HAVING text_chars < {THIN_PAGE_CHARS} ORDER BY s.url""")]
    unreadable = [dict(r) for r in conn.execute("""
        SELECT v.id AS version_id, s.url, j.value AS flag FROM source_version v
        JOIN source s ON s.id = v.source_id, json_each(v.quality_flags) j
        WHERE j.value IN ('missing_object', 'unreadable_object', 'corrupt_object') ORDER BY s.url""")]
    return {
        "unreadable_files": unreadable,
        "thin_pages": thin,
        "gate": {
            "text_sources": len(text_sources),
            "passing": gate_done,
            "share": round(gate_done / len(text_sources), 4) if text_sources else 0.0,
            "share_of_attempted": (round(len(attempted_text & passing) / len(attempted_text), 4)
                                   if attempted_text else 0.0),
            "attempted": len(attempted_text),
            "target": GATE,
        },
        "targets_by_kind": {k: dict(v) for k, v in sorted(by_kind.items())},
        "failed_targets": len(failed),
        "failure_first_outcome": dict(first_outcomes.most_common()),
        "failure_final_outcome": dict(final_outcomes.most_common()),
        "failures_by_domain": [{"domain": d, "failed": sum(c.values()), "outcomes": dict(c)}
                               for d, c in top_domains],
        "versions_by_route": dict(routes),
        "version_flags": dict(flags.most_common()),
        "source_languages": dict(languages.most_common()),
        "passages": passage_count,
        "youtube": dict(youtube),
        "media_files_for_transcription": media_files,
    }


def to_markdown(report: dict) -> str:
    gate = report["gate"]
    lines = ["# Phase 1 preservation report", "",
             f"**Gate:** {gate['passing']} of {gate['text_sources']} text sources have text, language "
             f"and passages ({gate['share']:.1%}; target {gate['target']:.0%}). "
             f"Of the {gate['attempted']} attempted so far: {gate['share_of_attempted']:.1%}.", "",
             "## Targets by kind", "", "| Kind | Stored | Failed | Pending | Held |", "|---|---:|---:|---:|---:|"]
    for kind, counts in report["targets_by_kind"].items():
        lines.append(f"| {kind} | {counts.get('stored', 0)} | {counts.get('failed', 0)} | "
                     f"{counts.get('pending', 0)} | {counts.get('held', 0)} |")
    lines += ["", f"## Failures ({report['failed_targets']} targets)", "",
              "First failure (live page or capture) → final outcome after any Wayback fallback:", "",
              "| First outcome | Targets |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in report["failure_first_outcome"].items()]
    lines += ["", "| Final outcome | Targets |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in report["failure_final_outcome"].items()]
    lines += ["", "## Failures by domain (top 30)", "", "| Domain | Failed | Outcomes |", "|---|---:|---|"]
    for row in report["failures_by_domain"]:
        outcomes = ", ".join(f"{k} {v}" for k, v in sorted(row["outcomes"].items(), key=lambda x: -x[1]))
        lines.append(f"| {row['domain']} | {row['failed']} | {outcomes} |")
    lines += ["", "## Stored versions", "",
              "By route: " + ", ".join(f"{k} {v}" for k, v in report["versions_by_route"].items()),
              "", "Flags: " + (", ".join(f"{k} {v}" for k, v in report["version_flags"].items()) or "none"),
              "", "Source languages: " + ", ".join(f"{k} {v}" for k, v in report["source_languages"].items()),
              "", f"Passages: {report['passages']}",
              "", f"Audio/video files found (queued for transcription later): "
                  f"{report['media_files_for_transcription']}"]
    if report.get("unreadable_files"):
        lines += ["", f"## Stored files removed or unreadable ({len(report['unreadable_files'])}, review list)", "",
                  "Usually quarantined by antivirus (Wayback pages with injected scripts); not fetched again "
                  "automatically.", ""]
        lines += [f"- {row['url']} ({row['flag']})" for row in report["unreadable_files"]]
    if report.get("thin_pages"):
        lines += ["", f"## Large pages with little text ({len(report['thin_pages'])}, review list)", "",
                  "Over 150 KB of HTML but under 800 characters extracted: usually text rendered by JavaScript.", ""]
        lines += [f"- {row['url']} ({row['text_chars']} chars)" for row in report["thin_pages"]]
    if report["youtube"]:
        lines += ["", "YouTube: " + ", ".join(f"{k} {v}" for k, v in sorted(report["youtube"].items()))]
    return "\n".join(lines) + "\n"


def report_json(report: dict) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)
