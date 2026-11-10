"""Phase 3: local reading. The model quotes; software locates.

One structured call per section of a source (consecutive passages up to
``chunk_chars``). The model returns statements (quote, speaker, quoted
party, intermediary, attribution, stance, claim flag), named entities and
stated relations. Software then:

- locates every quote in the passages it was given (``quote_locator``); a
  quote it cannot find is kept as a usage with ``evidence_state =
  'unlocated'`` and no passage, so the located share can be measured;
- turns located statements into usages (``found_by = 'model'``), claims
  (claimant = speaker; never ruled true or false) and entity mentions;
- resolves entity names by exact normalised label or variant only; an
  unknown name becomes a new proposed entity (no fuzzy merging);
- keeps a relation only with a located quote and a type from the closed
  vocabulary (unmapped wording becomes ``other_stated``).

Every record is written with a 'propose' decision (model tag, run, prompt
version, quote). A source's records are written only when all of its
sections were read; a section that fails is retried once (a reply cut off
at the token cap is retried as two halves), then the source's run is marked
failed and the batch moves on. Reading a source again supersedes the
records of its earlier reading runs (one recorded, undoable decision per
run).

Not read: merged duplicates (their keeper is read), versions flagged
``challenge_page`` or with a missing/unreadable file, and passages that
repeat across sources (page furniture).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .db import canonical_json, record_decision, utc_now
from .match import furniture_hashes
from .passages import sha256_text
from .quote_locator import locate_quote
from .relationship_vocabulary import RELATIONSHIP_TYPES, normalize_relationship_type
from .seeds import normalize_label
from .urls import host_of

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
DEFAULT_PROMPT = "read-v3"
GAP_MIN_CHARS = 400
# Time: a budget per 1,000 characters (config: reading.seconds_per_1000_chars). A source that runs
# past CEILING_FACTOR times its budget (at least MIN_CEILING_SECONDS) is stopped and marked failed,
# so one runaway document cannot hold the batch. A running run whose heartbeat is older than
# STALL_MINUTES has stalled (the process died or hangs); the next batch closes it as failed.
DEFAULT_SECONDS_PER_1000_CHARS = 10
CEILING_FACTOR = 3
MIN_CEILING_SECONDS = 600
STALL_MINUTES = 20   # a passage this long with no located quote is sent once more on its own
DEFAULT_CHUNK_CHARS = 6000
CARRY_CHARS = 400
SKIP_FLAGS = {"challenge_page", "missing_object", "unreadable_object", "corrupt_object"}
ATTRIBUTIONS = ("direct_quote", "reported_speech", "paraphrase_attributed",
                "editorial_narration", "source_claim_without_named_speaker", "unknown")
STANCES = ("endorses", "criticizes", "denies", "acknowledges", "minimizes",
           "reports", "describes", "mixed", "unclear", "not_applicable")
ENTITY_KINDS = ("person", "organization", "publication", "law", "event")
MAX_QUOTE_WORDS = 60   # a longer "quote" is a pasted paragraph: kept, but flagged in diagnostics


class ReadError(RuntimeError):
    pass


# --------------------------------------------------------------------------- prompt

@dataclass(frozen=True)
class Prompt:
    version: str
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def load_prompt(version: str = DEFAULT_PROMPT, folder: Path = PROMPTS_DIR) -> Prompt:
    path = folder / f"{version}.md"
    if not path.exists():
        raise ReadError(f"no prompt {path}")
    return Prompt(version, path.read_text(encoding="utf-8"))


def response_schema(unit_ids: list[str]) -> dict:
    unit = {"type": "string", "enum": list(unit_ids)}
    text = {"type": "string"}
    statement = {
        "type": "object", "additionalProperties": False,
        "required": ["quote", "speaker", "quoted_party", "intermediary", "attribution", "stance", "claim"],
        "properties": {
            "quote": text, "speaker": text, "quoted_party": text, "intermediary": text,
            "attribution": {"type": "string", "enum": list(ATTRIBUTIONS)},
            "stance": {"type": "string", "enum": list(STANCES)},
            "claim": {"type": "boolean"}}}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["statements", "entities", "relations"],
        "properties": {
            # One required list per unit: the model has to answer for every passage before it can
            # close the reply (with a single list it sometimes stopped after one or two items).
            "statements": {"type": "object", "additionalProperties": False, "required": list(unit_ids),
                           "properties": {u: {"type": "array", "items": statement} for u in unit_ids}},
            "entities": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "kind", "unit", "quote"],
                "properties": {"name": text, "kind": {"type": "string", "enum": list(ENTITY_KINDS)},
                               "unit": unit, "quote": text}}},
            "relations": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["subject", "type", "object", "stated_by", "unit", "quote"],
                "properties": {"subject": text,
                               "type": {"type": "string", "enum": list(RELATIONSHIP_TYPES)},
                               "object": text, "stated_by": text, "unit": unit, "quote": text}}},
        },
    }


# --------------------------------------------------------------------------- what to read

def keeper(conn: sqlite3.Connection, source_id: str) -> str:
    row = conn.execute("SELECT merged_into FROM source WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise ReadError(f"unknown source {source_id}")
    return row[0] or source_id


def source_units(conn: sqlite3.Connection, source_id: str, furniture: set | None = None) -> tuple[list[dict], dict]:
    """Passages to read for a source, in reading order, each text once.

    Returns (units, skipped) where skipped counts what was left out and why.
    """
    furniture = furniture_hashes(conn) if furniture is None else furniture
    skipped = {"flagged_versions": [], "furniture_passages": 0, "repeated_passages": 0}
    versions = conn.execute(
        "SELECT id, mime_type, quality_flags FROM source_version WHERE source_id = ? ORDER BY created_at, id",
        (source_id,)).fetchall()
    units, seen = [], set()
    for v in versions:
        flags = set(json.loads(v["quality_flags"] or "[]"))
        if flags & SKIP_FLAGS:
            skipped["flagged_versions"].append({"version_id": v["id"], "flags": sorted(flags & SKIP_FLAGS)})
            continue
        for p in conn.execute("SELECT * FROM passage WHERE version_id = ? ORDER BY sequence", (v["id"],)):
            if p["text_sha256"] in seen:
                skipped["repeated_passages"] += 1
                continue
            seen.add(p["text_sha256"])
            if p["text_sha256"] in furniture:
                skipped["furniture_passages"] += 1
                continue
            units.append({"passage_id": p["id"], "version_id": v["id"], "text": p["text"],
                          "language": p["language"], "page_start": p["page_start"], "page_end": p["page_end"],
                          "time_start": p["time_start"], "mime_type": v["mime_type"]})
    return units, skipped


def make_chunks(units: list[dict], max_chars: int = DEFAULT_CHUNK_CHARS) -> list[list[dict]]:
    """Consecutive passages of one version, up to ``max_chars`` (a passage is never split)."""
    chunks: list[list[dict]] = []
    for unit in units:
        last = chunks[-1] if chunks else None
        if (last and last[-1]["version_id"] == unit["version_id"]
                and sum(len(u["text"]) for u in last) + len(unit["text"]) <= max_chars):
            last.append(unit)
        else:
            chunks.append([unit])
    return chunks


def _where(unit: dict) -> str:
    if unit["page_start"] is not None:
        end = unit["page_start"] + unit["text"].count("\f")
        return f"page {unit['page_start']}" + (f"–{end}" if end != unit["page_start"] else "")
    if unit["time_start"] is not None:
        seconds = int(unit["time_start"])
        return f"at {seconds // 3600:d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return ""


def source_header(conn: sqlite3.Connection, source_id: str) -> str:
    s = conn.execute("SELECT * FROM source WHERE id = ?", (source_id,)).fetchone()
    lines = [f"Source URL: {s['url']}", f"Site: {host_of(s['url'])}"]
    if s["title"]:
        lines.append(f"Title: {s['title']}")
    if s["publisher"]:
        lines.append(f"Publisher: {s['publisher']}")
    if s["published_date"]:
        lines.append(f"Published: {s['published_date']} ({s['published_date_certainty']})")
    if s["captured_date"]:
        lines.append(f"Captured by the Wayback Machine: {s['captured_date']} (a capture date, "
                     "not a publication date)")
    return "\n".join(lines)


def build_messages(prompt: Prompt, header: str, chunk: list[dict], carry: dict | None,
                   section: tuple[int, int]) -> tuple[list[dict], list[dict]]:
    """Chat messages plus the unit records (P1…) the quotes are located in."""
    records = [{"unit_id": f"P{i}", "text": u["text"], **u} for i, u in enumerate(chunk, 1)]
    parts = [header, f"Section {section[0]} of {section[1]}."]
    if carry:
        parts.append("Context only, from the end of the previous section (do not quote it):\n"
                     f"Last speaker: {carry['speaker'] or 'unknown'}\n…{carry['tail']}")
    parts.append("Units:")
    for r in records:
        where = _where(r)
        parts.append(f"[{r['unit_id']}]{' (' + where + ')' if where else ''}\n{r['text']}")
    parts.append("Relation types: " + ", ".join(RELATIONSHIP_TYPES))
    return ([{"role": "system", "content": prompt.text},
             {"role": "user", "content": "\n\n".join(parts)}], records)


# --------------------------------------------------------------------------- model calls

@dataclass
class ChunkReading:
    records: list[dict]
    data: dict
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    gap_pass: bool = False
    salvaged: bool = False   # cut off at the token cap; the complete items before the cut were kept


@dataclass
class SourceReading:
    source_id: str
    chunks: list[ChunkReading] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Reader:
    def __init__(self, conn: sqlite3.Connection, client, role, *, prompt: Prompt | None = None,
                 chunk_chars: int = DEFAULT_CHUNK_CHARS, log: Callable[[str], None] = lambda _: None):
        self.conn = conn
        self.client = client
        self.role = role
        self.prompt = prompt or load_prompt()
        self.chunk_chars = chunk_chars
        self.log = log
        self.clock = time.monotonic
        self._furniture: set | None = None

    @property
    def furniture(self) -> set:
        if self._furniture is None:
            self._furniture = furniture_hashes(self.conn)
        return self._furniture

    def _call(self, messages: list[dict], unit_ids: list[str]):
        extra = self.role.extra
        return self.client.chat_json(
            self.role.model, messages, schema=response_schema(unit_ids), temperature=self.role.temperature,
            num_predict=int(extra.get("num_predict", 4096)), num_ctx=extra.get("num_ctx"),
            keep_alive=extra.get("keep_alive", 0))

    def _read_chunk(self, header: str, chunk: list[dict], carry: dict | None,
                    section: tuple[int, int], *, retried: bool = False) -> list[ChunkReading]:
        messages, records = build_messages(self.prompt, header, chunk, carry, section)
        try:
            result = self._call(messages, [r["unit_id"] for r in records])
        except Exception as exc:  # OllamaError, timeouts: retry once
            if retried:
                raise ReadError(f"section {section[0]}: {exc}") from None
            return self._read_chunk(header, chunk, carry, section, retried=True)
        reading = ChunkReading(records, result.data if isinstance(result.data, dict) else {},
                               result.seconds, result.prompt_tokens or 0, result.output_tokens or 0, 1)
        if result.truncated or not isinstance(result.data, dict):
            if len(chunk) > 1 and not retried:  # too much for one reply: read it as two halves
                half = len(chunk) // 2
                first = self._read_chunk(header, chunk[:half], carry, section, retried=True)
                second = self._read_chunk(header, chunk[half:], _carry(first[-1]), section, retried=True)
                first[0].seconds += reading.seconds
                first[0].calls += 1
                first[0].input_tokens += reading.input_tokens
                first[0].output_tokens += reading.output_tokens
                return first + second
            if retried:
                salvaged = salvage_json(result.text) if result.truncated else None
                if salvaged and statements_of(salvaged):  # keep the complete items before the cut
                    reading.data, reading.salvaged = salvaged, True
                    return [reading]
                why = "reply cut off at the token cap" if result.truncated else "reply was not JSON"
                raise ReadError(f"section {section[0]}: {why}")
            return self._read_chunk(header, chunk, carry, section, retried=True)
        return [reading]

    def ceiling_seconds(self, chars: int) -> float:
        budget = float(self.role.extra.get("seconds_per_1000_chars", DEFAULT_SECONDS_PER_1000_CHARS))
        return max(MIN_CEILING_SECONDS, CEILING_FACTOR * budget * chars / 1000)

    def read_source(self, source_id: str, on_section: Callable | None = None) -> SourceReading:
        units, _ = source_units(self.conn, source_id, self.furniture)
        chunks = make_chunks(units, self.chunk_chars)
        header = source_header(self.conn, source_id)
        out = SourceReading(source_id)
        carry = None
        ceiling = self.ceiling_seconds(sum(len(u["text"]) for u in units))
        started = self.clock()
        for n, chunk in enumerate(chunks, 1):
            if self.clock() - started > ceiling:
                raise ReadError(f"stopped after {n - 1} of {len(chunks)} sections: over the time ceiling "
                                f"of {ceiling:.0f} s for this length")
            readings = self._read_chunk(header, chunk, carry, (n, len(chunks)))
            gaps = _gaps(readings)
            if gaps:  # the model sometimes stops early: passages it skipped get one more look
                try:
                    extra = self._read_chunk(header, gaps, carry, (n, len(chunks)))
                except ReadError as exc:
                    self.log(f"  section {n}: gap pass failed ({exc}); kept the first reading")
                    extra = []
                for reading in extra:
                    reading.gap_pass = True
                readings += extra
            out.chunks += readings
            carry = _carry([r for r in readings if not r.gap_pass][-1])  # the end of the section, not a gap
            self.log(f"  section {n}/{len(chunks)}: {sum(len(statements_of(r.data)) for r in readings)}"
                     f" statements, {sum(r.seconds for r in readings):.0f} s")
            if on_section:
                on_section(n, len(chunks), sum(c.seconds for c in out.chunks))
        return out


def salvage_json(text: str) -> dict | None:
    """The longest prefix of a cut-off JSON reply that parses once its open brackets are closed."""
    stack: list[str] = []
    cuts: list[tuple[int, tuple]] = []
    in_string = escaped = False
    for i, ch in enumerate(text or ""):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
            cuts.append((i + 1, tuple(stack)))
    for pos, still_open in reversed(cuts):
        closing = "".join("}" if c == "{" else "]" for c in reversed(still_open))
        try:
            data = json.loads(text[:pos] + closing)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def statements_of(data: dict) -> list[dict]:
    """Statements as a flat list, each with its unit ("statements" is keyed by unit id)."""
    statements = data.get("statements") or {}
    if isinstance(statements, list):  # the list format of prompts read-v1 and read-v2
        return [s for s in statements if isinstance(s, dict)]
    return [{**s, "unit": unit} for unit, items in statements.items() if isinstance(items, list)
            for s in items if isinstance(s, dict)]


def _gaps(readings: list[ChunkReading]) -> list[dict]:
    """Units of at least GAP_MIN_CHARS where no statement quote was located."""
    gaps = []
    for reading in readings:
        hit = set()
        for s in statements_of(reading.data):
            found = locate_quote(s.get("quote") or "", reading.records, preferred_unit_id=s.get("unit"))
            if found:
                hit.add(found["unit_id"])
        gaps += [{k: v for k, v in r.items() if k != "unit_id"} for r in reading.records
                 if r["unit_id"] not in hit and len(r["text"].strip()) >= GAP_MIN_CHARS]
    return gaps


def _carry(reading: ChunkReading) -> dict:
    statements = statements_of(reading.data)
    speaker = next((s.get("speaker") for s in reversed(statements) if s.get("speaker")), None)
    return {"speaker": speaker, "tail": reading.records[-1]["text"][-CARRY_CHARS:]}


# --------------------------------------------------------------------------- writing records

def _blank(value) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else None
    return value or None


class EntityResolver:
    """Exact normalised label or variant; otherwise a new proposed entity."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.index: dict[str, str] = {}
        for row in conn.execute("SELECT id, normalized_label FROM entity"):
            self.index.setdefault(row[1], row[0])
        for row in conn.execute("SELECT entity_id, normalized_label FROM entity_variant"):
            self.index.setdefault(row[1], row[0])

    def find(self, name: str | None) -> str | None:
        return self.index.get(normalize_label(name or "")) if name else None

    def find_or_create(self, name: str, kind: str, *, decide: Callable) -> tuple[str, bool]:
        key = normalize_label(name)
        if key in self.index:
            return self.index[key], False
        entity_id = "ent_" + hashlib.sha256(f"model|{kind}|{key}".encode()).hexdigest()[:16]
        self.conn.execute(
            "INSERT OR IGNORE INTO entity(id, kind, label, normalized_label, created_at, evidence_state)"
            " VALUES (?,?,?,?,?, 'located')", (entity_id, kind, name.strip(), key, utc_now()))
        self.index[key] = entity_id
        decide("entity", entity_id, f"New {kind} named in the source; no exact label or variant match.", name)
        return entity_id, True


def _overlapping(usages: list[dict], passage_id: str, start: int, end: int) -> dict | None:
    for u in usages:
        if u["passage_id"] == passage_id and u["char_start"] < end and start < u["char_end"]:
            return u
    return None


def write_reading(conn: sqlite3.Connection, reading: SourceReading, *, role, prompt: Prompt,
                  run_id: str) -> dict:
    """Insert usages, claims, entities and relations for a finished reading (caller commits)."""
    now = utc_now()
    counts = {"statements": 0, "located": 0, "unlocated": 0, "claims": 0, "entity_mentions": 0,
              "new_entities": 0, "relations": 0, "relations_dropped": 0, "long_quotes": 0,
              "repeats_dropped": 0, "salvaged_sections": sum(c.salvaged for c in reading.chunks)}
    unlocated_examples: list[dict] = []

    def decide(table: str, target_id: str, reason: str, quote: str | None = None):
        record_decision(conn, actor_kind="model", actor_id=role.model, run_id=run_id, action="propose",
                        target_table=table, target_id=target_id, quote=quote,
                        reason=f"{prompt.version}: {reason}")

    def insert_usage(fields: dict) -> dict:
        usage_id = f"use_{uuid.uuid4().hex[:20]}"
        row = {"id": usage_id, "found_by": "model", "run_id": run_id, "created_at": now, **fields}
        row["quote_sha256"] = sha256_text(row["quote"])
        cols = ", ".join(row)
        conn.execute(f"INSERT INTO usage({cols}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
        return row

    resolver = EntityResolver(conn)
    for chunk in reading.chunks:
        by_unit = {r["unit_id"]: r for r in chunk.records}
        statements: list[dict] = []
        repeats: set = set()
        for s in statements_of(chunk.data):
            quote = s.get("quote") or ""
            if not quote.strip():
                continue
            key = (normalize_label(quote), normalize_label(s.get("speaker") or ""))
            if key in repeats:  # a model stuck in a loop repeats itself; keep one copy
                counts["repeats_dropped"] += 1
                continue
            repeats.add(key)
            counts["statements"] += 1
            if len(quote.split()) > MAX_QUOTE_WORDS:
                counts["long_quotes"] += 1
            found = locate_quote(quote, chunk.records, preferred_unit_id=s.get("unit"))
            fields = {"speaker": _blank(s.get("speaker")) or "unknown",
                      "quoted_party": _blank(s.get("quoted_party")),
                      "intermediary": _blank(s.get("intermediary")),
                      "attribution_mode": s.get("attribution") if s.get("attribution") in ATTRIBUTIONS else "unknown",
                      "stance": s.get("stance") if s.get("stance") in STANCES else "unknown"}
            if found is None:
                counts["unlocated"] += 1
                if len(unlocated_examples) < 20:
                    unlocated_examples.append({"unit": s.get("unit"), "quote": quote[:200]})
                row = insert_usage({**fields, "quote": quote.strip()[:2000], "evidence_state": "unlocated"})
                decide("usage", row["id"], "Statement quote could not be located in the text; kept as unlocated.",
                       row["quote"])
                continue
            unit = by_unit[found["unit_id"]]
            row = insert_usage({**fields, "passage_id": unit["passage_id"], "char_start": found["start"],
                                "char_end": found["end"], "quote": found["quote"], "language": unit["language"],
                                "evidence_state": "located"})
            counts["located"] += 1
            statements.append(row)
            decide("usage", row["id"], f"Statement by {fields['speaker']} ({fields['attribution_mode']}, "
                                       f"{fields['stance']}); quote located by software ({found['method']}).",
                   found["quote"])
            if s.get("claim") is True:
                claim_id = f"clm_{uuid.uuid4().hex[:20]}"
                conn.execute("INSERT INTO claim(id, claimant_entity_id, claimant_label, text, created_at,"
                             " evidence_state) VALUES (?,?,?,?,?, 'located')",
                             (claim_id, resolver.find(fields["speaker"]), fields["speaker"], found["quote"], now))
                conn.execute("INSERT INTO claim_usage(claim_id, usage_id) VALUES (?,?)", (claim_id, row["id"]))
                counts["claims"] += 1
                decide("claim", claim_id, f"Claim attributed to {fields['speaker']}; not assessed for truth.",
                       found["quote"])

        seen_names: set = set()
        for e in chunk.data.get("entities") or []:
            name, kind = _blank(e.get("name")), e.get("kind")
            if not name or kind not in ENTITY_KINDS or normalize_label(name) in seen_names:
                continue
            found = locate_quote(e.get("quote") or name, chunk.records, preferred_unit_id=e.get("unit"))
            if found is None:
                continue  # a name not in the text is not a mention
            seen_names.add(normalize_label(name))
            entity_id, created = resolver.find_or_create(name, kind, decide=decide)
            counts["new_entities"] += created
            unit = by_unit[found["unit_id"]]
            voice = _overlapping(statements, unit["passage_id"], found["start"], found["end"])
            row = insert_usage({"entity_id": entity_id, "passage_id": unit["passage_id"],
                                "char_start": found["start"], "char_end": found["end"], "quote": found["quote"],
                                "language": unit["language"], "evidence_state": "located",
                                "speaker": voice["speaker"] if voice else "unknown",
                                "attribution_mode": voice["attribution_mode"] if voice else "unknown",
                                "stance": "not_applicable"})
            counts["entity_mentions"] += 1
            decide("usage", row["id"], f"Mention of {kind} '{name}'; located by software.", found["quote"])

        for r in chunk.data.get("relations") or []:
            subject, obj = _blank(r.get("subject")), _blank(r.get("object"))
            found = locate_quote(r.get("quote") or "", chunk.records, preferred_unit_id=r.get("unit"))
            if not subject or not obj or found is None:
                counts["relations_dropped"] += 1
                continue
            rtype, method = normalize_relationship_type(r.get("type") or "")
            unit = by_unit[found["unit_id"]]
            evidence = _overlapping(statements, unit["passage_id"], found["start"], found["end"])
            if evidence is None:
                evidence = insert_usage({"passage_id": unit["passage_id"], "char_start": found["start"],
                                         "char_end": found["end"], "quote": found["quote"],
                                         "language": unit["language"], "evidence_state": "located",
                                         "speaker": _blank(r.get("stated_by")) or "unknown",
                                         "attribution_mode": "unknown", "stance": "not_applicable"})
                decide("usage", evidence["id"], "Quote stating a relation; located by software.", found["quote"])
            subject_id, object_id = resolver.find(subject), resolver.find(obj)
            status = ("resolved" if subject_id and object_id else "subject_resolved" if subject_id
                      else "object_resolved" if object_id else "unresolved")
            relation_id = f"rel_{uuid.uuid4().hex[:20]}"
            conn.execute(
                "INSERT INTO relation(id, subject_entity_id, subject_label, type, object_entity_id, object_label,"
                " attribution, attributed_source_id, layer, resolution_status, created_at, evidence_state)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?, 'located')",
                (relation_id, subject_id, subject, rtype, object_id, obj,
                 _blank(r.get("stated_by")) or evidence["speaker"], reading.source_id,
                 "co_mention" if rtype == "co_mentioned" else "asserted", status, now))
            conn.execute("INSERT INTO relation_usage(relation_id, usage_id) VALUES (?,?)", (relation_id, evidence["id"]))
            counts["relations"] += 1
            decide("relation", relation_id, f"Source states: {subject} {rtype} {obj} (type {method}); "
                                            f"stated by {_blank(r.get('stated_by')) or 'unknown'}.", found["quote"])
    # Coverage: passages the model read but quoted nothing from. Not an error (a passage may hold
    # nothing relevant), but long gaps are listed so a person or a second pass can look at them.
    sent = [r["passage_id"] for c in reading.chunks for r in c.records]
    quoted = {u["passage_id"] for u in conn.execute(
        "SELECT passage_id FROM usage WHERE run_id = ? AND passage_id IS NOT NULL", (run_id,))}
    uncovered = [pid for pid in sent if pid not in quoted]
    counts["passages_sent"] = len(sent)
    counts["passages_without_quotes"] = len(uncovered)
    return {**counts, "unlocated_examples": unlocated_examples, "uncovered_passages": uncovered[:200]}


def supersede_earlier(conn: sqlite3.Connection, source_id: str, new_run_id: str, *, actor_id: str) -> int:
    """Mark records of earlier reading runs of this source superseded (one decision per run)."""
    runs = [r[0] for r in conn.execute(
        """SELECT id FROM run WHERE role = 'reading' AND outcome = 'succeeded' AND id <> ?
           AND json_extract(inputs, '$.source_id') = ?
           AND id NOT IN (SELECT target_id FROM decision WHERE target_table = 'run' AND action = 'supersede')""",
        (new_run_id, source_id))]
    for old in runs:
        usage_ids = "SELECT id FROM usage WHERE run_id = ?"
        conn.execute(f"UPDATE claim SET review_state = 'superseded' WHERE id IN "
                     f"(SELECT claim_id FROM claim_usage WHERE usage_id IN ({usage_ids}))", (old,))
        conn.execute(f"UPDATE relation SET review_state = 'superseded' WHERE id IN "
                     f"(SELECT relation_id FROM relation_usage WHERE usage_id IN ({usage_ids}))", (old,))
        conn.execute("UPDATE usage SET review_state = 'superseded' WHERE run_id = ?", (old,))
        record_decision(conn, actor_kind="model", actor_id=actor_id, run_id=new_run_id, action="supersede",
                        target_table="run", target_id=old,
                        reason=f"Source {source_id} read again by run {new_run_id}; earlier records superseded.",
                        previous_state={"review_state": "proposed"},
                        new_state={"review_state": "superseded", "superseded_by": new_run_id})
    return len(runs)


# --------------------------------------------------------------------------- batch

def _heartbeat(conn: sqlite3.Connection, run_id: str, done: int, total: int, seconds: float) -> None:
    with conn:
        conn.execute("UPDATE run SET heartbeat_at = ?, progress = ? WHERE id = ?",
                     (utc_now(), canonical_json({"sections_done": done, "sections": total,
                                                 "model_seconds": round(seconds, 1)}), run_id))


def _age_minutes(stamp: str | None, now: datetime) -> float | None:
    if not stamp:
        return None
    then = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (now - then).total_seconds() / 60


def run_status(conn: sqlite3.Connection, *, stall_minutes: float = STALL_MINUTES) -> list[dict]:
    """Reading runs still marked running, with their progress and whether they have stalled."""
    now = datetime.now(timezone.utc)
    out = []
    for r in conn.execute("SELECT * FROM run WHERE role = 'reading' AND outcome = 'running' ORDER BY started_at"):
        age = _age_minutes(r["heartbeat_at"] or r["started_at"], now)
        out.append({"id": r["id"], "source_id": json.loads(r["inputs"]).get("source_id"),
                    "started_at": r["started_at"], "heartbeat_at": r["heartbeat_at"] or r["started_at"],
                    "progress": json.loads(r["progress"]) if r["progress"] else None,
                    "minutes_since_heartbeat": round(age, 1) if age is not None else None,
                    "stalled": age is not None and age > stall_minutes})
    return out


def close_stalled(conn: sqlite3.Connection, *, stall_minutes: float = STALL_MINUTES) -> list[dict]:
    """Mark stalled reading runs failed (nothing was written for them: records come at the end)."""
    stale = [r for r in run_status(conn, stall_minutes=stall_minutes) if r["stalled"]]
    with conn:
        for r in stale:
            conn.execute("UPDATE run SET outcome = 'failed', error = ?, finished_at = ? WHERE id = ?",
                         (f"stalled: no progress since {r['heartbeat_at']}", utc_now(), r["id"]))
    return stale


def already_read(conn: sqlite3.Connection, source_id: str, prompt: Prompt, model: str) -> bool:
    return conn.execute(
        """SELECT 1 FROM run WHERE role = 'reading' AND outcome = 'succeeded' AND model = ?
           AND prompt_version = ? AND json_extract(inputs, '$.source_id') = ?
           AND json_extract(inputs, '$.prompt_sha256') = ?""",
        (model, prompt.version, source_id, prompt.sha256)).fetchone() is not None


def read_sources(conn: sqlite3.Connection, client, role, source_ids: list[str], *,
                 prompt: Prompt | None = None, chunk_chars: int = DEFAULT_CHUNK_CHARS,
                 again: bool = False, log: Callable[[str], None] = lambda _: None) -> list[dict]:
    conn.row_factory = sqlite3.Row
    reader = Reader(conn, client, role, prompt=prompt, chunk_chars=chunk_chars, log=log)
    try:
        return _read_all(conn, reader, role, source_ids, again=again, chunk_chars=chunk_chars, log=log)
    finally:  # one resident model at a time: free it when the batch ends
        try:
            client.unload(role.model)
        except Exception:
            pass


def _read_all(conn, reader: Reader, role, source_ids: list[str], *, again: bool, chunk_chars: int,
              log: Callable[[str], None]) -> list[dict]:
    prompt = reader.prompt
    for stale in close_stalled(conn):
        log(f"closed stalled run {stale['id']} ({stale['source_id']}): no progress since {stale['heartbeat_at']}")
    results, done = [], set()
    for requested in source_ids:
        source_id = keeper(conn, requested)
        if source_id in done:
            continue
        done.add(source_id)
        if not again and already_read(conn, source_id, prompt, role.model):
            results.append({"source_id": source_id, "outcome": "skipped", "why": "already read with this prompt"})
            continue
        units, skipped = source_units(conn, source_id, reader.furniture)
        chunks = make_chunks(units, chunk_chars)
        run_id = f"run_{uuid.uuid4().hex}"
        inputs = {"source_id": source_id, "requested": requested, "prompt_sha256": prompt.sha256,
                  "chunk_chars": chunk_chars, "passages": len(units), "sections": len(chunks),
                  "chars": sum(len(u["text"]) for u in units),
                  "versions": sorted({u["version_id"] for u in units}), "skipped": skipped}
        with conn:
            conn.execute("INSERT INTO run(id, role, model, prompt_version, inputs, started_at, heartbeat_at,"
                         " progress) VALUES (?, 'reading', ?, ?, ?, ?, ?, ?)",
                         (run_id, role.model, prompt.version, canonical_json(inputs), utc_now(), utc_now(),
                          canonical_json({"sections_done": 0, "sections": len(chunks)})))
        log(f"{source_id}: {len(units)} passages in {len(chunks)} sections")
        started = time.monotonic()
        try:
            reading = reader.read_source(source_id, on_section=lambda n, total, secs: _heartbeat(
                conn, run_id, n, total, secs))
        except Exception as exc:  # the batch continues; nothing from this source is written
            with conn:
                conn.execute("UPDATE run SET outcome = 'failed', error = ?, seconds = ?, finished_at = ? WHERE id = ?",
                             (str(exc)[:1000], round(time.monotonic() - started, 1), utc_now(), run_id))
            results.append({"source_id": source_id, "run_id": run_id, "outcome": "failed", "error": str(exc)})
            log(f"  failed: {exc}")
            continue
        model_seconds = sum(c.seconds for c in reading.chunks)
        with conn:
            counts = write_reading(conn, reading, role=role, prompt=prompt, run_id=run_id)
            superseded = supersede_earlier(conn, source_id, run_id, actor_id=role.model)
            conn.execute(
                "UPDATE run SET outcome = 'succeeded', seconds = ?, input_tokens = ?, output_tokens = ?,"
                " inputs = ?, finished_at = ? WHERE id = ?",
                (round(model_seconds, 1), sum(c.input_tokens for c in reading.chunks),
                 sum(c.output_tokens for c in reading.chunks),
                 canonical_json({**inputs, "calls": sum(c.calls for c in reading.chunks),
                                 "gap_calls": sum(c.calls for c in reading.chunks if c.gap_pass),
                                 "wall_seconds": round(time.monotonic() - started, 1),
                                 "counts": {k: v for k, v in counts.items() if k not in ("unlocated_examples", "uncovered_passages")},
                                 "unlocated_examples": counts["unlocated_examples"],
                                 "uncovered_passages": counts["uncovered_passages"]}),
                 utc_now(), run_id))
        results.append({"source_id": source_id, "run_id": run_id, "outcome": "succeeded",
                        "seconds": round(model_seconds, 1), "superseded_runs": superseded,
                        **{k: v for k, v in counts.items() if k not in ("unlocated_examples", "uncovered_passages")}})
        log(f"  done in {model_seconds:.0f} s: {counts['located']} located, {counts['unlocated']} unlocated,"
            f" {counts['claims']} claims, {counts['entity_mentions']} entity mentions, {counts['relations']} relations")
    return results
