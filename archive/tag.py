"""Tags and excerpts (docs/TAGGING.md). The model labels; software locates and compares.

Two taggers (roles ``tagging`` and ``tagging_second``) each see one section of a source at a time: its
passages and the readers' statements in it, numbered S1…, each with its quote, speaker, stance and the
lexicon terms software found inside it. For each statement a tagger gives:

- an actor type for the speaker, with the words of the text that state it;
- for each listed term, whether the speaker uses it as their own word or only describes it;
- tactic, practice and function tags from the closed lists, each as used or only described.

It also marks excerpts to keep whole (first and last words) and new terms. Software then:

- keeps a tag only for a statement it showed, a label from the lists and a mode; an actor type only
  with words it finds in the section; a term only among the statement's own matches;
- applies an actor type it found for a speaker to all of that speaker's statements in the source
  (the role is stated once, usually in one place), each citing the same words;
- locates cues, excerpt ends and new terms in the section (``quote_locator``); what it cannot find is
  dropped (a cue it cannot find is dropped, the tag stays on its statement's located quote);
- stores an excerpt as the exact span of its version's text between the two ends;
- writes every tag and excerpt with a 'propose' decision (model, run, prompt version).

``compare`` then records what the two taggers' current runs agree on: the same tag with the same mode →
the first tagger's kept, the second's superseded; the same tag with the other mode → both disputed; one
tagger only → held (assessment ``one_reader``). Excerpts of one kind are paired by overlap.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import read
from .db import canonical_json, record_decision, utc_now
from .quote_locator import locate_quote
from .seeds import normalize_label

DEFAULT_PROMPT = "tag-v3"
RULE = "tags/v2"   # v2: a speaker's stated role applies to all of their statements in the source
ACTOR_ID = f"rule:{RULE}"
ACTOR_PREFIX = "rule:tags/"
JOB = "tagging"
SCHEMES = ("tactic", "practice", "function")
MODES = ("uses", "describes")
EXCERPT_KINDS = ("testimony", "prayer_script", "conversion_script", "course_structure", "counter_sermon")
MAX_EXCERPT_CHARS = 8000
MIN_OVERLAP = 0.5
SEEDS = Path(__file__).resolve().parent.parent / "seeds"
LISTS_SEED = SEEDS / "tag_lists_2026-11-12.json"
ONTOLOGY = SEEDS / "SOGICE_Ontology_v3.0.md"
DEFAULT_SECONDS_PER_1000_CHARS = 15
# The lists each prompt version tags with. tag-v1 offered the 44 tactics; from tag-v2 the seven clusters
# (Sérgio, 12 Nov 2026: the tactics overlapped too much to tag reliably).
PROMPT_LISTS = {"tag-v1": ("tactic", "practice", "function"), "tag-v2": ("cluster", "practice", "function"),
                "tag-v3": ("cluster", "practice", "function")}
# tag-v2: up to two actor types per statement (a speaker may state two roles). tag-v3: the same, given once
# per speaker (software applies them to the speaker's other statements, spread_actor_types).
PROMPT_FEATURES = {"tag-v2": frozenset({"actor_list"}), "tag-v3": frozenset({"actor_list"})}
MAX_ACTORS = 2


class TagError(RuntimeError):
    pass


# --------------------------------------------------------------------------- vocabulary

def parse_clusters(ontology: str) -> list[dict]:
    """The seven clusters of the ontology (Part I): notation, label, core question and definition."""
    questions = {m.group(1): m.group(3).strip() for m in re.finditer(
        r"^\| (C\d) \| ([^|]+?) \| ([^|]+?) \|$", ontology, re.MULTILINE)}
    out = []
    for m in re.finditer(r"^\*\*(C\d) — ([^:*]+):\*\* (.+)$", ontology, re.MULTILINE):
        out.append({"notation": m.group(1), "label": m.group(2).strip(),
                    "definition": f"{questions.get(m.group(1), '')} {m.group(3).strip()}".strip()})
    return out


def seed_lists(conn: sqlite3.Connection, *, path: Path = LISTS_SEED, ontology: Path = ONTOLOGY,
               actor_id: str | None = None) -> dict:
    """Add what the taggers' lists need and is not there yet (caller commits):

    - actor types (scheme 'actor_type') from ``path``;
    - the ontology's seven clusters (scheme 'cluster');
    - definitions for the functions, which the seed left without one. These were drafted by Claude, so
      each is a recorded 'edit' decision by the review model (``actor_id``), with definition_source 'model'.
    """
    from .seeds import slug
    data = json.loads(path.read_text(encoding="utf-8"))
    counts = Counter()
    for item in data["actor_types"]:
        concept_id = "actor_type:" + slug(item["label"])
        counts["actor_types"] += conn.execute(
            "INSERT OR IGNORE INTO concept(id, scheme, pref_label, notation, definition, definition_source, created_at)"
            " VALUES (?, 'actor_type', ?, ?, ?, 'seed', ?)",
            (concept_id, item["label"], item.get("kind"), item.get("definition"), utc_now())).rowcount
    for item in parse_clusters(ontology.read_text(encoding="utf-8")):
        counts["clusters"] += conn.execute(
            "INSERT OR IGNORE INTO concept(id, scheme, pref_label, notation, definition, definition_source, created_at)"
            " VALUES (?, 'cluster', ?, ?, ?, 'seed', ?)",
            ("cluster:" + slug(item["label"]), item["label"], item["notation"], item["definition"], utc_now())).rowcount
    missing = [(r["id"], r["pref_label"]) for r in conn.execute(
        "SELECT id, pref_label FROM concept WHERE scheme = 'function' AND definition IS NULL")
        if r["pref_label"] in data.get("function_definitions", {}) and r["pref_label"] not in data.get("not_offered", [])]
    if missing:
        if actor_id is None:
            from .config import load_models_config
            actor_id = load_models_config().review_model or "claude"
        run_id = f"run_{uuid.uuid4().hex}"
        conn.execute("INSERT INTO run(id, role, model, inputs, started_at, outcome, finished_at)"
                     " VALUES (?, 'other', ?, ?, ?, 'succeeded', ?)",
                     (run_id, actor_id, canonical_json({"job": "tag lists", "file": path.name}), utc_now(), utc_now()))
        for concept_id, label in missing:
            definition = data["function_definitions"][label]
            conn.execute("UPDATE concept SET definition = ?, definition_source = 'model' WHERE id = ?",
                         (definition, concept_id))
            record_decision(conn, actor_kind="model", actor_id=actor_id, run_id=run_id, action="edit",
                            target_table="concept", target_id=concept_id,
                            reason=f"Function '{label}' had no definition; one drafted for the taggers ({path.name}).",
                            previous_state={"definition": None}, new_state={"definition": definition})
            counts["function_definitions"] += 1
    return dict(counts)


def not_offered(path: Path = LISTS_SEED) -> set:
    return set(json.loads(path.read_text(encoding="utf-8")).get("not_offered", []))


@dataclass(frozen=True)
class Vocabulary:
    tags: dict      # label shown to the tagger ("tactic: Identity-Erasure") -> concept id
    actors: dict    # actor type label -> concept id
    text: str       # the lists as the tagger reads them
    sha256: str


def vocabulary(conn: sqlite3.Connection, prompt_version: str = DEFAULT_PROMPT) -> Vocabulary:
    schemes = PROMPT_LISTS.get(prompt_version, PROMPT_LISTS[DEFAULT_PROMPT]) + ("actor_type",)
    rows = conn.execute(
        f"""SELECT c.id, c.scheme, c.pref_label, c.definition, c.notation, b.pref_label AS parent
           FROM concept c LEFT JOIN concept b ON b.id = c.broader
           WHERE c.scheme IN ({','.join('?' for _ in schemes)})
             AND c.review_state NOT IN ('rejected', 'superseded')
           ORDER BY c.scheme, coalesce(b.pref_label, c.pref_label), c.broader IS NOT NULL, c.notation,
                    c.pref_label""", schemes).fetchall()
    skip = not_offered()
    tags, actors, lines = {}, {}, []
    titles = {"tactic": "Tactics (use the most specific branch that fits)",
              "cluster": "Clusters (the kind of argument a statement makes or describes)", "practice": "Practices",
              "function": "Functions (what a word or phrase does)", "actor_type": "Actor types (for actors)"}
    for scheme in schemes:
        lines.append(f"\n{titles[scheme]}:")
        for r in (r for r in rows if r["scheme"] == scheme and r["pref_label"] not in skip):
            definition = " ".join((r["definition"] or "").split())
            if scheme == "actor_type":
                actors[r["pref_label"]] = r["id"]
                lines.append(f"- {r['pref_label']} ({r['notation'] or 'person'}): {definition}")
                continue
            label = f"{scheme}: {r['pref_label']}"
            tags[label] = r["id"]
            indent = "  " if r["parent"] and scheme == "tactic" else ""
            lines.append(f"{indent}- {label}" + (f": {definition}" if definition else ""))
    text = "\n".join(lines).strip()
    return Vocabulary(tags, actors, text, hashlib.sha256(text.encode("utf-8")).hexdigest())


# --------------------------------------------------------------------------- what to tag

def statements_for(conn: sqlite3.Connection, source_id: str, *, primary_model: str | None = None) -> tuple[list[dict], list[str]]:
    """The readers' current statements for a source, each once, and the reading runs they come from.

    From the first reader: every statement still standing (agreed, held or disputed). From the other
    reader: only those it alone found (its agreed copies are superseded, its disputed copies have the
    first reader's counterpart).
    """
    from .reconcile import current_readings
    runs = current_readings(conn, source_id)
    if not runs:
        return [], []
    runs = sorted(runs, key=lambda r: r["model"] != primary_model)
    out = []
    for n, r in enumerate(runs):
        skip = ("rejected", "superseded") if n == 0 else ("rejected", "superseded", "disputed")
        out += [dict(u) for u in conn.execute(
            f"""SELECT u.*, p.version_id, p.char_start AS passage_start FROM usage u
                JOIN passage p ON p.id = u.passage_id
                WHERE u.run_id = ? AND u.entity_id IS NULL AND u.evidence_state = 'located'
                  AND u.review_state NOT IN ({','.join('?' for _ in skip)})
                ORDER BY p.version_id, p.sequence, u.char_start""", (r["id"], *skip))]
    return out, [r["id"] for r in runs]


def terms_in(conn: sqlite3.Connection, statements: list[dict]) -> dict:
    """Lexicon term matches inside each statement's quote: {usage id: [{concept_id, label, match…}]}."""
    by_passage: dict = {}
    for s in statements:
        by_passage.setdefault(s["passage_id"], []).append(s)
    out: dict = {s["id"]: [] for s in statements}
    for pid, group in by_passage.items():
        for m in conn.execute(
                """SELECT u.passage_id, u.char_start, u.char_end, u.quote, c.id AS concept_id, c.pref_label
                   FROM usage u JOIN concept c ON c.id = u.concept_id
                   WHERE u.found_by = 'exact_match' AND c.scheme = 'term' AND u.passage_id = ?
                     AND u.review_state NOT IN ('rejected', 'superseded') ORDER BY u.char_start""", (pid,)):
            for s in group:
                if s["char_start"] <= m["char_start"] and m["char_end"] <= s["char_end"]:
                    if all(t["concept_id"] != m["concept_id"] for t in out[s["id"]]):
                        out[s["id"]].append(dict(m))
    return out


def response_schema(statement_ids: list[str], unit_ids: list[str], vocab: Vocabulary,
                    features: frozenset = frozenset()) -> dict:
    text = {"type": "string"}
    unit = {"type": "string", "enum": list(unit_ids)}
    if "actor_list" in features:   # tag-v2: up to two stated roles, each with the words that state it
        actor_fields = {"actors": {"type": "array", "maxItems": MAX_ACTORS, "items": {
            "type": "object", "additionalProperties": False, "required": ["type", "words"],
            "properties": {"type": {"type": "string", "enum": list(vocab.actors)}, "words": text}}}}
    else:
        actor_fields = {"actor": {"type": "string", "enum": [""] + list(vocab.actors)}, "actor_words": text}
    item = {"type": "object", "additionalProperties": False,
            "required": [*actor_fields, "terms", "tags"],
            "properties": {
                **actor_fields,
                "terms": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["term", "mode"],
                    "properties": {"term": text, "mode": {"type": "string", "enum": list(MODES)}}}},
                "tags": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["tag", "mode", "cue"],
                    "properties": {"tag": {"type": "string", "enum": list(vocab.tags)},
                                   "mode": {"type": "string", "enum": list(MODES)}, "cue": text}}}}}
    return {
        "type": "object", "additionalProperties": False, "required": ["statements", "excerpts", "new_terms"],
        "properties": {
            # one required object per statement: the tagger has to answer for each before it can close
            "statements": {"type": "object", "additionalProperties": False, "required": list(statement_ids),
                           "properties": {s: item for s in statement_ids}},
            "excerpts": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["kind", "unit", "first_words", "last_words"],
                "properties": {"kind": {"type": "string", "enum": list(EXCERPT_KINDS)}, "unit": unit,
                               "first_words": text, "last_words": text}}},
            "new_terms": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["term", "unit", "quote"],
                "properties": {"term": text, "unit": unit, "quote": text}}},
        },
    }


@dataclass
class Section:
    records: list[dict]          # units P1… (read.build_messages records)
    statements: list[dict]       # each with "sid" (S1…) and "unit_id"
    terms: dict                  # usage id -> term matches


def sections_for(conn: sqlite3.Connection, source_id: str, statements: list[dict], terms: dict, *,
                 chunk_chars: int = read.DEFAULT_CHUNK_CHARS, furniture: set | None = None) -> tuple[list[Section], int]:
    """The reading sections that hold statements. Returns (sections, statements in passages not sent)."""
    units, _ = read.source_units(conn, source_id, furniture)
    by_passage: dict = {}
    for s in statements:
        by_passage.setdefault(s["passage_id"], []).append(s)
    sections, placed = [], 0
    for chunk in read.make_chunks(units, chunk_chars):
        records = [{"unit_id": f"P{i}", **u} for i, u in enumerate(chunk, 1)]
        inside = []
        for r in records:
            for s in by_passage.get(r["passage_id"], []):
                inside.append({**s, "sid": f"S{len(inside) + 1}", "unit_id": r["unit_id"]})
        if inside:
            sections.append(Section(records, inside, {s["id"]: terms.get(s["id"], []) for s in inside}))
            placed += len(inside)
    return sections, len(statements) - placed


def build_messages(prompt: read.Prompt, vocab: Vocabulary, header: str, section: Section,
                   where: tuple[int, int]) -> list[dict]:
    parts = [header, f"Section {where[0]} of {where[1]}.", "Units:"]
    for r in section.records:
        place = read._where(r)
        parts.append(f"[{r['unit_id']}]{' (' + place + ')' if place else ''}\n{r['text']}")
    lines = ["Statements:"]
    for s in section.statements:
        lines.append(f"[{s['sid']}] ({s['unit_id']}) speaker: {s['speaker']}; stance: {s['stance']}\n"
                     f"\"{s['quote']}\"")
        found = section.terms.get(s["id"]) or []
        if found:
            lines.append("Lexicon terms in it: " + "; ".join(t["pref_label"] for t in found))
    parts.append("\n".join(lines))
    return [{"role": "system", "content": prompt.text + "\n\n" + vocab.text},
            {"role": "user", "content": "\n\n".join(parts)}]


# --------------------------------------------------------------------------- model calls

@dataclass
class SectionTags:
    section: Section
    data: dict
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


@dataclass
class SourceTags:
    source_id: str
    sections: list[SectionTags] = field(default_factory=list)


class Tagger:
    def __init__(self, conn: sqlite3.Connection, client, role, *, prompt: read.Prompt, vocab: Vocabulary,
                 log: Callable[[str], None] = lambda _: None):
        self.conn, self.client, self.role, self.prompt, self.vocab, self.log = conn, client, role, prompt, vocab, log
        self.clock = time.monotonic

    def _call(self, messages: list[dict], section: Section):
        extra = self.role.extra
        return self.client.chat_json(
            self.role.model, messages,
            schema=response_schema([s["sid"] for s in section.statements], [r["unit_id"] for r in section.records],
                                   self.vocab, PROMPT_FEATURES.get(self.prompt.version, frozenset())),
            temperature=self.role.temperature, num_predict=int(extra.get("num_predict", 4096)),
            num_ctx=extra.get("num_ctx"), think=extra.get("think", False), keep_alive=extra.get("keep_alive", 0))

    def tag_section(self, header: str, section: Section, where: tuple[int, int], *,
                    retried: bool = False) -> list[SectionTags]:
        try:
            result = self._call(build_messages(self.prompt, self.vocab, header, section, where), section)
        except Exception as exc:   # OllamaError, timeouts: retry once
            if retried:
                raise TagError(f"section {where[0]}: {exc}") from None
            return self.tag_section(header, section, where, retried=True)
        out = SectionTags(section, result.data if isinstance(result.data, dict) else {}, result.seconds,
                          result.prompt_tokens or 0, result.output_tokens or 0, 1)
        if result.truncated or not isinstance(result.data, dict):
            if len(section.statements) > 1:   # too many statements for one reply: two halves, same text
                half = len(section.statements) // 2
                parts = []
                for group in (section.statements[:half], section.statements[half:]):
                    renumbered = [{**s, "sid": f"S{i}"} for i, s in enumerate(group, 1)]
                    parts += self.tag_section(header, Section(section.records, renumbered, section.terms), where,
                                              retried=True)
                parts[0].seconds += out.seconds
                parts[0].calls += 1
                return parts
            if retried:
                raise TagError(f"section {where[0]}: " + ("reply cut off at the token cap" if result.truncated
                                                          else "reply was not JSON"))
            return self.tag_section(header, section, where, retried=True)
        return [out]

    def tag_source(self, source_id: str, sections: list[Section], ceiling: float,
                   on_section: Callable | None = None) -> SourceTags:
        header = read.source_header(self.conn, source_id)
        out = SourceTags(source_id)
        started = self.clock()
        for n, section in enumerate(sections, 1):
            if self.clock() - started > ceiling:
                raise TagError(f"stopped after {n - 1} of {len(sections)} sections: over the time ceiling "
                               f"of {ceiling:.0f} s")
            parts = self.tag_section(header, section, (n, len(sections)))
            out.sections += parts
            tagged = sum(len(p.data.get("statements") or {}) for p in parts)
            self.log(f"  section {n}/{len(sections)}: {tagged} statements, {sum(p.seconds for p in parts):.0f} s")
            if on_section:
                on_section(n, len(sections), sum(s.seconds for s in out.sections))
        return out


# --------------------------------------------------------------------------- writing records

def version_slice(conn: sqlite3.Connection, version_id: str, start: int, end: int) -> str | None:
    """The version's text between two offsets (its passages laid end to end), or None if not covered."""
    rows = conn.execute("SELECT char_start, char_end, text FROM passage WHERE version_id = ? AND char_end > ?"
                        " AND char_start < ? ORDER BY char_start", (version_id, start, end)).fetchall()
    if not rows or rows[0]["char_start"] > start or rows[-1]["char_end"] < end:
        return None
    pieces, at = [], rows[0]["char_start"]
    for r in rows:
        if r["char_start"] != at:   # a hole: passages are gap-free, so the text is not all there
            return None
        pieces.append(r["text"])
        at = r["char_end"]
    text = "".join(pieces)
    base = rows[0]["char_start"]
    return text[start - base:end - base]


def _passage_starts(conn: sqlite3.Connection, records: list[dict]) -> dict:
    ids = [r["passage_id"] for r in records]
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT id, char_start FROM passage WHERE id IN ({','.join('?' for _ in ids)})", ids)}


def _blank(value) -> str:
    return value.strip() if isinstance(value, str) else ""


GENERIC_SPEAKERS = {"", "unknown", "unnamed", "unnamed speaker", "anonymous", "speaker"}


def _speaker_key(speaker) -> str:
    key = normalize_label(_blank(speaker))
    return "" if key in GENERIC_SPEAKERS else key


def spread_actor_types(conn: sqlite3.Connection, run_id: str, statements: list[dict]) -> int:
    """A speaker's actor types, as this run found them stated, for all of their statements in the source.

    The text states a role once ("Justin … underwent six years of conversion practices"); taggers then give
    the type for the speaker's other statements without words, or not at all. Each type software located
    for a speaker (same label, not a generic one) is added to that speaker's other statements, citing the
    same words. Returns how many tags were added (caller commits).
    """
    by_speaker: dict = {}
    for s in statements:
        if _speaker_key(s.get("speaker")):
            by_speaker.setdefault(_speaker_key(s["speaker"]), []).append(s)
    found = conn.execute(
        """SELECT t.*, u.speaker, c.pref_label FROM tag t JOIN usage u ON u.id = t.usage_id
           JOIN concept c ON c.id = t.concept_id
           WHERE t.run_id = ? AND c.scheme = 'actor_type' AND t.cue IS NOT NULL ORDER BY t.created_at, t.rowid""",
        (run_id,)).fetchall()
    have = {(r[0], r[1]) for r in conn.execute(
        "SELECT usage_id, concept_id FROM tag WHERE run_id = ?", (run_id,))}
    first: dict = {}
    for t in found:
        first.setdefault((_speaker_key(t["speaker"]), t["concept_id"]), t)
    added = 0
    for (speaker, concept_id), t in first.items():
        for s in by_speaker.get(speaker, []):
            if (s["id"], concept_id) in have:
                continue
            have.add((s["id"], concept_id))
            row = {"id": f"tag_{uuid.uuid4().hex[:20]}", "usage_id": s["id"], "concept_id": concept_id,
                   "mode": "is", "cue_passage_id": t["cue_passage_id"], "cue_start": t["cue_start"],
                   "cue_end": t["cue_end"], "cue": t["cue"], "run_id": run_id, "created_at": utc_now()}
            conn.execute(f"INSERT INTO tag({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                         list(row.values()))
            record_decision(conn, actor_kind="model", actor_id=ACTOR_ID, run_id=run_id, action="propose",
                            target_table="tag", target_id=row["id"], quote=t["cue"],
                            reason=f"actor type '{t['pref_label']}' (is) on the statement by {s['speaker']}: "
                                   f"stated for this speaker elsewhere in the source ('{t['cue']}'), located by "
                                   "the same run; software applies it to each of their statements.")
            added += 1
    return added


def write_tags(conn: sqlite3.Connection, tagged: SourceTags, *, role, prompt: read.Prompt, vocab: Vocabulary,
               run_id: str) -> dict:
    """Insert the tags and excerpts of a finished tagging (caller commits)."""
    now = utc_now()
    counts: Counter = Counter()
    known_terms = {r[0] for r in conn.execute(
        "SELECT normalized_label FROM concept_label l JOIN concept c ON c.id = l.concept_id WHERE c.scheme = 'term'")}

    def decide(table, target_id, reason, quote=None):
        record_decision(conn, actor_kind="model", actor_id=role.model, run_id=run_id, action="propose",
                        target_table=table, target_id=target_id, quote=quote, reason=f"{prompt.version}: {reason}")

    def insert(table, row):
        row = {"run_id": run_id, "created_at": now, **row}
        conn.execute(f"INSERT INTO {table}({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                     list(row.values()))
        return row

    seen_tags: set = set()
    seen_assets: list = []
    for part in tagged.sections:
        section, data = part.section, part.data
        by_sid = {s["sid"]: s for s in section.statements}
        answers = data.get("statements") or {}
        for sid, answer in answers.items():
            s = by_sid.get(sid)
            if s is None or not isinstance(answer, dict):
                counts["unknown_statements"] += 1
                continue
            counts["statements_answered"] += 1

            def add_tag(concept_id, mode, cue, label, why):
                key = (s["id"], concept_id)
                if key in seen_tags:
                    counts["repeats_dropped"] += 1
                    return
                seen_tags.add(key)
                row = {"id": f"tag_{uuid.uuid4().hex[:20]}", "usage_id": s["id"], "concept_id": concept_id,
                       "mode": mode}
                if cue:
                    unit = next(r for r in section.records if r["unit_id"] == cue["unit_id"])
                    row.update(cue_passage_id=unit["passage_id"], cue_start=cue["start"], cue_end=cue["end"],
                               cue=cue["quote"])
                insert("tag", row)
                counts["tags"] += 1
                decide("tag", row["id"], f"{label} ({mode}) on the statement by {s['speaker']}; {why}.",
                       cue["quote"] if cue else s["quote"])

            stated = answer.get("actors") if isinstance(answer.get("actors"), list) else [
                {"type": answer.get("actor"), "words": answer.get("actor_words")}]
            for item in [x for x in stated if isinstance(x, dict) and _blank(x.get("type"))][:MAX_ACTORS]:
                actor = _blank(item.get("type"))
                if actor not in vocab.actors:
                    counts["actor_unknown"] += 1
                    continue
                found = locate_quote(_blank(item.get("words")), section.records, preferred_unit_id=s["unit_id"])
                if found is None:   # an actor type must be stated in the text
                    counts["actor_unlocated"] += 1
                    continue
                add_tag(vocab.actors[actor], "is", found, f"actor type '{actor}'",
                        "the words that state it located by software")
            listed = {normalize_label(t["pref_label"]): t for t in section.terms.get(s["id"]) or []}
            listed.update({normalize_label(t["quote"]): t for t in section.terms.get(s["id"]) or []})
            for t in answer.get("terms") or []:
                if not isinstance(t, dict) or t.get("mode") not in MODES:
                    continue
                match = listed.get(normalize_label(_blank(t.get("term"))))
                if match is None:
                    counts["terms_not_listed"] += 1
                    continue
                unit = next((r for r in section.records if r["passage_id"] == match["passage_id"]), None)
                cue = ({"unit_id": unit["unit_id"], "start": match["char_start"], "end": match["char_end"],
                        "quote": match["quote"]} if unit else None)
                add_tag(match["concept_id"], t["mode"], cue, f"term '{match['pref_label']}'",
                        "the term was matched by software")
            for t in answer.get("tags") or []:
                if not isinstance(t, dict) or t.get("tag") not in vocab.tags or t.get("mode") not in MODES:
                    counts["tags_invalid"] += 1
                    continue
                cue_text = _blank(t.get("cue"))
                found = locate_quote(cue_text, section.records, preferred_unit_id=s["unit_id"]) if cue_text else None
                if cue_text and found is None:
                    counts["cues_unlocated"] += 1
                add_tag(vocab.tags[t["tag"]], t["mode"], found, t["tag"],
                        "cue located by software" if found else "no cue; the statement's quote is located")

        starts = _passage_starts(conn, section.records)
        version_of = {r["unit_id"]: r["version_id"] for r in section.records}
        for e in data.get("excerpts") or []:
            if not isinstance(e, dict) or e.get("kind") not in EXCERPT_KINDS:
                continue
            first = locate_quote(_blank(e.get("first_words")), section.records, preferred_unit_id=e.get("unit"))
            last = locate_quote(_blank(e.get("last_words")), section.records, preferred_unit_id=e.get("unit"))
            if first is None or last is None or version_of[first["unit_id"]] != version_of[last["unit_id"]]:
                counts["excerpts_unlocated"] += 1
                continue
            unit_first = next(r for r in section.records if r["unit_id"] == first["unit_id"])
            unit_last = next(r for r in section.records if r["unit_id"] == last["unit_id"])
            start = starts[unit_first["passage_id"]] + first["start"]
            end = starts[unit_last["passage_id"]] + last["end"]
            if end <= start or end - start > MAX_EXCERPT_CHARS:
                counts["excerpts_bad_span"] += 1
                continue
            version_id = version_of[first["unit_id"]]
            text = version_slice(conn, version_id, start, end)
            if text is None:
                counts["excerpts_bad_span"] += 1
                continue
            if any(k == e["kind"] and v == version_id and start < b and a < end for k, v, a, b in seen_assets):
                counts["repeats_dropped"] += 1
                continue
            seen_assets.append((e["kind"], version_id, start, end))
            voices = Counter(s["speaker"] for s in section.statements
                             if s["version_id"] == version_id
                             and start <= s["passage_start"] + s["char_start"] < end)
            row = insert("asset", {"id": f"ast_{uuid.uuid4().hex[:20]}", "kind": e["kind"],
                                   "source_id": tagged.source_id, "version_id": version_id,
                                   "passage_id": unit_first["passage_id"], "char_start": start, "char_end": end,
                                   "text": text, "speaker": voices.most_common(1)[0][0] if voices else None})
            counts["excerpts"] += 1
            decide("asset", row["id"], f"Excerpt kept whole: {e['kind']} ({len(text)} characters); both ends "
                                       "located by software.", text)
        for t in data.get("new_terms") or []:
            if not isinstance(t, dict):
                continue
            term = _blank(t.get("term"))
            if normalize_label(term) in known_terms:
                counts["new_terms_known"] += 1
                continue
            found = locate_quote(_blank(t.get("quote")), section.records, preferred_unit_id=t.get("unit"))
            hit = (re.search(re.escape(term), found["quote"], re.IGNORECASE)
                   if found and len(term) >= 3 else None)   # offsets in the text itself, never a casefolded copy
            if hit is None:
                counts["new_terms_unlocated"] += 1
                continue
            unit = next(r for r in section.records if r["unit_id"] == found["unit_id"])
            start = starts[unit["passage_id"]] + found["start"] + hit.start()
            label = hit.group(0)
            if any(k == "new_term" and normalize_label(v) == normalize_label(label) for k, v, _a, _b in seen_assets):
                counts["repeats_dropped"] += 1
                continue
            seen_assets.append(("new_term", label, start, start + len(label)))
            row = insert("asset", {"id": f"ast_{uuid.uuid4().hex[:20]}", "kind": "new_term",
                                   "source_id": tagged.source_id, "version_id": unit["version_id"],
                                   "passage_id": unit["passage_id"], "char_start": start,
                                   "char_end": start + len(label), "text": label, "label": label})
            counts["new_terms"] += 1
            decide("asset", row["id"], f"New term candidate '{label}', not in the lexicon; located by software "
                                       f"in: {found['quote']}", found["quote"])
    spread = spread_actor_types(conn, run_id, [s for part in tagged.sections for s in part.section.statements])
    if spread:
        counts["actor_spread"] = spread
        counts["tags"] += spread
    return dict(counts)


def supersede_earlier(conn: sqlite3.Connection, source_id: str, new_run_id: str, *, model: str) -> int:
    """Mark this model's earlier tagging of the source superseded (one recorded decision per run)."""
    runs = [r[0] for r in conn.execute(
        """SELECT id FROM run WHERE role = 'extraction' AND outcome = 'succeeded' AND id <> ? AND model = ?
           AND json_extract(inputs, '$.job') = ? AND json_extract(inputs, '$.source_id') = ?
           AND id NOT IN (SELECT target_id FROM decision WHERE target_table = 'run' AND action = 'supersede')""",
        (new_run_id, model, JOB, source_id))]
    for old in runs:
        conn.execute("UPDATE tag SET review_state = 'superseded' WHERE run_id = ?", (old,))
        conn.execute("UPDATE asset SET review_state = 'superseded' WHERE run_id = ?", (old,))
        record_decision(conn, actor_kind="model", actor_id=model, run_id=new_run_id, action="supersede",
                        target_table="run", target_id=old,
                        reason=f"Source {source_id} tagged again by run {new_run_id}; earlier tags superseded.",
                        previous_state={"review_state": "proposed"},
                        new_state={"review_state": "superseded", "superseded_by": new_run_id})
    return len(runs)


# --------------------------------------------------------------------------- batch

def already_tagged(conn: sqlite3.Connection, source_id: str, model: str, inputs: dict) -> bool:
    return conn.execute(
        """SELECT 1 FROM run WHERE role = 'extraction' AND outcome = 'succeeded' AND model = ?
           AND json_extract(inputs, '$.job') = ? AND json_extract(inputs, '$.source_id') = ?
           AND json_extract(inputs, '$.prompt_sha256') = ? AND json_extract(inputs, '$.vocabulary_sha256') = ?
           AND json_extract(inputs, '$.reading_runs') = json(?)""",
        (model, JOB, source_id, inputs["prompt_sha256"], inputs["vocabulary_sha256"],
         json.dumps(inputs["reading_runs"]))).fetchone() is not None


def tag_sources(conn: sqlite3.Connection, client, role, source_ids: list[str], *, primary_model: str,
                prompt: read.Prompt | None = None, again: bool = False, chunk_chars: int = read.DEFAULT_CHUNK_CHARS,
                log: Callable[[str], None] = lambda _: None) -> list[dict]:
    """Tag each source's current statements with one model. Failed sources are marked, the batch goes on."""
    conn.row_factory = sqlite3.Row
    prompt = prompt or read.load_prompt(DEFAULT_PROMPT)
    with conn:
        seed_lists(conn)
    vocab = vocabulary(conn, prompt.version)
    tagger = Tagger(conn, client, role, prompt=prompt, vocab=vocab, log=log)
    furniture = read.furniture_hashes(conn)
    results, done = [], set()
    try:
        for requested in source_ids:
            source_id = read.keeper(conn, requested)
            if source_id in done:
                continue
            done.add(source_id)
            statements, reading_runs = statements_for(conn, source_id, primary_model=primary_model)
            if not statements:
                results.append({"source_id": source_id, "outcome": "skipped", "why": "no statements to tag"})
                continue
            terms = terms_in(conn, statements)
            sections, unplaced = sections_for(conn, source_id, statements, terms, chunk_chars=chunk_chars,
                                              furniture=furniture)
            chars = sum(len(r["text"]) for s in sections for r in s.records)
            inputs = {"job": JOB, "source_id": source_id, "requested": requested, "prompt_sha256": prompt.sha256,
                      "vocabulary_sha256": vocab.sha256, "rule": RULE, "reading_runs": sorted(reading_runs),
                      "statements": len(statements) - unplaced, "statements_not_in_sections": unplaced,
                      "sections": len(sections), "chars": chars}
            if not again and already_tagged(conn, source_id, role.model, inputs):
                results.append({"source_id": source_id, "outcome": "skipped", "why": "already tagged"})
                continue
            run_id = f"run_{uuid.uuid4().hex}"
            with conn:
                conn.execute("INSERT INTO run(id, role, model, prompt_version, inputs, started_at, heartbeat_at,"
                             " progress) VALUES (?, 'extraction', ?, ?, ?, ?, ?, ?)",
                             (run_id, role.model, prompt.version, canonical_json(inputs), utc_now(), utc_now(),
                              canonical_json({"sections_done": 0, "sections": len(sections)})))
            log(f"{source_id}: {inputs['statements']} statements in {len(sections)} sections")
            budget = float(role.extra.get("seconds_per_1000_chars", DEFAULT_SECONDS_PER_1000_CHARS))
            ceiling = max(read.MIN_CEILING_SECONDS, read.CEILING_FACTOR * budget * chars / 1000)
            started = time.monotonic()
            try:
                tagged = tagger.tag_source(source_id, sections, ceiling, on_section=lambda n, total, secs:
                                           read._heartbeat(conn, run_id, n, total, secs))
            except Exception as exc:   # nothing from this source is written; the batch goes on
                with conn:
                    conn.execute("UPDATE run SET outcome = 'failed', error = ?, seconds = ?, finished_at = ?"
                                 " WHERE id = ?", (str(exc)[:1000], round(time.monotonic() - started, 1),
                                                   utc_now(), run_id))
                results.append({"source_id": source_id, "run_id": run_id, "outcome": "failed", "error": str(exc)})
                log(f"  failed: {exc}")
                continue
            seconds = sum(s.seconds for s in tagged.sections)
            with conn:
                counts = write_tags(conn, tagged, role=role, prompt=prompt, vocab=vocab, run_id=run_id)
                superseded = supersede_earlier(conn, source_id, run_id, model=role.model)
                conn.execute("UPDATE run SET outcome = 'succeeded', seconds = ?, input_tokens = ?, output_tokens = ?,"
                             " inputs = ?, finished_at = ? WHERE id = ?",
                             (round(seconds, 1), sum(s.input_tokens for s in tagged.sections),
                              sum(s.output_tokens for s in tagged.sections),
                              canonical_json({**inputs, "calls": sum(s.calls for s in tagged.sections),
                                              "wall_seconds": round(time.monotonic() - started, 1),
                                              "counts": counts}), utc_now(), run_id))
            results.append({"source_id": source_id, "run_id": run_id, "outcome": "succeeded",
                            "seconds": round(seconds, 1), "superseded_runs": superseded, **counts})
            log(f"  done in {seconds:.0f} s: {counts.get('tags', 0)} tags, {counts.get('excerpts', 0)} excerpts, "
                f"{counts.get('new_terms', 0)} new terms")
    finally:   # one resident model at a time
        try:
            client.unload(role.model)
        except Exception:
            pass
    return results


# --------------------------------------------------------------------------- comparing the two taggers

def current_taggings(conn: sqlite3.Connection, source_id: str, models: list[str]) -> list[sqlite3.Row]:
    """The latest succeeded, not superseded tagging run of each model."""
    out = []
    for model in models:
        row = conn.execute(
            """SELECT * FROM run r WHERE role = 'extraction' AND outcome = 'succeeded' AND model = ?
               AND json_extract(inputs, '$.job') = ? AND json_extract(inputs, '$.source_id') = ?
               AND NOT EXISTS (SELECT 1 FROM decision d WHERE d.target_table = 'run' AND d.target_id = r.id
                               AND d.action = 'supersede')
               ORDER BY started_at DESC LIMIT 1""", (model, JOB, source_id)).fetchone()
        if row:
            out.append(row)
    return out


def _last_compare(conn, table, record_id):
    from .reconcile import _effective_last
    last = _effective_last(conn, table, record_id)
    return last if last and last["actor_id"].startswith(ACTOR_PREFIX) and last["action"] in ("merge", "dispute") else None


def _settled(conn, table, record_id) -> bool:
    """A reviewer or the researcher has ruled on it: comparing must not change it."""
    from .reconcile import _effective_last
    last = _effective_last(conn, table, record_id, skip_audits=True)
    return bool(last) and last["action"] != "propose" and not last["actor_id"].startswith(ACTOR_PREFIX)


def _overlap(a, b) -> float:
    if a["version_id"] != b["version_id"]:
        return 0.0
    shared = min(a["char_end"], b["char_end"]) - max(a["char_start"], b["char_start"])
    shortest = min(a["char_end"] - a["char_start"], b["char_end"] - b["char_start"])
    return shared / shortest if shared > 0 and shortest > 0 else 0.0


def compare_source(conn: sqlite3.Connection, source_id: str, *, models: list[str], run_id: str) -> Counter:
    runs = current_taggings(conn, source_id, models)
    counts: Counter = Counter()
    conn.execute("UPDATE assessment SET current = 0 WHERE source_id = ? AND target_table IN ('tag', 'asset')",
                 (source_id,))
    if not runs:
        return counts
    readers = [{"model": r["model"], "run_id": r["id"], "prompt": r["prompt_version"],
                "tagged_at": r["finished_at"] or r["started_at"]} for r in runs]
    rows = {}
    for table in ("tag", "asset"):
        rows[table] = {}
        for r in runs:
            records = [dict(x) for x in conn.execute(f"SELECT * FROM {table} WHERE run_id = ?", (r["id"],))]
            for x in records:   # undo what an earlier comparison set (rulings stay)
                last = _last_compare(conn, table, x["id"])
                if last and not _settled(conn, table, x["id"]):
                    x["review_state"] = json.loads(last["previous_state"] or "{}").get("review_state", "proposed")
                    conn.execute(f"UPDATE {table} SET review_state = ? WHERE id = ?", (x["review_state"], x["id"]))
            rows[table][r["model"]] = [x for x in records if x["review_state"] not in ("rejected", "superseded")]
    now = utc_now()

    def assess(table, record, confidence, *, counterpart=None, proposed_by, differences=None):
        conn.execute("INSERT INTO assessment(id, run_id, source_id, target_table, target_id, counterpart_id, confidence,"
                     " readers, proposed_by, differences, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (f"asm_{uuid.uuid4().hex[:20]}", run_id, source_id, table, record["id"],
                      counterpart and counterpart["id"], confidence, canonical_json(readers),
                      canonical_json(sorted(proposed_by)), canonical_json(differences) if differences else None, now))
        counts[f"{table}:{confidence}"] += 1

    def set_state(table, record, state, action, reason, *, verdict="not_audited", extra=None):
        if _settled(conn, table, record["id"]):
            counts["kept_reviewed"] += 1
            return
        conn.execute(f"UPDATE {table} SET review_state = ? WHERE id = ?", (state, record["id"]))
        record_decision(conn, actor_kind="model", actor_id=ACTOR_ID, run_id=run_id, action=action,
                        target_table=table, target_id=record["id"], reason=reason, audit_verdict=verdict,
                        quote=record.get("cue") or record.get("text"),
                        previous_state={"review_state": record["review_state"]},
                        new_state={"review_state": state, **(extra or {})})

    if len(runs) == 1:
        only = runs[0]["model"]
        for table in ("tag", "asset"):
            for x in rows[table][only]:
                assess(table, x, "single_reading", proposed_by=[only])
        return counts
    first, second = runs[0]["model"], runs[1]["model"]
    both = [first, second]
    # tags: both taggers saw the same statements, so a tag is the pair (statement, concept)
    a = {(x["usage_id"], x["concept_id"]): x for x in rows["tag"][first]}
    b = {(x["usage_id"], x["concept_id"]): x for x in rows["tag"][second]}
    for key in sorted(set(a) | set(b)):
        x, y = a.get(key), b.get(key)
        if x and y and x["mode"] == y["mode"]:
            assess("tag", x, "two_readers_agree", counterpart=y, proposed_by=both)
            assess("tag", y, "two_readers_agree", counterpart=x, proposed_by=both)
            record_decision(conn, actor_kind="model", actor_id=ACTOR_ID, run_id=run_id, action="audit",
                            target_table="tag", target_id=x["id"], audit_verdict="pass", quote=x.get("cue"),
                            reason=f"Both taggers ({', '.join(both)}) gave this tag with mode '{x['mode']}'.")
            set_state("tag", y, "superseded", "merge", f"Same tag as {x['id']} from the other tagger.",
                      extra={"merged_into": x["id"]})
        elif x and y:
            for record, other in ((x, y), (y, x)):
                assess("tag", record, "readers_disagree", counterpart=other, proposed_by=both,
                       differences={"mode": [x["mode"], y["mode"]]})
                set_state("tag", record, "disputed", "dispute",
                          f"The two taggers disagree on the mode: '{x['mode']}' vs '{y['mode']}'.",
                          verdict="fail", extra={"counterpart": other["id"]})
        else:
            only = x or y
            assess("tag", only, "one_reader", proposed_by=[first if x else second])
    # excerpts: one kind, paired by overlap; new terms by their label
    left = list(rows["asset"][second])
    for x in rows["asset"][first]:
        if x["kind"] == "new_term":
            y = next((y for y in left if y["kind"] == "new_term"
                      and normalize_label(y["label"] or "") == normalize_label(x["label"] or "")), None)
        else:
            y = max((y for y in left if y["kind"] == x["kind"] and _overlap(x, y) >= MIN_OVERLAP),
                    key=lambda y: _overlap(x, y), default=None)
        if y is None:
            assess("asset", x, "one_reader", proposed_by=[first])
            continue
        left.remove(y)
        differences = ({"span": [[x["char_start"], x["char_end"]], [y["char_start"], y["char_end"]]]}
                       if (x["char_start"], x["char_end"]) != (y["char_start"], y["char_end"]) else None)
        assess("asset", x, "two_readers_agree", counterpart=y, proposed_by=both, differences=differences)
        assess("asset", y, "two_readers_agree", counterpart=x, proposed_by=both, differences=differences)
        record_decision(conn, actor_kind="model", actor_id=ACTOR_ID, run_id=run_id, action="audit",
                        target_table="asset", target_id=x["id"], audit_verdict="pass", quote=x["text"],
                        reason=f"Both taggers ({', '.join(both)}) marked this {x['kind']}"
                               + (" (their spans differ; the first tagger's is kept)." if differences else "."))
        set_state("asset", y, "superseded", "merge", f"Same {x['kind']} as {x['id']} from the other tagger.",
                  extra={"merged_into": x["id"]})
    for y in left:
        assess("asset", y, "one_reader", proposed_by=[second])
    return counts


def compare(conn: sqlite3.Connection, source_ids: list[str], *, primary_model: str, second_model: str) -> dict:
    run_id = f"run_{uuid.uuid4().hex}"
    totals: Counter = Counter()
    models = [primary_model, second_model]
    with conn:
        conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                     (run_id, canonical_json({"job": "compare tags", "rule": RULE, "models": models,
                                              "sources": len(source_ids)}), utc_now()))
        for sid in source_ids:
            totals += compare_source(conn, sid, models=models, run_id=run_id)
        conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ? WHERE id = ?", (utc_now(), run_id))
    return {"run_id": run_id, "sources": len(source_ids), "counts": dict(sorted(totals.items()))}
