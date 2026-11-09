"""Index one stored source_version: text, sections, passages, language.

For each version that has bytes but no text yet:

1. extract canonical text (``archive.extract``) and store it in the depot
   as a .txt object (``source_version.text_path`` / ``text_sha256``);
2. cut sections (~16,000 characters, type 'body') and, inside each section,
   passages (~1,500 characters) with ``archive.passages`` — gap-free, so the
   passages of a version join back to its exact text;
   Caption passages also get time_start/time_end (seconds) from their cues;
3. detect the language of every passage with lingua ('und' when a passage
   has too few letters), with the top confidence value;
4. write sections and passages in one transaction (the passage_fts index is
   filled by the schema's triggers);
5. fill empty source fields: title, publisher (site name), published date
   (from HTML metadata, certainty 'inferred') and main language (the
   language covering most characters).

A version whose file cannot be read is flagged ``extraction_failed`` and
skipped by later runs unless asked to retry.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from .db import canonical_json, utc_now
from .depot import Depot
from .extract import Extraction, ExtractionError, extract
from .passages import build_passages, build_sections, sha256_text

MIN_LETTERS_FOR_LANGUAGE = 20
_ISO_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_detector = None


def detector():
    """Lazily built lingua detector over all languages (shared)."""
    global _detector
    if _detector is None:
        from lingua import LanguageDetectorBuilder
        _detector = LanguageDetectorBuilder.from_all_languages().build()
    return _detector


def detect_language(text: str) -> tuple[str, float | None]:
    """(ISO 639-1 code, confidence) or ('und', None) for text with too few letters."""
    if sum(ch.isalpha() for ch in text) < MIN_LETTERS_FOR_LANGUAGE:
        return "und", None
    values = detector().compute_language_confidence_values(text)
    if not values:
        return "und", None
    top = values[0]
    return top.language.iso_code_639_1.name.lower(), round(float(top.value), 4)


@dataclass
class IndexResult:
    version_id: str
    status: str                     # 'indexed' | 'failed'
    chars: int = 0
    sections: int = 0
    passages: int = 0
    languages: dict = field(default_factory=dict)   # language -> characters
    flags: list = field(default_factory=list)
    error: str | None = None


def pending_versions(conn: sqlite3.Connection, *, limit: int | None = None,
                     retry_failed: bool = False) -> list[sqlite3.Row]:
    sql = ("SELECT v.*, s.url AS source_url FROM source_version v JOIN source s ON s.id = v.source_id"
           " WHERE v.text_sha256 IS NULL")
    if not retry_failed:
        sql += " AND NOT EXISTS (SELECT 1 FROM json_each(v.quality_flags) WHERE value = 'extraction_failed')"
    sql += " ORDER BY v.created_at, v.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def _times(extraction: Extraction, start: int, end: int) -> tuple[float | None, float | None]:
    first, last = extraction.time_of(start), extraction.time_of(end - 1)
    return (first[0] if first else None, last[1] if last else None)


def _extension(path: str) -> str:
    return path.rsplit(".", 1)[-1].lower()


def _merge_flags(existing: str, new: list[str]) -> str:
    flags = set(json.loads(existing or "[]")) | set(new)
    if "extraction_failed" not in new:      # a successful retry clears the old failure
        flags.discard("extraction_failed")
    return canonical_json(sorted(flags))


def _spans(version_id: str, extraction: Extraction):
    """Sections and passages with offsets in the version's canonical text."""
    text = extraction.text
    sections, passages = [], []
    for section in build_sections(text, doc_id=version_id).spans:
        sections.append(section)
        inner = build_passages(section.text, doc_id=version_id).spans
        for unit in inner:
            passages.append((section.sequence, section.char_start + unit.char_start,
                             section.char_start + unit.char_end, unit.text))
    return sections, passages


class TextIndexer:
    def __init__(self, conn: sqlite3.Connection, depot: Depot):
        self.conn, self.depot = conn, depot

    def index(self, version: sqlite3.Row) -> IndexResult:
        version_id = version["id"]
        try:
            data = self.depot.read(version["path"])
            extraction = extract(data, _extension(version["path"]), url=version["source_url"])
        except ExtractionError as exc:
            with self.conn:
                self.conn.execute("UPDATE source_version SET quality_flags = ? WHERE id = ?",
                                  (_merge_flags(version["quality_flags"], ["extraction_failed"]),
                                   version_id))
            return IndexResult(version_id, "failed", error=str(exc), flags=["extraction_failed"])

        text = extraction.text
        text_path = self.depot.put(text.encode("utf-8"), "txt").relative_path if text else None
        sections, passages = _spans(version_id, extraction) if text else ([], [])
        now = utc_now()
        result = IndexResult(version_id, "indexed", chars=len(text), sections=len(sections),
                             passages=len(passages), flags=extraction.flags)
        language_chars: Counter = Counter()
        rows = []
        for sequence, (section_seq, start, end, passage_text) in enumerate(passages, 1):
            language, confidence = detect_language(passage_text)
            language_chars[language] += len(passage_text)
            time_start, time_end = _times(extraction, start, end)
            rows.append((f"psg_{version_id[4:]}_{sequence:05d}", version_id,
                         f"sec_{version_id[4:]}_{section_seq:03d}", sequence, start, end,
                         extraction.page_of(start), extraction.page_of(end - 1), passage_text,
                         sha256_text(passage_text), language, confidence, now,
                         time_start, time_end))
        result.languages = dict(language_chars.most_common())

        with self.conn:
            self.conn.execute(
                "UPDATE source_version SET extraction_tool = ?, text_sha256 = ?, text_path = ?,"
                " quality_flags = ? WHERE id = ?",
                (extraction.tool, sha256_text(text), text_path,
                 _merge_flags(version["quality_flags"], extraction.flags), version_id))
            for section in sections:
                end = section.char_end
                self.conn.execute(
                    "INSERT INTO section(id, version_id, sequence, type, char_start, char_end,"
                    " page_start, page_end, created_at, time_start, time_end)"
                    " VALUES (?,?,?, 'body', ?,?,?,?,?,?,?)",
                    (f"sec_{version_id[4:]}_{section.sequence:03d}", version_id, section.sequence,
                     section.char_start, end, extraction.page_of(section.char_start),
                     extraction.page_of(end - 1), now, *_times(extraction, section.char_start, end)))
            self.conn.executemany(
                "INSERT INTO passage(id, version_id, section_id, sequence, char_start, char_end,"
                " page_start, page_end, text, text_sha256, language, language_confidence,"
                " created_at, time_start, time_end) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._fill_source(version["source_id"], extraction, language_chars)
        return result

    def _fill_source(self, source_id: str, extraction: Extraction, language_chars: Counter) -> None:
        source = self.conn.execute("SELECT * FROM source WHERE id = ?", (source_id,)).fetchone()
        updates: dict = {}
        if not source["title"] and extraction.title:
            updates["title"] = extraction.title[:500]
        if not source["publisher"] and extraction.sitename:
            updates["publisher"] = extraction.sitename[:300]
        if (not source["published_date"] and extraction.date and extraction.page_starts is None
                and _ISO_DATE.match(extraction.date)):
            updates["published_date"] = extraction.date
            updates["published_date_certainty"] = "inferred"
        known = [(lang, n) for lang, n in language_chars.most_common() if lang != "und"]
        if not source["language"] and known:
            updates["language"] = known[0][0]
        if updates:
            assignments = ", ".join(f"{column} = ?" for column in updates)
            self.conn.execute(f"UPDATE source SET {assignments} WHERE id = ?",
                              (*updates.values(), source_id))
