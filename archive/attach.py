"""Attach the researcher's own PDFs to their sources.

The researcher's PDFs are named after the URL they were queued under. Each
file is matched to a source, most certain first:

1. ``filename``       the file name is the target URL (any encoding: '/' as
                      '_' or '-', percent-escapes, scheme kept or dropped);
                      both sides are reduced to letters and digits;
2. ``filename_prefix`` the same, for names cut short (macOS allows 255 bytes):
                      the file's slug starts one target's slug, at least 30
                      characters long, and only one source fits;
3. ``doi``            a DOI in the PDF's metadata or first two pages equals a
                      target's DOI (from its URL or from Crossref);
4. ``title``          the PDF's title (metadata, else first lines of page 1)
                      is nearly identical to a source's registered title.

A doubtful title (similarity 0.80-0.93) or a file that fits several sources
goes on a review list and is not attached: no silent merges. The folder is
only read. Each attached file becomes a ``manual_upload`` version flagged
``researcher_copy`` and ``matched_by_<rule>``, and a ``local_file`` attempt on
the target, so preserve never fetches that target again.
"""
from __future__ import annotations

import bisect
import difflib
import re
import sqlite3
import subprocess
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from .db import canonical_json, utc_now
from .depot import Depot
from .doi import _DOI, doi_candidates
from .urls import UrlError, parse_wayback, short_hash

MIN_PREFIX = 30
TITLE_ATTACH = 0.93
TITLE_REVIEW = 0.80
MIN_TITLE = 20


def url_slug(text: str) -> str:
    """Letters and digits of a URL or URL-like file name, without scheme and 'www'."""
    text = unquote(unquote(text or "")).lower().strip()
    text = re.sub(r"^(?:https?)?[^a-z0-9]*", "", text, count=1) if text.startswith("http") else text
    text = re.sub(r"^www[^a-z0-9]+", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def title_key(text: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


@dataclass
class FileMatch:
    path: Path
    status: str                      # attached | already | review | unmatched | not_pdf | error
    rule: str | None = None
    source_id: str | None = None
    target_id: str | None = None
    candidates: list = field(default_factory=list)   # for review: (source id, title, score)
    note: str | None = None


class TargetIndex:
    """Lookups from slugs, DOIs and titles to (target id, source id)."""

    def __init__(self, conn: sqlite3.Connection):
        self.by_slug: dict[str, set] = defaultdict(set)
        self.by_doi: dict[str, set] = defaultdict(set)
        self.titles: list[tuple[str, str, str]] = []      # (title key, source id, title)
        self.first_target: dict[str, str] = {}
        rows = conn.execute("SELECT id, source_id, url, route FROM source_target ORDER BY id").fetchall()
        for row in rows:
            pair = (row["id"], row["source_id"])
            self.first_target.setdefault(row["source_id"], row["id"])
            urls = [row["url"]]
            try:
                ref = parse_wayback(row["url"])
                if ref:
                    urls.append(ref.original_url)
            except UrlError:
                pass
            for url in urls:
                for slug in self._slugs(url):
                    self.by_slug[slug].add(pair)
                for doi in doi_candidates(url):
                    self.by_doi[doi].add(pair)
        for row in conn.execute("""
                SELECT a.target_id, t.source_id, json_extract(a.detail, '$.doi') AS doi
                FROM fetch_attempt a JOIN source_target t ON t.id = a.target_id
                WHERE json_extract(a.detail, '$.doi') IS NOT NULL"""):
            self.by_doi[row["doi"].lower()].add((row["target_id"], row["source_id"]))
        for row in conn.execute("SELECT id, title FROM source WHERE title IS NOT NULL"):
            key = title_key(row["title"])
            if len(key) >= MIN_TITLE:
                self.titles.append((key, row["id"], row["title"]))
        self.sorted_slugs = sorted(self.by_slug)

    @staticmethod
    def _slugs(url: str) -> set:
        slug = url_slug(url)
        out = {slug}
        if slug.endswith("pdf"):
            out.add(slug[:-3])
        no_query = url_slug(url.split("?", 1)[0].split("#", 1)[0])
        out |= {no_query, no_query[:-3] if no_query.endswith("pdf") else no_query}
        return {s for s in out if s}

    def by_filename(self, stem: str) -> tuple[set, str | None]:
        slugs = {url_slug(stem), url_slug(stem + ".pdf")}
        found = set().union(*(self.by_slug.get(s, set()) for s in slugs))
        if found:
            return found, "filename"
        slug = url_slug(stem)
        if len(slug) >= MIN_PREFIX:
            start = bisect.bisect_left(self.sorted_slugs, slug)
            prefixed = set()
            for candidate in self.sorted_slugs[start:start + 50]:
                if not candidate.startswith(slug):
                    break
                prefixed |= self.by_slug[candidate]
            if prefixed:
                return prefixed, "filename_prefix"
        return set(), None

    def by_title(self, title: str) -> list[tuple[str, str, float]]:
        key = title_key(title)
        if len(key) < MIN_TITLE:
            return []
        words = set(key.split())
        scored = []
        for other, source_id, original in self.titles:
            other_words = set(other.split())
            if len(words & other_words) / max(1, len(words | other_words)) < 0.5:
                continue
            score = difflib.SequenceMatcher(None, key, other).ratio()
            if score >= TITLE_REVIEW:
                scored.append((source_id, original, round(score, 3)))
        return sorted(scored, key=lambda item: -item[2])


def _pdf_facts(path: Path) -> tuple[str | None, list[str], str]:
    """(metadata title, DOIs found, first-page text) via pdfinfo/pdftotext."""
    def run(cmd):
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
            return done.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.TimeoutExpired):
            return ""
    info = run(["pdfinfo", "-enc", "UTF-8", str(path)])
    title = None
    for line in info.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "Title" and value.strip():
            title = value.strip()
    text = run(["pdftotext", "-enc", "UTF-8", "-l", "2", str(path), "-"])
    dois = []
    for match in _DOI.finditer(unquote(info + "\n" + text)):
        for candidate in doi_candidates("https://doi.org/" + match.group(1))[:1]:
            if candidate not in dois:
                dois.append(candidate)
    first_page = text.split("\f", 1)[0]
    return title, dois, first_page


def _single_source(pairs: set) -> tuple[str, str] | None:
    sources = {source for _, source in pairs}
    if len(sources) != 1:
        return None
    return sorted(pairs)[0]


def match_file(path: Path, index: TargetIndex) -> FileMatch:
    try:
        with open(path, "rb") as handle:
            head = handle.read(5)
    except OSError as exc:
        return FileMatch(path, "error", note=str(exc))
    if head != b"%PDF-":
        return FileMatch(path, "not_pdf")

    pairs, rule = index.by_filename(path.stem)
    if pairs:
        chosen = _single_source(pairs)
        if chosen:
            return FileMatch(path, "matched", rule, chosen[1], chosen[0])
        return FileMatch(path, "review", rule, note="file name fits several sources",
                         candidates=sorted({(s, None, 1.0) for _, s in pairs}))

    title, dois, first_page = _pdf_facts(path)
    for doi in dois:
        found = index.by_doi.get(doi)
        if found:
            chosen = _single_source(found)
            if chosen:
                return FileMatch(path, "matched", "doi", chosen[1], chosen[0], note=doi)
    guesses = [title] if title else []
    lines = [line.strip() for line in first_page.splitlines() if line.strip()]
    guesses += [" ".join(lines[:n]) for n in (1, 2, 3)] if lines else []
    best: list = []
    for guess in guesses:
        scored = index.by_title(guess)
        if scored and (not best or scored[0][2] > best[0][2]):
            best = scored
    if best:
        top = best[0]
        rivals = [s for s in best[1:] if s[2] >= top[2] - 0.02 and s[0] != top[0]]
        if top[2] >= TITLE_ATTACH and not rivals:
            return FileMatch(path, "matched", "title", top[0], index.first_target.get(top[0]),
                             note=f"{top[2]}")
        return FileMatch(path, "review", "title", candidates=best[:5],
                         note="title similar but not certain" if not rivals else "several similar titles")
    return FileMatch(path, "unmatched", note=f"dois={dois}" if dois else None)


def attach(conn: sqlite3.Connection, depot: Depot, match: FileMatch, run_id: str) -> FileMatch:
    """Store a matched file as a researcher-copy version of its source."""
    data = match.path.read_bytes()
    stored = depot.put(data, "pdf")
    existing = conn.execute("SELECT id FROM source_version WHERE source_id = ? AND sha256 = ?",
                            (match.source_id, stored.sha256)).fetchone()
    if existing:
        match.status = "already"
        return match
    mtime = datetime.fromtimestamp(match.path.stat().st_mtime, timezone.utc)
    version_id = "ver_" + short_hash(f"{match.source_id}|{stored.sha256}")
    flags = canonical_json(sorted(["researcher_copy", f"matched_by_{match.rule}"]))
    with conn:
        conn.execute(
            "INSERT INTO source_version(id, source_id, sha256, path, byte_size, mime_type,"
            " acquisition_route, quality_flags, captured_at, created_at)"
            " VALUES (?,?,?,?,?, 'application/pdf', 'manual_upload', ?, ?, ?)",
            (version_id, match.source_id, stored.sha256, stored.relative_path, stored.size, flags,
             mtime.isoformat(timespec="seconds").replace("+00:00", "Z"), utc_now()))
        conn.execute(
            "INSERT INTO fetch_attempt(id, target_id, run_id, attempt, route, requested_url, final_url,"
            " content_type, outcome, detail, version_id, created_at)"
            " VALUES (?,?,?, 1, 'local_file', ?, ?, 'application/pdf', 'stored', ?, ?, ?)",
            (f"fat_{uuid.uuid4().hex}", match.target_id, run_id, match.path.as_uri(), match.path.as_uri(),
             canonical_json({"matched_by": match.rule, "file_name": match.path.name,
                             "bytes": stored.size, "note": match.note}),
             version_id, utc_now()))
    match.status = "attached"
    return match


def run_attach(conn: sqlite3.Connection, depot: Depot, folder: Path, *, dry_run: bool = False,
               log=print) -> dict:
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    index = TargetIndex(conn)
    run_id = f"run_{uuid.uuid4().hex}"
    if not dry_run:
        with conn:
            conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                         (run_id, canonical_json({"job": "attach_pdfs", "folder": str(folder),
                                                  "files": len(files)}), utc_now()))
    results = []
    for number, path in enumerate(files, 1):
        match = match_file(path, index)
        if match.status == "matched" and not dry_run:
            match = attach(conn, depot, match, run_id)
        results.append(match)
        log(f"[{number}/{len(files)}] {match.status:9} {match.rule or '':16} {path.name[:90]}")
    if not dry_run:
        with conn:
            conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ? WHERE id = ?",
                         (utc_now(), run_id))
    counts: dict = defaultdict(int)
    for match in results:
        counts[f"{match.status}:{match.rule}" if match.rule else match.status] += 1
    return {
        "folder": str(folder), "files": len(files), "dry_run": dry_run, "run_id": None if dry_run else run_id,
        "counts": dict(sorted(counts.items())),
        "review": [{"file": m.path.name, "rule": m.rule, "note": m.note, "candidates": m.candidates}
                   for m in results if m.status == "review"],
        "unmatched": [{"file": m.path.name, "note": m.note} for m in results if m.status == "unmatched"],
        "matched": [{"file": m.path.name, "rule": m.rule, "source_id": m.source_id, "status": m.status}
                    for m in results if m.source_id],
    }
