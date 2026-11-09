"""Import the old source queue into ``source`` and ``source_target``.

The old ``state/source_queue.db`` is opened read-only (``mode=ro``) and never
changed. Each old row becomes a fetch target; targets whose URLs name the same
page (see ``archive.urls.identity_key``) share one source:

* a live URL      -> target route ``live_fetch``
* a Wayback URL   -> target route ``wayback``; the source gets the original URL,
                     and the capture timestamp is kept on the target
* a Wayback ``sitemap`` link -> a live target for the site it names

Several Wayback captures of one page from different years stay separate
targets of one source; each becomes its own source_version when fetched.
Rows with exactly the same route and URL become one target that keeps every
old id. Old hints (type, priority, review flags, notes...) are kept per old
row in ``legacy_hints``; old model-routing fields are dropped.

Re-running is safe: existing sources and targets are left as they are.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

from .db import canonical_json, utc_now
from .urls import (UrlError, check_http_url, host_of, parse_wayback, short_hash,
                   source_id_for)

KIND_MAP = {"webpage": "webpage", "pdf": "pdf", "docx": "docx", "video": "video",
            "youtube": "youtube", "audio": "audio", "social": "social"}
HINT_TEXT_COLUMNS = (
    "source_type", "doc_type_hint", "priority", "status", "suggested_process_route",
    "notes", "tags", "batch_group", "corpus_doc_id", "title",
    "source_file_path", "source_file_relation", "source_file_url", "source_file_note",
)
HINT_FLAG_COLUMNS = (
    "needs_book_splitting", "needs_testimony_review", "needs_media_review",
    "needs_legal_review", "needs_source_file",
)
YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com"}
VIDEO_HOSTS = {"vimeo.com", "player.vimeo.com", "rumble.com", "dailymotion.com"}


def open_readonly(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"old queue not found: {path}")
    conn = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def read_queue(path: str | Path) -> list[dict]:
    with open_readonly(path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM source_queue ORDER BY id")]


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def kind_hint(source_type: str, original_url: str) -> str:
    """Old source_type, corrected by what the URL plainly is."""
    parts = urlsplit(original_url)
    names = [parts.path.lower()] + [v.lower() for _, v in parse_qsl(parts.query)]  # ?filename=x.pdf
    host = host_of(original_url)
    if any(name.endswith(".pdf") for name in names):
        return "pdf"
    if any(name.endswith((".docx", ".doc")) for name in names):
        return "docx"
    if host in YOUTUBE_HOSTS:
        return "youtube"
    if host in VIDEO_HOSTS:
        return "video"
    return KIND_MAP.get((source_type or "").strip().lower(), "unknown")


def legacy_hints(row: dict) -> dict:
    hints = {}
    for column in HINT_TEXT_COLUMNS:
        value = row.get(column)
        if isinstance(value, str) and value.strip():
            hints[column] = value.strip()
    flags = sorted(c for c in HINT_FLAG_COLUMNS if row.get(c))
    if flags:
        hints["flags"] = flags
    return hints


@dataclass
class ImportPlan:
    sources: dict = field(default_factory=dict)   # id -> row for table source
    targets: dict = field(default_factory=dict)   # id -> row for table source_target
    rejected: list = field(default_factory=list)  # {legacy_id, url, error}
    rows: int = 0


def plan_import(rows: list[dict], *, now: str | None = None) -> ImportPlan:
    """Pure step: old rows -> source and target rows (nothing written)."""
    now = now or utc_now()
    plan = ImportPlan(rows=len(rows))
    members: dict[str, list[dict]] = {}  # source id -> its targets, in order

    for row in sorted(rows, key=lambda r: str(r["id"])):
        legacy_id = str(row["id"])
        raw_url = (row.get("url") or "").strip()
        hints = legacy_hints(row)
        try:
            wayback = parse_wayback(raw_url)
            if wayback and wayback.kind == "capture":
                original, route, target_url, stamp = (
                    wayback.original_url, "wayback", raw_url, wayback.timestamp)
            elif wayback:  # sitemap link: fetch the site itself
                original, route, target_url, stamp = (
                    wayback.original_url, "live_fetch", wayback.original_url, None)
                hints["from_wayback_link"] = raw_url
            else:
                original = check_http_url(raw_url)
                route, target_url, stamp = "live_fetch", original, None
            source_id = source_id_for(original)
        except UrlError as exc:
            plan.rejected.append({"legacy_id": legacy_id, "url": raw_url, "error": str(exc)})
            continue

        target_id = "tgt_" + short_hash(f"{route}|{target_url}")
        hold = "legacy_priority_skip" if hints.get("priority") == "skip" else None
        target = plan.targets.get(target_id)
        if target is None:
            target = {
                "id": target_id, "source_id": source_id, "route": route, "url": target_url,
                "capture_timestamp": stamp, "kind_hint": kind_hint(row.get("source_type", ""), original),
                "hold": hold, "legacy_refs": [], "legacy_hints": {}, "created_at": now,
                "_original": original, "_title": hints.get("title"),
            }
            plan.targets[target_id] = target
            members.setdefault(source_id, []).append(target)
        else:
            if target["kind_hint"] == "unknown":
                target["kind_hint"] = kind_hint(row.get("source_type", ""), original)
            if hold is None:  # held only if every merged row said skip
                target["hold"] = None
        target["legacy_refs"].append(legacy_id)
        target["legacy_hints"][legacy_id] = hints

    for source_id, targets in members.items():
        live = [t for t in targets if t["route"] == "live_fetch"]
        captures = sorted((t for t in targets if t["route"] == "wayback"),
                          key=lambda t: (t["capture_timestamp"] or "").ljust(14, "0"))
        first = captures[0] if captures else None
        ref = parse_wayback(first["url"]) if first else None
        captured = ref.captured_date if ref else None
        plan.sources[source_id] = {
            "id": source_id,
            "url": (live[0] if live else first or targets[0])["_original"],
            "title": next((t["_title"] for t in targets if t["_title"]), None),
            "archive_url": first["url"] if first else None,
            "captured_date": captured,
            "captured_date_certainty": ref.captured_certainty if captured else None,
            "legacy_ref": min(ref_id for t in targets for ref_id in t["legacy_refs"]),
            "created_at": now,
        }
    return plan


def _insert(conn: sqlite3.Connection, table: str, row: dict) -> bool:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    cursor = conn.execute(f"INSERT OR IGNORE INTO {table}({columns}) VALUES ({marks})",
                          tuple(row.values()))
    return cursor.rowcount == 1


def apply_import(conn: sqlite3.Connection, plan: ImportPlan, *, inputs: dict) -> dict:
    """Write the plan in one transaction and return a report."""
    started = utc_now()
    run_id = f"run_{uuid.uuid4().hex}"
    new_sources = new_targets = 0
    with conn:
        for source in plan.sources.values():
            new_sources += _insert(conn, "source", source)
        for target in plan.targets.values():
            row = {k: v for k, v in target.items() if not k.startswith("_")}
            row["legacy_refs"] = canonical_json(row["legacy_refs"])
            row["legacy_hints"] = canonical_json(row["legacy_hints"])
            new_targets += _insert(conn, "source_target", row)
        conn.execute(
            "INSERT INTO run(id, role, inputs, outcome, started_at, finished_at)"
            " VALUES (?, 'other', ?, 'succeeded', ?, ?)",
            (run_id, canonical_json({"job": "queue_import", **inputs}), started, utc_now()))
    report = summarize(plan)
    report.update(run_id=run_id, new_sources=new_sources, new_targets=new_targets,
                  existing_sources=len(plan.sources) - new_sources,
                  existing_targets=len(plan.targets) - new_targets)
    return report


def summarize(plan: ImportPlan) -> dict:
    targets = list(plan.targets.values())
    per_source = Counter(t["source_id"] for t in targets)
    merged = [t for t in targets if len(t["legacy_refs"]) > 1]
    multi = [s for s, n in per_source.items() if n > 1]
    hosts = Counter(host_of(plan.sources[s]["url"]) for s in plan.sources)
    return {
        "old_rows": plan.rows,
        "rejected": plan.rejected,
        "sources": len(plan.sources),
        "targets": len(targets),
        "targets_by_route": dict(Counter(t["route"] for t in targets)),
        "targets_by_kind": dict(Counter(t["kind_hint"] for t in targets).most_common()),
        "targets_held": dict(Counter(t["hold"] for t in targets if t["hold"])),
        "targets_with_several_old_rows": len(merged),
        "sources_with_several_targets": len(multi),
        "sources_with_live_and_wayback": sum(
            1 for s in multi
            if {t["route"] for t in targets if t["source_id"] == s} >= {"live_fetch", "wayback"}),
        "domains": len(hosts),
        "top_domains": hosts.most_common(15),
    }


def run_import(queue_path: str | Path, conn: sqlite3.Connection) -> dict:
    rows = read_queue(queue_path)
    plan = plan_import(rows)
    inputs = {"queue_path": str(queue_path), "queue_sha256": file_sha256(queue_path),
              "queue_rows": len(rows)}
    return apply_import(conn, plan, inputs=inputs)


def write_report(report: dict, folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
