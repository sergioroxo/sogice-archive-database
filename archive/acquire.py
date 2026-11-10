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

A live page or fallback capture whose HTML yields no text (a script-rendered
shell) is not stored: it counts as ``empty`` and the fallback continues. A
Wayback target (a specific capture someone chose) is stored as it is.

A short scholarly landing page (under 3,000 characters of text) that declares
its PDF in a ``citation_pdf_url`` meta tag gets that PDF stored too (flag
``citation_pdf``): PMC pages of scanned articles, OJS journals, repositories.

A live target whose URL holds a DOI also goes to Crossref/OpenAlex
(``archive.doi``): registered metadata always; when the page is blocked, the
abstract and open-access copies too, before a single Wayback try.

Identical bytes are stored once: a second target (or a re-run) that gets the
same file attaches to the existing source_version.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import uuid
from urllib.parse import urljoin
from dataclasses import dataclass, field

from .db import canonical_json, utc_now
from .depot import Depot, DepotError, sniff_extension
from .doi import CROSSREF, MAX_OA_COPIES, OPENALEX, DoiResolver, doi_candidates
from .extract import ExtractionError, extract_html
from .fetch import REFUSED_TYPE_PREFIXES, Capture, FetchResult, Fetcher, capture_time
from .urls import UrlError, current_url, parse_wayback, short_hash, wayback_raw_url

STORABLE = {"html", "pdf", "txt", "docx", "doc"}
FALLBACK_AFTER = {"blocked", "not_found", "gone", "http_error", "robots_disallowed",
                  "network_error", "empty", "unsupported_type"}
MAX_FALLBACK_CAPTURES = 3   # newest captures of a dead site are often the parking page
RETRY_PAUSE = 10.0
MAX_RETRY_PAUSE = 60.0
VIDEO_KINDS = ("youtube",)  # handled by archive.captions, not fetched as pages


@dataclass
class TargetResult:
    target_id: str
    status: str                       # 'stored' | 'failed'
    version_id: str | None = None
    route: str | None = None          # route of the stored attempt
    new_file: bool = False
    outcomes: list = field(default_factory=list)   # every attempt's outcome, in order


def _text_length(body: bytes | None, url: str | None) -> int:
    """Characters of text this HTML gives (0 for none or unreadable)."""
    try:
        extraction = extract_html(body or b"", url)
    except ExtractionError:
        return 0
    return 0 if "no_text" in extraction.flags else len(extraction.text)


def _has_text(body: bytes | None, url: str | None) -> bool:
    """Does this HTML give any text? (A live page or a fallback capture must.)"""
    return _text_length(body, url) > 0


_CITATION_PDF = re.compile(
    rb"""<meta[^>]+name=["']citation_pdf_url["'][^>]+content=["']([^"']+)["']"""
    rb"""|<meta[^>]+content=["']([^"']+)["'][^>]+name=["']citation_pdf_url["']""", re.IGNORECASE)
SHORT_LANDING_PAGE = 3000


def citation_pdf_url(body: bytes | None, base_url: str | None) -> str | None:
    """The PDF a scholarly landing page declares (Google Scholar citation_pdf_url tag)."""
    match = _CITATION_PDF.search((body or b"")[:400_000])
    if not match:
        return None
    raw = (match.group(1) or match.group(2)).decode("utf-8", "replace").strip()
    url = urljoin(base_url or "", html.unescape(raw))
    return url if url.startswith(("http://", "https://")) else None


def _is_media(result: FetchResult) -> bool:
    """A live audio/video/image file: no Wayback copy will be a text document either."""
    return (result.outcome == "unsupported_type"
            and (result.content_type or "").lower().startswith(REFUSED_TYPE_PREFIXES))


_CHALLENGE_STORED = ("COALESCE(a.version_id IN (SELECT v.id FROM source_version v, json_each(v.quality_flags) j"
                     " WHERE j.value = 'challenge_page'), 0)")


def recheck_stored(conn: sqlite3.Connection, depot: Depot, *, dry_run: bool = False) -> list[dict]:
    """Run today's challenge detection over stored HTML copies; flag the bot-check pages.

    Flagged versions are kept (never deleted) but no longer count as stored, so
    the next ``preserve`` fetches their targets again (live, then Wayback).
    """
    from .fetch import classify_challenge
    found = []
    rows = conn.execute("""SELECT v.id, v.source_id, v.path, v.quality_flags FROM source_version v
        WHERE (v.mime_type LIKE '%html%' OR v.path LIKE '%.html' OR v.path LIKE '%.htm')
          AND v.byte_size <= 2000000
          AND NOT EXISTS (SELECT 1 FROM json_each(v.quality_flags) j WHERE j.value = 'challenge_page')""").fetchall()
    for row in rows:
        try:
            body = depot.read(row["path"])
        except (OSError, DepotError):
            continue
        kind = classify_challenge(200, {}, body)
        if not kind:
            continue
        found.append({"version_id": row["id"], "source_id": row["source_id"], "challenge": kind})
        if not dry_run:
            flags = sorted(set(json.loads(row["quality_flags"] or "[]")) | {"challenge_page"})
            with conn:
                conn.execute("UPDATE source_version SET quality_flags = ? WHERE id = ?",
                             (canonical_json(flags), row["id"]))
    return found


def pending_targets(conn: sqlite3.Connection, *, limit: int | None = None,
                    kinds: list[str] | None = None, target_ids: list[str] | None = None,
                    retry_failed: bool = False) -> list[sqlite3.Row]:
    """Targets not yet stored (and, unless retry_failed, never tried).

    A stored copy later found to be a bot-check page (version flagged
    'challenge_page' by ``recheck_stored``) does not count: its target is
    pending again, and that attempt does not count as a try.
    """
    sql = ["SELECT * FROM source_target t WHERE t.hold IS NULL",
           f"AND NOT EXISTS (SELECT 1 FROM fetch_attempt a WHERE a.target_id = t.id"
           f" AND a.outcome = 'stored' AND NOT {_CHALLENGE_STORED})"]
    params: list = []
    if not retry_failed:
        sql.append(f"AND NOT EXISTS (SELECT 1 FROM fetch_attempt a WHERE a.target_id = t.id"
                   f" AND NOT (a.outcome = 'stored' AND {_CHALLENGE_STORED}))")
    if target_ids:
        sql.append(f"AND t.id IN ({','.join('?' for _ in target_ids)})")
        params += target_ids
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

    def _store_bytes(self, source_id: str, data: bytes, extension: str, mime: str | None,
                     route: str, flags: list[str], captured: str | None) -> tuple[str, bool]:
        """Put bytes in the depot and attach them to the source as a version (idempotent)."""
        stored = self.depot.put(data, extension)
        version_id = "ver_" + short_hash(f"{source_id}|{stored.sha256}")
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO source_version(id, source_id, sha256, path, byte_size,"
                " mime_type, acquisition_route, quality_flags, captured_at, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (version_id, source_id, stored.sha256, stored.relative_path, stored.size, mime,
                 route, canonical_json(sorted(flags)), captured, utc_now()))
            row = self.conn.execute(
                "SELECT id FROM source_version WHERE source_id = ? AND sha256 = ?",
                (source_id, stored.sha256)).fetchone()
        return row["id"], stored.created

    def _store(self, target: sqlite3.Row, route: str, result: FetchResult,
               flags: list[str], require_text: bool = False,
               expect_pdf: bool = False) -> tuple[str | None, bool, str | None]:
        """Store the body; return (version_id, new_file, refusal reason)."""
        extension = sniff_extension(result.body or b"", result.content_type or "")
        if extension not in STORABLE:
            return None, False, f"not a document type ({extension}, {result.content_type})"
        if expect_pdf and extension != "pdf":
            return None, False, f"declared PDF link returned {extension} ({result.content_type})"
        if require_text and extension == "html" and not _has_text(result.body, result.final_url):
            return None, False, "no extractable text (script-rendered or empty page)"
        captured = capture_time(result.final_url)[0] if route == "wayback" else utc_now()
        mime = (result.content_type or "").split(";")[0].strip() or None
        version_id, created = self._store_bytes(target["source_id"], result.body, extension, mime,
                                                route, flags, captured)
        return version_id, created, None

    # -- fetching ----------------------------------------------------------

    def _fetch_and_store(self, target: sqlite3.Row, url: str, route: str,
                         flags: list[str], outcomes: list, extra: dict | None = None,
                         require_text: bool = False, follow_pdf: bool = True
                         ) -> tuple[str | None, bool, FetchResult]:
        """Fetch with one retry on a transient failure; store a document if one came back."""
        result = None
        for attempt in (1, 2):
            if attempt == 2:
                pause = min(result.retry_after or RETRY_PAUSE, MAX_RETRY_PAUSE)
                self.fetcher.sleep(pause)
            result = self.fetcher.get(url, route=route)
            if result.ok:
                version_id, new_file, refusal = self._store(target, route, result, flags, require_text,
                                                            expect_pdf=not follow_pdf and "citation_pdf" in flags)
                if refusal:
                    result.outcome = "empty" if refusal.startswith("no extractable") else "unsupported_type"
                    result.error = refusal
                self._record(target["id"], attempt, route, result, version_id, extra)
                outcomes.append("stored" if version_id else result.outcome)
                if version_id and route == "live_fetch" and follow_pdf:
                    self._follow_citation_pdf(target, result, flags, outcomes, extra)
                return version_id, new_file, result
            self._record(target["id"], attempt, route, result, None, extra)
            outcomes.append(result.outcome)
            if not result.transient:
                break
        return None, False, result

    def _follow_citation_pdf(self, target: sqlite3.Row, page: FetchResult, flags: list[str],
                             outcomes: list, extra: dict | None) -> str | None:
        """A short scholarly landing page: also store the PDF it declares."""
        if "html" not in (page.content_type or "").lower():
            return None
        pdf_url = citation_pdf_url(page.body, page.final_url)
        if not pdf_url or _text_length(page.body, page.final_url) >= SHORT_LANDING_PAGE:
            return None
        version_id, _, _ = self._fetch_and_store(
            target, current_url(pdf_url), "live_fetch", sorted(set(flags) | {"citation_pdf"}), outcomes,
            {**(extra or {}), "landing_page": page.final_url}, follow_pdf=False)
        return version_id

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

        has_doi = bool(doi_candidates(target["url"]))
        version_id, new_file, live = self._fetch_and_store(
            target, current_url(target["url"]), "live_fetch", [], outcomes, require_text=True)
        if version_id:
            if has_doi:
                self._doi(target, outcomes, live_version=version_id)
            return TargetResult(target["id"], "stored", version_id, "live_fetch", new_file, outcomes)
        if live.outcome not in FALLBACK_AFTER or _is_media(live):
            return TargetResult(target["id"], "failed", outcomes=outcomes)

        doi_version, full_text = (self._doi(target, outcomes, fallback_for=live.outcome)
                                  if has_doi else (None, False))
        if full_text:
            return TargetResult(target["id"], "stored", doi_version, "doi", True, outcomes)

        # Publisher captures are mostly paywall pages or redirect loops: one try is enough.
        tries = 1 if doi_version else MAX_FALLBACK_CAPTURES
        captures, lookup = self.fetcher.wayback_captures(target["url"], limit=8)
        if not captures:
            lookup.outcome = "no_capture" if lookup.ok else lookup.outcome
            lookup.error = lookup.error or "no Wayback capture listed"
            self._record(target["id"], 1, "wayback", lookup, None,
                         {"fallback_for": live.outcome, "cdx": True})
            outcomes.append(lookup.outcome)
        for capture in captures[:tries]:
            version_id, new_file, _ = self._fetch_and_store(
                target, capture.raw_url, "wayback", ["wayback_fallback"], outcomes,
                {"fallback_for": live.outcome, "capture_timestamp": capture.timestamp},
                require_text=True)
            if version_id:
                return TargetResult(target["id"], "stored", version_id, "wayback", new_file, outcomes)
        if doi_version:
            return TargetResult(target["id"], "stored", doi_version, "doi", True, outcomes)
        return TargetResult(target["id"], "failed", outcomes=outcomes)

    # -- DOI route -------------------------------------------------------------

    def _doi(self, target: sqlite3.Row, outcomes: list, *, live_version: str | None = None,
             fallback_for: str | None = None) -> tuple[str | None, bool]:
        """Crossref/OpenAlex for the target's DOI. Returns (version id, full text?).

        With ``live_version`` (the page itself was stored) only the metadata is
        used: registered title, journal and date, and the API replies attached
        to the stored attempt. Otherwise the abstract is stored and up to two
        open-access copies are tried.
        """
        record, results = DoiResolver(self.fetcher).lookup(target["url"])
        if record is None:
            for result in results:
                if not result.ok:
                    self._record(target["id"], 1, "live_fetch", result, None,
                                 {"api": True, "fallback_for": fallback_for})
            outcomes.append("no_doi_record")
            return None, False
        detail = {"api": True, "doi": record.doi, "is_oa": record.is_oa}
        for name, data in (("crossref", record.crossref), ("openalex", record.openalex)):
            if data:
                detail[f"{name}_path"] = self.depot.put(canonical_json(data).encode("utf-8"),
                                                        "json").relative_path
        self._apply_doi_metadata(target["source_id"], record)
        if live_version:
            with self.conn:
                self.conn.execute(
                    "UPDATE fetch_attempt SET detail = json_patch(detail, ?)"
                    " WHERE target_id = ? AND version_id = ?",
                    (canonical_json(detail), target["id"], live_version))
            outcomes.append("doi_metadata")
            return None, False

        detail["fallback_for"] = fallback_for
        api_result = next((r for r in results if r.ok and
                           (r.url.startswith(CROSSREF) if record.abstract_source == "crossref"
                            else r.url.startswith(OPENALEX))), None) or next(r for r in results if r.ok)
        abstract_version = None
        if record.abstract:
            abstract_version, _ = self._store_bytes(
                target["source_id"], record.abstract_text().encode("utf-8"), "txt", "text/plain",
                "live_fetch", ["doi_abstract", f"abstract_{record.abstract_source}"], utc_now())
            self._record(target["id"], 1, "live_fetch", api_result, abstract_version, detail)
            outcomes.append("doi_abstract")
        else:
            empty = FetchResult(api_result.url, "empty", http_status=api_result.http_status,
                                error="DOI metadata found; no abstract")
            self._record(target["id"], 1, "live_fetch", empty, None, detail)
            outcomes.append("doi_no_abstract")
        for url, version, _kind in record.oa_urls[:MAX_OA_COPIES]:
            version_id, _, _ = self._fetch_and_store(
                target, url, "live_fetch", ["open_access_copy", f"oa_{version.lower()}"], outcomes,
                {**detail, "oa_version": version}, require_text=True)
            if version_id:
                return version_id, True
        return abstract_version, False

    def _apply_doi_metadata(self, source_id: str, record) -> None:
        """Registered metadata wins over page guesses: title, journal (or publisher), date."""
        updates = {}
        if record.title:
            updates["title"] = record.title[:500]
        if record.container or record.publisher:
            updates["publisher"] = (record.container or record.publisher)[:300]
        if record.date:
            updates["published_date"], updates["published_date_certainty"] = record.date, "exact"
        if updates:
            with self.conn:
                self.conn.execute(
                    f"UPDATE source SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                    (*updates.values(), source_id))


__all__ = ["Acquirer", "Capture", "TargetResult", "pending_targets"]
