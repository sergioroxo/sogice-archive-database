"""Acquire one source_target: fetch, store the bytes, record version and attempts.

Routes
* ``wayback`` target: fetch that capture in raw (``id_``) form.
* ``live_fetch`` target: fetch the live URL (robots.txt obeyed). If that does
  not give a document (blocked, gone, parked, disallowed, network failure...),
  look up the newest Wayback captures (CDX) and fetch up to three of them. A version
  found this way is flagged ``wayback_fallback``.

Retry rule (CLAUDE.md): a transient failure (timeout, 5xx, 429) is retried
once in the same run; then the target is failed and the batch continues.
Failed targets are skipped by later runs unless ``retry_failed`` is set.

Identical bytes are stored once: a second target (or a re-run) that gets the
same file attaches to the existing source_version.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field

from .db import canonical_json, utc_now
from .depot import Depot, sniff_extension
from .fetch import Capture, FetchResult, Fetcher, capture_time
from .urls import UrlError, parse_wayback, short_hash, wayback_raw_url

STORABLE = {"html", "pdf", "txt", "docx", "doc"}
FALLBACK_AFTER = {"blocked", "not_found", "gone", "http_error", "robots_disallowed",
                  "network_error", "empty", "unsupported_type"}
MAX_FALLBACK_CAPTURES = 3   # newest captures of a dead site are often the parking page
RETRY_PAUSE = 10.0
MAX_RETRY_PAUSE = 60.0
SKIPPED_KINDS = ("youtube",)  # captions come later (Phase 1 step 6)


@dataclass
class TargetResult:
    target_id: str
    status: str                       # 'stored' | 'failed'
    version_id: str | None = None
    route: str | None = None          # route of the stored attempt
    new_file: bool = False
    outcomes: list = field(default_factory=list)   # every attempt's outcome, in order


def pending_targets(conn: sqlite3.Connection, *, limit: int | None = None,
                    kinds: list[str] | None = None, target_ids: list[str] | None = None,
                    retry_failed: bool = False) -> list[sqlite3.Row]:
    """Targets not yet stored (and, unless retry_failed, never tried)."""
    sql = ["SELECT * FROM source_target t WHERE t.hold IS NULL",
           "AND NOT EXISTS (SELECT 1 FROM fetch_attempt a WHERE a.target_id = t.id"
           " AND a.outcome = 'stored')"]
    params: list = []
    if not retry_failed:
        sql.append("AND NOT EXISTS (SELECT 1 FROM fetch_attempt a WHERE a.target_id = t.id)")
    if target_ids:
        sql.append(f"AND t.id IN ({','.join('?' for _ in target_ids)})")
        params += target_ids
    else:
        sql.append(f"AND t.kind_hint NOT IN ({','.join('?' for _ in SKIPPED_KINDS)})")
        params += list(SKIPPED_KINDS)
    if kinds:
        sql.append(f"AND t.kind_hint IN ({','.join('?' for _ in kinds)})")
        params += kinds
    sql.append("ORDER BY t.id")
    if limit:
        sql.append("LIMIT ?")
        params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


class Acquirer:
    def __init__(self, conn: sqlite3.Connection, fetcher: Fetcher, depot: Depot, *,
                 run_id: str | None = None):
        self.conn, self.fetcher, self.depot, self.run_id = conn, fetcher, depot, run_id

    # -- recording ---------------------------------------------------------

    def _record(self, target_id: str, attempt: int, route: str, result: FetchResult,
                version_id: str | None = None, extra: dict | None = None) -> None:
        detail = result.detail()
        if extra:
            detail.update(extra)
        outcome = "stored" if version_id else (
            result.outcome if result.outcome != "ok" else "unsupported_type")
        with self.conn:
            self.conn.execute(
                "INSERT INTO fetch_attempt(id, target_id, run_id, attempt, route, requested_url,"
                " final_url, http_status, content_type, outcome, detail, version_id, seconds,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"fat_{uuid.uuid4().hex}", target_id, self.run_id, attempt, route, result.url,
                 result.final_url, result.http_status, result.content_type, outcome,
                 canonical_json(detail), version_id, result.seconds, utc_now()))

    def _store(self, target: sqlite3.Row, route: str, result: FetchResult,
               flags: list[str]) -> tuple[str | None, bool, str | None]:
        """Store the body; return (version_id, new_file, refusal reason)."""
        extension = sniff_extension(result.body or b"", result.content_type or "")
        if extension not in STORABLE:
            return None, False, f"not a document type ({extension}, {result.content_type})"
        stored = self.depot.put(result.body, extension)
        if route == "wayback":
            captured, _ = capture_time(result.final_url)
        else:
            captured = utc_now()
        version_id = "ver_" + short_hash(f"{target['source_id']}|{stored.sha256}")
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO source_version(id, source_id, sha256, path, byte_size,"
                " mime_type, acquisition_route, quality_flags, captured_at, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (version_id, target["source_id"], stored.sha256, stored.relative_path, stored.size,
                 (result.content_type or "").split(";")[0].strip() or None, route,
                 canonical_json(sorted(flags)), captured, utc_now()))
            row = self.conn.execute(
                "SELECT id FROM source_version WHERE source_id = ? AND sha256 = ?",
                (target["source_id"], stored.sha256)).fetchone()
        return row["id"], stored.created, None

    # -- fetching ----------------------------------------------------------

    def _fetch_and_store(self, target: sqlite3.Row, url: str, route: str,
                         flags: list[str], outcomes: list, extra: dict | None = None
                         ) -> tuple[str | None, bool, FetchResult]:
        """Fetch with one retry on a transient failure; store a document if one came back."""
        result = None
        for attempt in (1, 2):
            if attempt == 2:
                pause = min(result.retry_after or RETRY_PAUSE, MAX_RETRY_PAUSE)
                self.fetcher.sleep(pause)
            result = self.fetcher.get(url, route=route)
            if result.ok:
                version_id, new_file, refusal = self._store(target, route, result, flags)
                if refusal:
                    result.outcome, result.error = "unsupported_type", refusal
                self._record(target["id"], attempt, route, result, version_id, extra)
                outcomes.append("stored" if version_id else result.outcome)
                return version_id, new_file, result
            self._record(target["id"], attempt, route, result, None, extra)
            outcomes.append(result.outcome)
            if not result.transient:
                break
        return None, False, result

    def acquire(self, target: sqlite3.Row) -> TargetResult:
        outcomes: list = []
        if target["route"] == "wayback":
            try:
                ref = parse_wayback(target["url"])
                url = wayback_raw_url(ref)
            except UrlError:
                url = target["url"]
            version_id, new_file, _ = self._fetch_and_store(target, url, "wayback", [], outcomes)
            return TargetResult(target["id"], "stored" if version_id else "failed", version_id,
                                "wayback" if version_id else None, new_file, outcomes)

        version_id, new_file, live = self._fetch_and_store(
            target, target["url"], "live_fetch", [], outcomes)
        if version_id:
            return TargetResult(target["id"], "stored", version_id, "live_fetch", new_file, outcomes)
        if live.outcome not in FALLBACK_AFTER:
            return TargetResult(target["id"], "failed", outcomes=outcomes)

        captures, lookup = self.fetcher.wayback_captures(target["url"], limit=8)
        if not captures:
            lookup.outcome = "no_capture" if lookup.ok else lookup.outcome
            lookup.error = lookup.error or "no Wayback capture listed"
            self._record(target["id"], 1, "wayback", lookup, None,
                         {"fallback_for": live.outcome, "cdx": True})
            outcomes.append(lookup.outcome)
            return TargetResult(target["id"], "failed", outcomes=outcomes)
        for capture in captures[:MAX_FALLBACK_CAPTURES]:
            version_id, new_file, _ = self._fetch_and_store(
                target, capture.raw_url, "wayback", ["wayback_fallback"], outcomes,
                {"fallback_for": live.outcome, "capture_timestamp": capture.timestamp})
            if version_id:
                return TargetResult(target["id"], "stored", version_id, "wayback", new_file, outcomes)
        return TargetResult(target["id"], "failed", outcomes=outcomes)


__all__ = ["Acquirer", "Capture", "TargetResult", "pending_targets"]
