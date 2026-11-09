"""Load the seed vocabularies into concepts, labels, relations and entities.

Sources (``seeds/``), in load order:

Load order: registry, tactics/practices/functions, lexicon, GOV.UK, glossary
(so that Lexicon cross-references to tactics and practices resolve).

1. ``Entity_Registry_v1.1.md``  organisations, persons, laws, events (with
   acronyms, former names); state 'proposed' (the registry says unverified).
2. ``SOGICE_Lexicon_v2.1.md``    the concept backbone (scheme 'term'):
   sections 1-9 entries (cluster in ``notation``: C1-C7, slur, reference),
   their "Related" cross-references (targets that have no entry become
   proposed placeholder concepts), section 10 acronyms (alt labels, or entity
   variants for organisations), section 11 translations. Section 13 is
   superseded and ignored. State 'confirmed' (researcher's own lexicon).
3. ``sanity_seed_2026-05-05.json`` the 44-entry tactic tree (with parents)
   and 14 practices; ``SOGICE_Ontology_v3.0.md`` the function tags.
4. ``govuk_conversion_therapy_glossary_2021.json``  external scheme, linked
   with exactMatch (missing terms become reference concepts).
5. ``sogice_glossary_2026-04-03.json`` 210 discovered terms, through
   ``merge_map_2026-11-09.json``: listed merges are model decisions (recorded,
   undoable); unlisted terms attach when their wording equals a label,
   otherwise become proposed concepts (definition_source 'model').

``seeds/quarantine/`` is never read. The old Sanity lexicon and organisation
lists are not used (parser leftovers, duplicate actors, "Network:" pseudo
organisations).

Deterministic ids (``term:<slug>``, ``person:<slug>``...) make the load
idempotent: running it again adds nothing and records no new decisions.
Cross-references are resolved last, when every label is known.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .db import canonical_json, record_decision, utc_now
from .urls import short_hash

SEEDS = Path(__file__).resolve().parent.parent / "seeds"
LANGUAGE_NAMES = {
    "english": "en", "norwegian": "no", "italian": "it", "french": "fr", "german": "de",
    "spanish": "es", "spanish legal": "es", "polish": "pl", "finnish": "fi", "swedish": "sv",
    "hungarian": "hu", "greek": "el", "maltese": "mt", "portuguese": "pt", "romanian": "ro",
    "russian": "ru", "danish": "da", "dutch": "nl",
}
LANGUAGE_CODES = {"IT": "it", "NO": "no", "RO": "ro", "PL": "pl", "DE": "de", "HU": "hu", "FR": "fr",
                  "ES": "es", "PT": "pt", "EL": "el", "MT": "mt", "NL": "nl", "DA": "da", "SV": "sv",
                  "FI": "fi", "RU": "ru"}
COUNTRIES = {
    "usa": "US", "us": "US", "united states": "US", "uk": "GB", "united kingdom": "GB", "england": "GB",
    "scotland": "GB-SCT", "wales": "GB-WLS", "norway": "NO", "sweden": "SE", "denmark": "DK",
    "finland": "FI", "iceland": "IS", "germany": "DE", "france": "FR", "belgium": "BE",
    "netherlands": "NL", "spain": "ES", "portugal": "PT", "italy": "IT", "malta": "MT", "cyprus": "CY",
    "greece": "GR", "poland": "PL", "hungary": "HU", "romania": "RO", "moldova": "MD",
    "switzerland": "CH", "austria": "AT", "ireland": "IE", "canada": "CA", "australia": "AU",
    "brazil": "BR", "indonesia": "ID", "south africa": "ZA", "kenya": "KE", "nigeria": "NG",
}
ORG_CONTEXT = ("body", "org", "party", "advocacy", "standards", "monitoring", "association")
_DASHES = {ord(c): "-" for c in "‐‑‒–—―−"}
_QUOTES = {**{ord(c): "'" for c in "‘’‚‛`´"}, **{ord(c): '"' for c in "“”„‟«»"}}
_ACRONYM = re.compile(r"[A-ZÀ-ÖØ-ÞΑ-ΩА-Я0-9][A-ZÀ-ÖØ-ÞΑ-ΩА-Я0-9\-.&]{1,9}")


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------

def normalize_label(text: str) -> str:
    """Comparison form: NFKC, casefold, dashes and quotes folded, hyphens as spaces."""
    text = unicodedata.normalize("NFKC", text or "").translate(_DASHES).translate(_QUOTES).casefold()
    text = re.sub(r"[-_]+", " ", text.replace('"', " "))
    text = re.sub(r"^[\s\"'.,;:]+|[\s\"'.,;:]+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def slug(text: str) -> str:
    return re.sub(r"[^\w]+", "-", normalize_label(text)).strip("-") or short_hash(text, 8)


def clean_label(text: str) -> str:
    text = re.sub(r"\*\*|`|†", "", text or "")
    text = text.strip().strip("\"'“”‘’«»").strip()
    text = re.sub(r"\s+", " ", text)
    return text.rstrip(".,;:").strip()


def is_acronym(text: str) -> bool:
    text = text.strip()
    return bool(_ACRONYM.fullmatch(text)) and sum(ch.isupper() for ch in text) >= 2


NOT_LABELS = {"UK", "US", "USA", "EU", "UN"}


def expands(acronym: str, phrase: str) -> bool:
    """Do the phrase's word initials spell the acronym (in order)?"""
    letters = [ch for ch in acronym.upper() if ch.isalpha()]
    initials = [w[0].upper() for w in re.split(r"[\s\-]+", phrase) if w and w[0].isalpha()]
    position = 0
    for initial in initials:
        if position < len(letters) and initial == letters[position]:
            position += 1
    return bool(letters) and position == len(letters)


def split_labels(text: str) -> tuple[list[str], str | None]:
    """'SSA / USSA (Expansion)' -> labels, and a language code from '(IT)'.

    Parentheticals become labels when they are an acronym or expand an
    acronym; '(IT)' sets the language; others ('(legislative loophole)') are
    qualifiers and are dropped.
    """
    language = None
    labels: list[str] = []
    for part in re.split(r"\s+/\s+", text):
        extras = []
        for paren in re.findall(r"\(([^()]*)\)", part):
            inner = paren.strip()
            main = clean_label(re.sub(r"\([^()]*\)", "", part))
            if inner in LANGUAGE_CODES:
                language = LANGUAGE_CODES[inner]
            elif inner in NOT_LABELS:
                continue
            elif (is_acronym(inner) and expands(inner, main)) or (is_acronym(main) and expands(main, inner)):
                extras.append(clean_label(inner))
        main = clean_label(re.sub(r"\([^()]*\)", "", part))
        if main:
            labels.append(main)
        labels.extend(e for e in extras if e)
    seen, out = set(), []
    for label in labels:
        key = normalize_label(label)
        if key and key not in seen:
            seen.add(key)
            out.append(label)
    return out, language


def parse_glossary_term(term: str) -> list[str]:
    """Labels in an April glossary term: 'SSA – x / y', 'elektrošok (ES) / ...'."""
    labels: list[str] = []
    for side in re.split(r"\s+[–—]\s+", term):
        acronyms = [clean_label(p) for p in re.findall(r"\(([^()]*)\)", side)
                    if is_acronym(p.strip()) and p.strip() not in NOT_LABELS]
        # Brackets go first: '(ex-gay / ex-trans)' must not be split by its slash.
        for part in re.split(r"\s+/\s+", re.sub(r"\s*\([^()]*\)", "", side)):
            main = clean_label(part)
            if main:
                labels.append(main)
        labels.extend(acronyms)
    seen, out = set(), []
    for label in labels:
        key = normalize_label(label)
        if key and key not in seen:
            seen.add(key)
            out.append(label)
    return out


# ---------------------------------------------------------------------------
# Parsers (pure)
# ---------------------------------------------------------------------------

@dataclass
class LexiconEntry:
    labels: list
    language: str
    section: int
    notation: str
    definition: str | None = None
    function: str | None = None
    related: list = field(default_factory=list)


def _section_notation(number: int) -> str:
    return f"C{number}" if 1 <= number <= 7 else {8: "slur", 9: "reference"}.get(number, f"S{number}")


def parse_lexicon(text: str) -> dict:
    lines = text.splitlines()
    entries: list[LexiconEntry] = []
    acronyms: list[tuple[str, str, str]] = []
    table_rows: list[dict] = []
    language_terms: list[tuple[str, str]] = []
    section = 0
    i = 0
    header_languages: list[str] = []
    while i < len(lines):
        line = lines[i]
        heading = re.match(r"^## SECTION (\d+)", line)
        if heading:
            section = int(heading.group(1))
            i += 1
            continue
        if section in (12, 13) or section == 0:
            i += 1
            continue
        if section == 10:
            row = [c.strip() for c in line.strip().strip("|").split("|")]
            if line.startswith("|") and len(row) == 3 and row[0] not in ("Acronym",) and not row[0].startswith("---"):
                acronyms.append((row[0], row[1], row[2]))
            i += 1
            continue
        if section == 11:
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if cells and cells[0] == "English":
                    header_languages = [LANGUAGE_NAMES.get(c.lower()) for c in cells]
                elif header_languages and cells and not cells[0].startswith("---"):
                    table_rows.append({lang: cell for lang, cell in zip(header_languages, cells) if lang})
            else:
                match = re.match(r"^\*\*([A-Za-z ]+):\*\*\s*(.+)$", line)
                if match and match.group(1).lower() in LANGUAGE_NAMES:
                    language = LANGUAGE_NAMES[match.group(1).lower()]
                    body = match.group(2)
                    if "`" in body:
                        items = re.findall(r"`([^`]+)`", body)
                    else:
                        items = []
                        for chunk in body.split(" · "):
                            item = re.split(r"\s+\(|\s+—\s+|\s+-\s+", chunk.strip(), maxsplit=1)[0]
                            items.extend(p for p in re.split(r"\s+/\s+", item) if p)
                    for item in items:
                        label = clean_label(item)
                        if label and len(label) <= 120:
                            language_terms.append((label, language))
            i += 1
            continue

        bold = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", line)
        if not bold:
            i += 1
            continue
        name, rest = bold.group(1), bold.group(2)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if nxt.startswith("- Cluster") or nxt.startswith("- Definition"):
            labels, language = split_labels(f"{name} {rest}".strip() if rest.startswith("(") else name)
            entry = LexiconEntry(labels, language or "en", section, _section_notation(section))
            j = i + 1
            while j < len(lines) and lines[j].startswith("- "):
                field_line = lines[j][2:]
                key, _, value = field_line.partition(":")
                key = key.strip().lower()
                if key == "definition":
                    entry.definition = value.strip()
                elif key == "cluster":
                    entry.function = (value.split("Function:", 1)[1].strip()
                                      if "Function:" in value else None)
                elif key == "related":
                    entry.related = [clean_label(re.sub(r"\([^()]*\)", "", r))
                                     for r in re.findall(r"→\s*([^,→]+)", value)]
                    entry.related = [r for r in entry.related if r]
                j += 1
            entries.append(entry)
            i = j
            continue
        dash = re.match(r"^(\([^)]*\)\s*)?—\s*(.*)$", rest)
        if dash and section in (8, 9):
            labels, language = split_labels(name)
            body = dash.group(2).strip()
            meta, definition = None, body
            first, _, remainder = body.partition(". ")
            if " | " in first:
                meta, definition = first, remainder.strip() or None
            if language is None and definition and re.search(r"\bSpanish\b", definition):
                language = "es"
            entries.append(LexiconEntry(labels, language or "en", section, _section_notation(section),
                                        definition, meta.split("|")[-1].strip() if meta else None))
        i += 1
    return {"entries": entries, "acronyms": acronyms, "translations": table_rows,
            "language_terms": language_terms}


@dataclass
class RegistryEntry:
    kind: str
    labels: list
    description: str | None = None
    country: str | None = None
    former: list = field(default_factory=list)       # (name, from, to)
    start: str | None = None
    end: str | None = None


def _country(value: str | None) -> str | None:
    if not value:
        return None
    first = re.split(r"[,/;(]| and ", value)[0].strip().lower()
    return COUNTRIES.get(first)


def _year(value: str | None) -> str | None:
    match = re.search(r"\b(1[89]\d\d|20\d\d)\b", value or "")
    return match.group(1) if match else None


def parse_registry(text: str) -> list[RegistryEntry]:
    lines = text.splitlines()
    kind = None
    entries = []
    for i, line in enumerate(lines):
        if line.startswith("## PART 1"):
            kind = "organization"
        elif line.startswith("## PART 2"):
            kind = "person"
        elif line.startswith("## PART 3"):
            kind = "law"
        elif line.startswith("## PART 4"):
            kind = "event"
        elif line.startswith("## APPENDIX —"):
            kind = None
        elif line.startswith("### New Laws"):
            kind = "law"
        elif line.startswith("### New Events"):
            kind = "event"
        elif line.startswith("### Updated"):
            kind = None
        bold = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", line)
        if not (kind and bold and i + 1 < len(lines) and lines[i + 1].startswith("- ")):
            continue
        name = f"{bold.group(1)} {bold.group(2)}".strip()
        fields = {}
        j = i + 1
        while j < len(lines) and lines[j].startswith("- "):
            key, _, value = lines[j][2:].partition(":")
            fields[key.strip().lower()] = value.strip()
            j += 1
        labels = []
        for label in split_labels(name)[0]:
            label = re.sub(r"\s*\((?:\d{4}|legacy/dissolved|UK)\)\s*$", "", label).strip()
            titled = re.match(r"^(Dr|Fr|Rev|Prof)\.\s+(.+)$", label)
            if titled:
                labels.append(titled.group(2))
            labels.append(label)
        seen, unique = set(), []
        for label in labels:
            if normalize_label(label) not in seen:
                seen.add(normalize_label(label))
                unique.append(label)
        former = [(m.group(1), m.group(2), m.group(3)) for m in re.finditer(
            r'name:\s*"([^"]+)"(?:,\s*from_year:\s*(\d{4}))?(?:,\s*to_year:\s*(\d{4}))?',
            fields.get("former names", ""))]
        start = _year(fields.get("founded") or fields.get("date") or fields.get("year in force")
                      or fields.get("year"))
        entries.append(RegistryEntry(kind, unique, fields.get("description") or fields.get("note"),
                                     _country(fields.get("country") or fields.get("jurisdiction")),
                                     former, start, _year(fields.get("dissolved"))))
    return entries


def parse_functions(ontology: str) -> list[str]:
    match = re.search(r"### Function Tags\s+(.+?)\n\n", ontology, re.DOTALL)
    return re.findall(r"`Function: ([^`]+)`", match.group(1)) if match else []


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class SeedLoader:
    def __init__(self, conn: sqlite3.Connection, *, run_id: str, now: str | None = None):
        self.conn, self.run_id, self.now = conn, run_id, now or utc_now()
        self.index: dict[str, set] = defaultdict(set)        # normalized label -> concept ids
        self.entity_index: dict[str, set] = defaultdict(set)
        self.stats: Counter = Counter()
        self.notes: dict[str, list] = defaultdict(list)
        self.pending_related: list = []
        for row in conn.execute("SELECT concept_id, normalized_label FROM concept_label"):
            self.index[row[1]].add(row[0])
        for row in conn.execute("SELECT entity_id, normalized_label FROM entity_variant"):
            self.entity_index[row[1]].add(row[0])

    # -- concepts -------------------------------------------------------------

    def concept(self, concept_id: str, scheme: str, pref: str, *, language: str = "en",
                definition: str | None = None, definition_source: str | None = None,
                notation: str | None = None, broader: str | None = None,
                review_state: str = "proposed") -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO concept(id, scheme, pref_label, notation, definition, definition_source,"
            " broader, created_at, review_state) VALUES (?,?,?,?,?,?,?,?,?)",
            (concept_id, scheme, pref, notation, definition or None,
             (definition_source or "seed") if definition else None, broader, self.now, review_state))
        created = cursor.rowcount == 1
        if created:
            self.stats[f"concepts_{scheme}"] += 1
        self.label(concept_id, pref, language, "pref", review_state=review_state)
        return created

    def label(self, concept_id: str, text: str, language: str, kind: str = "alt",
              review_state: str = "proposed") -> str | None:
        text = clean_label(text)
        normalized = normalize_label(text)
        if not normalized:
            return None
        if concept_id in self.index.get(normalized, ()):
            return None                      # this wording is already a label of the concept
        if kind == "pref" and self.conn.execute(
                "SELECT 1 FROM concept_label WHERE concept_id = ? AND language = ? AND kind = 'pref'",
                (concept_id, language)).fetchone():
            kind = "alt"
        label_id = "lab_" + short_hash(f"{concept_id}|{normalized}|{language}|{kind}")
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO concept_label(id, concept_id, label, normalized_label, language, kind,"
            " created_at, review_state) VALUES (?,?,?,?,?,?,?,?)",
            (label_id, concept_id, text, normalized, language, kind, self.now, review_state))
        self.index[normalized].add(concept_id)
        if cursor.rowcount == 1:
            self.stats["labels"] += 1
            return label_id
        return None

    def find(self, text: str, scheme: str | None = "term") -> set:
        ids = self.index.get(normalize_label(text), set())
        return {i for i in ids if scheme is None or i.startswith(scheme + ":")}

    def resolve(self, ref: str) -> set:
        """A cross-reference: a term label, else a tactic, practice or function (by label or id)."""
        prefix, _, name = ref.partition(":")
        schemes = ("term", "tactic", "practice", "function")
        if name and prefix.strip().lower() in schemes:
            schemes, ref = (prefix.strip().lower(),), name.strip()
        for scheme in schemes:
            found = self.find(ref, scheme)
            if found:
                return found
            candidate = f"{scheme}:{slug(ref)}"
            if self.conn.execute("SELECT 1 FROM concept WHERE id = ?", (candidate,)).fetchone():
                return {candidate}
        return set()

    def relation(self, concept_id: str, relation: str, target: str | None = None,
                 external: tuple[str, str, str] | None = None) -> None:
        key = f"{concept_id}|{relation}|{target}|{external}"
        values = (("rel_" + short_hash(key), concept_id, relation, target)
                  + (external if external else (None, None, None)) + (self.now,))
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO concept_relation(id, concept_id, relation, target_concept_id,"
            " external_scheme, external_id, external_label, created_at) VALUES (?,?,?,?,?,?,?,?)", values)
        self.stats["relations"] += cursor.rowcount

    def unique_id(self, base: str) -> str:
        candidate, n = base, 2
        while self.conn.execute("SELECT 1 FROM concept WHERE id = ?", (candidate,)).fetchone():
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    # -- entities -------------------------------------------------------------

    def entity(self, kind: str, labels: list[str], *, description=None, country=None,
               start=None, end=None, former=()) -> str:
        pref = labels[0]
        entity_id = f"{kind}:{slug(pref)}"
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO entity(id, kind, label, normalized_label, description, country,"
            " start_date, start_date_certainty, end_date, end_date_certainty, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (entity_id, kind, pref, normalize_label(pref), description, country,
             start, "exact" if start else None, end, "exact" if end else None, self.now))
        self.stats[f"entities_{kind}"] += cursor.rowcount
        for position, label in enumerate(labels):
            variant_kind = "pref" if position == 0 else ("abbreviation" if is_acronym(label) else "alt")
            self.variant(entity_id, label, variant_kind)
        for name, valid_from, valid_to in former:
            self.variant(entity_id, name, "former_name", valid_from, valid_to)
        return entity_id

    def variant(self, entity_id: str, label: str, kind: str, valid_from=None, valid_to=None) -> None:
        normalized = normalize_label(label)
        if not normalized or entity_id in self.entity_index.get(normalized, ()):
            return                           # empty, or this name is already a variant of the entity
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO entity_variant(id, entity_id, label, normalized_label, kind, valid_from,"
            " valid_to, created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("var_" + short_hash(f"{entity_id}|{normalized}|{kind}"), entity_id, clean_label(label),
             normalized, kind, valid_from, valid_to, self.now))
        self.entity_index[normalized].add(entity_id)
        self.stats["entity_variants"] += cursor.rowcount

    # -- loaders ----------------------------------------------------------------

    def load_registry(self, entries: list[RegistryEntry]) -> None:
        for entry in entries:
            if entry.labels:
                self.entity(entry.kind, entry.labels, description=entry.description,
                            country=entry.country, start=entry.start, end=entry.end, former=entry.former)

    def load_lexicon(self, parsed: dict) -> None:
        pending_related = []
        for entry in parsed["entries"]:
            if not entry.labels:
                continue
            existing = set().union(*(self.find(label) for label in entry.labels))
            concept_id = sorted(existing)[0] if existing else f"term:{slug(entry.labels[0])}"
            self.concept(concept_id, "term", entry.labels[0], language=entry.language,
                         definition=entry.definition, notation=entry.notation, review_state="confirmed")
            for label in entry.labels[1:]:
                self.label(concept_id, label, entry.language, "alt", review_state="confirmed")
            pending_related.extend((concept_id, ref) for ref in entry.related)

        for acronym, full, context in parsed["acronyms"]:
            targets = self.find(acronym) | self.find(full)
            is_org = any(word in context.lower() for word in ORG_CONTEXT) or \
                bool(self.entity_index.get(normalize_label(acronym)) or self.entity_index.get(normalize_label(full)))
            if is_org and not targets:
                found = self.entity_index.get(normalize_label(acronym)) or self.entity_index.get(normalize_label(full))
                if found:
                    for entity_id in found:
                        self.variant(entity_id, acronym, "abbreviation")
                        self.variant(entity_id, full, "alt")
                else:
                    self.entity("organization", [full, acronym], description=context)
                continue
            concept_id = sorted(targets)[0] if targets else f"term:{slug(acronym)}"
            self.concept(concept_id, "term", acronym, definition=None, notation="acronym",
                         review_state="confirmed")
            self.label(concept_id, full, "en", "alt", review_state="confirmed")

        for row in parsed["translations"]:
            english = clean_label(row.get("en", ""))
            if not english:
                continue
            targets = self.find(english)
            concept_id = sorted(targets)[0] if targets else f"term:{slug(english)}"
            self.concept(concept_id, "term", english, notation="translation-table", review_state="confirmed")
            for language, cell in row.items():
                if language == "en" or not cell or cell.strip() in ("—", "-"):
                    continue
                for value in re.split(r"\s+/\s+", cell):
                    self.label(concept_id, value, language, "alt", review_state="confirmed")

        for label, language in parsed["language_terms"]:
            targets = self.find(label)
            if targets:
                for concept_id in targets:
                    self.label(concept_id, label, language, "alt", review_state="confirmed")
                continue
            concept_id = self.unique_id(f"term:{slug(label)}")
            self.concept(concept_id, "term", label, language=language, notation="language-note",
                         review_state="confirmed")

        self.pending_related.extend(pending_related)

    def resolve_related(self) -> None:
        """Link Lexicon cross-references (last, so every label is known)."""
        for concept_id, ref in self.pending_related:
            targets = self.resolve(ref)
            if not targets and (self.entity_index.get(normalize_label(ref))):
                self.notes["related_to_entity"].append(f"{concept_id} -> {ref}")
                continue
            if not targets:
                target = f"term:{slug(ref)}"
                if self.concept(target, "term", ref, notation="xref-placeholder"):
                    self.notes["placeholders"].append(ref)
                targets = {target}
            for target in targets:
                if target != concept_id:
                    self.relation(concept_id, "related", target)

    def load_tactics_practices(self, sanity: dict, ontology: str) -> None:
        for tactic in sorted(sanity.get("tacticEntry", []), key=lambda t: "parentTactic" in t):
            concept_id = "tactic:" + tactic["_id"].removeprefix("tactic-")
            parent = (tactic.get("parentTactic") or {}).get("_ref")
            self.concept(concept_id, "tactic", tactic["tactic"], definition=tactic.get("definition"),
                         notation=tactic.get("primaryCluster"),
                         broader="tactic:" + parent.removeprefix("tactic-") if parent else None,
                         review_state="confirmed" if tactic.get("approvedBy") == "researcher" else "proposed")
        for practice in sanity.get("practiceEntry", []):
            self.concept("practice:" + practice["_id"].removeprefix("practice-"), "practice",
                         practice["practice"], definition=practice.get("definition"),
                         notation=practice.get("practiceType"))
        for name in parse_functions(ontology):
            self.concept(f"function:{slug(name)}", "function", name, review_state="confirmed")

    def load_govuk(self, data: dict) -> None:
        for term in data.get("terms", []):
            label = term.get("canonical_term") or term["source_term"]
            targets = self.find(label) | self.find(term["source_term"])
            if not targets:
                concept_id = f"term:{slug(label)}"
                self.concept(concept_id, "term", label, definition=term.get("source_definition"),
                             notation="reference")
                targets = {concept_id}
            for concept_id in targets:
                self.relation(concept_id, "exactMatch",
                              external=("govuk_glossary_2021", term["source_id"], term["source_term"]))

    def load_glossary(self, glossary: dict, merge_map: dict) -> None:
        entries_map = merge_map.get("entries", {})
        for key, spec in merge_map.get("new_concepts", {}).items():
            clash = self.find(spec["pref_label"]) - {key}
            if clash:
                raise ValueError(f"merge map 'new' concept {key} duplicates existing {sorted(clash)}")
            if self.concept(key, "term", spec["pref_label"], notation="glossary-merge"):
                self._decide("propose", "concept", key, spec["reason"],
                             new_state={"pref_label": spec["pref_label"]})
            for alt in spec.get("alt", []):
                self.label(key, alt, "en", "alt")
        for key, spec in merge_map.get("extra_labels", {}).items():
            for label in spec["labels"]:
                label_id = self.label(key, label, spec.get("language", "en"), "alt")
                if label_id:
                    self._decide("attach", "concept_label", label_id, spec["reason"],
                                 new_state={"concept_id": key, "label": label})
        mapped, unmapped = [], []
        for entry in glossary.get("entries", []):
            (mapped if entry["term"] in entries_map else unmapped).append(entry)

        for entry in unmapped:                       # pass 1: exact attach or new concept
            term, language = entry["term"], entry.get("language") or "en"
            labels = parse_glossary_term(term)
            if not labels:
                continue
            targets = set().union(*(self.find(label) for label in labels))
            if targets:
                for concept_id in targets:
                    for label in labels:
                        self.label(concept_id, label, language, "alt")
                self.stats["glossary_attached_exact"] += 1
                continue
            concept_id = self.unique_id(f"term:{slug(labels[0])}")
            self.concept(concept_id, "term", labels[0], language=language,
                         definition=entry.get("draft_definition"), definition_source="model",
                         notation=entry.get("proposed_category") or "glossary")
            for label in labels[1:]:
                self.label(concept_id, label, language, "alt")
            self.stats["glossary_new_concepts"] += 1

        missing = sorted({spec["concept"] for spec in entries_map.values() if spec["action"] == "label_of"}
                         - {row[0] for row in self.conn.execute("SELECT id FROM concept")})
        if missing:
            raise ValueError(f"merge map points to concepts that do not exist: {missing}")

        for entry in mapped:                         # pass 2: the merge map's decisions
            term, language = entry["term"], entry.get("language") or "en"
            spec = entries_map[term]
            if spec["action"] == "skip":
                self.stats["glossary_skipped"] += 1
            elif spec["action"] == "entity":
                self.entity(spec["kind"], [spec.get("label", term)], country=spec.get("country"),
                            description=entry.get("draft_definition"))
                self.stats["glossary_to_entity"] += 1
            elif spec["action"] == "label_of":
                for label in spec.get("labels") or parse_glossary_term(term):
                    label_id = self.label(spec["concept"], label, language, "alt")
                    if label_id:
                        self._decide("attach", "concept_label", label_id, spec["reason"],
                                     new_state={"concept_id": spec["concept"], "label": label,
                                                "language": language, "glossary_term": term})
                self.stats["glossary_merged"] += 1
        unused = sorted(set(entries_map) - {e["term"] for e in mapped})
        if unused:
            self.notes["merge_map_unused"].extend(unused)

    def _decide(self, action: str, table: str, target_id: str, reason: str, new_state: dict) -> None:
        record_decision(self.conn, actor_kind="model", actor_id=self.actor, run_id=self.run_id,
                        action=action, target_table=table, target_id=target_id, reason=reason,
                        new_state=new_state)
        self.stats["decisions"] += 1

    actor = "claude-opus-5-5"


def merge_concepts(conn: sqlite3.Connection, duplicate: str, into: str, *, reason: str, actor_kind: str,
                   actor_id: str, run_id: str | None = None) -> list[dict]:
    """Fold a duplicate concept into another, as recorded decisions (nothing deleted).

    The duplicate's labels are added to ``into`` (one 'attach' decision each)
    and marked superseded; the duplicate itself is marked superseded with a
    'merge' decision. Re-run matching afterwards to move its usages.
    """
    decisions = []
    now = utc_now()
    own = {row["normalized_label"] for row in conn.execute(
        "SELECT normalized_label FROM concept_label WHERE concept_id = ?", (into,))}
    for row in conn.execute("SELECT * FROM concept_label WHERE concept_id = ? AND review_state NOT IN"
                            " ('rejected', 'superseded')", (duplicate,)).fetchall():
        if row["normalized_label"] not in own:
            label_id = "lab_" + short_hash(f"{into}|{row['normalized_label']}|{row['language']}|alt")
            conn.execute("INSERT OR IGNORE INTO concept_label(id, concept_id, label, normalized_label, language,"
                         " kind, created_at) VALUES (?,?,?,?,?, 'alt', ?)",
                         (label_id, into, row["label"], row["normalized_label"], row["language"], now))
            decisions.append(record_decision(
                conn, actor_kind=actor_kind, actor_id=actor_id, run_id=run_id, action="attach",
                target_table="concept_label", target_id=label_id, reason=f"Moved from {duplicate}: {reason}",
                new_state={"concept_id": into, "label": row["label"]}))
        conn.execute("UPDATE concept_label SET review_state = 'superseded' WHERE id = ?", (row["id"],))
    conn.execute("UPDATE concept SET review_state = 'superseded' WHERE id = ?", (duplicate,))
    decisions.append(record_decision(
        conn, actor_kind=actor_kind, actor_id=actor_id, run_id=run_id, action="merge",
        target_table="concept", target_id=duplicate, reason=reason,
        previous_state={"review_state": "proposed"}, new_state={"review_state": "superseded", "merged_into": into}))
    return decisions


def load_all(conn: sqlite3.Connection, seeds: Path = SEEDS) -> dict:
    merge_map = json.loads((seeds / "merge_map_2026-11-09.json").read_text(encoding="utf-8"))
    run_id = f"run_{uuid.uuid4().hex}"
    with conn:
        conn.execute("INSERT INTO run(id, role, model, prompt_version, inputs, started_at)"
                     " VALUES (?, 'other', ?, ?, ?, ?)",
                     (run_id, merge_map.get("decided_by", "").split(" ")[0] or None,
                      merge_map.get("prompt_version"), canonical_json({"job": "load_seeds"}), utc_now()))
    loader = SeedLoader(conn, run_id=run_id)
    conn.execute("BEGIN")
    try:
        loader.load_registry(parse_registry((seeds / "Entity_Registry_v1.1.md").read_text(encoding="utf-8")))
        loader.load_tactics_practices(
            json.loads((seeds / "sanity_seed_2026-05-05.json").read_text(encoding="utf-8")),
            (seeds / "SOGICE_Ontology_v3.0.md").read_text(encoding="utf-8"))
        loader.load_lexicon(parse_lexicon((seeds / "SOGICE_Lexicon_v2.1.md").read_text(encoding="utf-8")))
        loader.load_govuk(json.loads((seeds / "govuk_conversion_therapy_glossary_2021.json").read_text(encoding="utf-8")))
        loader.load_glossary(json.loads((seeds / "sogice_glossary_2026-04-03.json").read_text(encoding="utf-8")),
                             merge_map)
        loader.resolve_related()
        conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ?, inputs = ? WHERE id = ?",
                     (utc_now(), canonical_json({"job": "load_seeds", "stats": dict(loader.stats)}), run_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"run_id": run_id, "stats": dict(sorted(loader.stats.items())),
            "placeholders": loader.notes["placeholders"],
            "related_to_entity": loader.notes["related_to_entity"]}
