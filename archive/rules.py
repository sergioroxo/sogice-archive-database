"""Rules software applies to the readers' relations (from the first review trial, 10 Nov 2026).

The review trial showed most relation rulings falling into a few patterns that
need no judgement. These rules apply them, so the review queue is left with
the cases that do. They run after ``reconcile`` (the ``reconcile`` command
and ``read --both`` run both), on every relation still open (proposed or
disputed), and each ruling is a decision under ``rule:relations/v1`` with
the quote and the reason. A run of rulings can be undone like a review run
(``python -m archive review undo --run RUN_ID``). A record a reviewer or the
researcher has ruled on is never changed, and one a rule has ruled on is left
alone the next time.

1. **Ends are names in the text.** Both ends of a relation must be written in
   the passage that states it (the document being read may be named by its
   title, publisher or site), and must be names: not a profession, a title or
   office ("grand rabbin de France"), a description ("organisation
   évangélique", "commission") or a speaker label. Otherwise the relation is
   rejected. Registry entities (seed vocabularies) always count as names.
2. **Role lines.** "Name, role, Organisation" (or "Name, role of
   Organisation"), or "Name, role" in a list under a heading naming the
   organisation, states a position: the relation becomes ``employed_by`` and
   is confirmed. A profession on its own ("avocate", "psychologist",
   "universitaire") states no relation: rejected. A transcript's speaker
   label ("Mme X, rapporteure.") states no relation to a body: rejected. When
   the line names another body than the one the reader linked, the rule does
   not decide; the review queue does.
3. **Amendments and bills.** A text credited to someone ("Amendement CL34 de
   M. X", "proposition de loi de Mme Y") is authorship: "<text> authored_by
   <person>", never co_signatory_of and never the other way round. The shape
   is corrected; when the quote has the "amendement … de M./Mme <name>" form
   the relation is confirmed.

Rules decide on the words of the source and on word lists; they never use a
model, and a relation they cannot read stays as it was.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass

from .db import canonical_json, record_decision, utc_now

RULE = "relations/v1"
ACTOR_ID = f"rule:{RULE}"
ACTOR_PREFIX = "rule:relations/"
ROLE_TYPES = ("employed_by", "affiliated_with")
TEXT_TYPES = ("co_signatory_of", "authored_by")
BULLETS = "●•▪■◦–—-*·"
HEADING_BULLETS = "●•▪■"
SEARCH_CHARS = 300          # where to look for the subject's name around the quote
HEADING_CHARS = 2500        # how far back a list heading may be


# --------------------------------------------------------------------------- words

def fold(text: str | None) -> str:
    """Accent-, case- and punctuation-insensitive form (apostrophes and hyphens become spaces)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]|_", " ", text)).strip()


def _phrases(*groups: str) -> tuple[tuple[str, ...], ...]:
    out = {tuple(fold(p).split()) for g in groups for p in g.split("|") if p.strip()}
    return tuple(sorted(out, key=len, reverse=True))   # longest first


# A position in an organisation: "Name, <position> [of] Organisation" states employment.
POSITIONS = _phrases(
    "president|vice president|vice-president|co-president|chair|co-chair|chairman|chairwoman|chairperson|"
    "chief executive|chief executive officer|ceo|executive director|director|managing director|deputy director|"
    "general secretary|secretary general|secretary|treasurer|founder|co-founder|cofounder|member|board member|"
    "trustee|spokesperson|spokesman|spokeswoman|coordinator|head|manager|officer|chief officer|leader|"
    "pastor|senior pastor|dean|editor|adviser|advisor|representative|ambassador|volunteer|staff member|"
    "programme manager|program manager|policy officer|campaigns manager|chief operating officer|coo|"
    "chief medical officer|medical director|national medical director|clinical director|honorary secretary|"
    "honorary president|registrar|deputy chief executive|interim chief executive|acting chief executive|"
    "chief executive & registrar|chief executive and registrar|executive officer|general manager|"
    "présidente|président|vice-présidente|vice-président|coprésident|coprésidente|co-présidente|"
    "directeur|directrice|directeur général|directrice générale|délégué général|déléguée générale|"
    "fondateur|fondatrice|cofondateur|cofondatrice|co-fondateur|co-fondatrice|membre fondateur|"
    "membre|secrétaire|secrétaire générale|secrétaire général|trésorier|trésorière|"
    "porte-parole|coordinateur|coordinatrice|responsable|chargé de mission|chargée de mission|chef|cheffe|"
    "administrateur|administratrice|prieur|prieure|pasteur|aumônier|bénévole|salarié|salariée|"
    "animateur|animatrice|référent|référente|conseiller|conseillère|délégué|déléguée|"
    "presidenta|directora|fundador|fundadora|miembro|membro|coordenador|coordenadora|coordinadora|"
    "secretário|secretária|secretario|secretaria|tesoureiro|tesorero|porta-voz|portavoz")
# A profession: on its own it states no relation to the organisation a list puts it under.
PROFESSIONS = _phrases(
    "lawyer|attorney|barrister|solicitor|psychologist|clinical psychologist|psychiatrist|psychotherapist|"
    "therapist|counsellor|counselor|psychoanalyst|doctor|physician|general practitioner|gp|nurse|"
    "social worker|academic|sociologist|historian|theologian|journalist|writer|author|researcher|professor|"
    "lecturer|scholar|anthropologist|philosopher|sexologist|paediatrician|pediatrician|endocrinologist|"
    "avocat|avocate|psychologue|psychiatre|pédopsychiatre|psychothérapeute|psychanalyste|thérapeute|médecin|"
    "docteur|pédiatre|endocrinologue|infirmier|infirmière|universitaire|sociologue|historien|historienne|"
    "théologien|théologienne|journaliste|écrivain|écrivaine|auteur|autrice|auteure|chercheur|chercheuse|"
    "enseignant|enseignante|enseignant-chercheur|enseignante-chercheuse|professeur|professeure|"
    "maître de conférences|juriste|sexologue|sage-femme|anthropologue|philosophe|"
    "abogado|abogada|advogado|advogada|psicólogo|psicóloga|psiquiatra|médico|médica|periodista|jornalista")
# A role in a sitting: "Mme X, rapporteure." introduces a turn; it names no body.
SITTING_ROLES = _phrases(
    "rapporteur|rapporteure|rapporteuse|président|présidente|député|députée|sénateur|sénatrice|ministre|"
    "garde des sceaux|secrétaire d'état|chair|chairman|chairwoman|minister|rapporteur général")
# Titles and offices: a label made of these (and "of …") is an office, not a named entity.
TITLES = _phrases(
    "rabbi|chief rabbi|grand rabbin|rabbin|bishop|archbishop|cardinal|pope|priest|reverend|rev|imam|"
    "évêque|archevêque|pape|prêtre|curé|abbé|père|soeur|frère|pasteure|imam|mufti|"
    "député|députée|sénateur|sénatrice|ministre|minister|garde des sceaux|rapporteur|rapporteure|"
    "commissioner|mayor|maire|governor|gouverneur|judge|juge|magistrate|magistrat")
# Bodies inside the organisation a list heading names ("membre du bureau national").
INTERNAL = _phrases(
    "bureau|bureau national|conseil d'administration|conseil national|board|board of trustees|"
    "board of directors|section|comité|comité directeur|executive committee|steering committee|"
    "association|l'association|the association|the organisation|the organization|the charity|"
    "the board|the group|le groupe|la section|le bureau|le conseil|conseil|council|committee")
# Words that open the name of a body even when it is written in lower case ("du collectif des victimes").
BODIES = _phrases(
    "collectif|association|fédération|federation|réseau|network|groupe|group|église|church|institut|institute|"
    "fondation|foundation|ministère|ministry|coalition|alliance|union|société|society|centre|center|"
    "délégation|mission|ordre|syndicat|parti|party|conseil national|college|collège|université|university|"
    "hôpital|hospital|clinique|clinic|trust|charity|organisation|organization|agence|agency|office")
# Names of laws and institutions French writes in lower case ("code pénal", "commission des lois",
# "mission interministérielle de vigilance …"): a code or law, or an institution with its complement.
LAWS = {"code", "loi", "law", "act"}
INSTITUTIONS = {"commission", "mission", "delegation", "ministere", "conseil", "assemblee", "cour", "tribunal",
                "comite", "observatoire", "direction", "haut", "haute", "agence", "office", "senat"}
HONORIFICS = {"m", "mme", "mmes", "mm", "me", "mlle", "dr", "pr", "prof", "mr", "mrs", "ms", "miss", "sir",
              "dame", "lord", "lady", "sr", "sra", "srta", "don", "dona", "dra"}
ARTICLES = {"the", "a", "an", "le", "la", "les", "l", "un", "une", "el", "los", "las", "o", "os", "as"}
PREPOSITIONS = {"of", "at", "for", "with", "in", "de", "du", "des", "d", "au", "aux", "a", "à", "chez",
                "do", "da", "dos", "das", "del", "en", "within"}
STOP = ARTICLES | PREPOSITIONS | HONORIFICS | {"and", "et", "y", "e", "und"}
TEXT_LABEL = re.compile(r"\b(sous[- ])?amendements?\b|\bamendments?\b|\bproposition de loi\b|\bprojet de loi\b"
                        r"|\bproposici[oó]n de ley\b|\bprojeto de lei\b", re.IGNORECASE)
AMENDMENT_BY = re.compile(
    r"\b(?:sous-)?amendements?\b(?:\s+(?:n°\s*)?[A-Z]{0,4}\s?\d+(?:\s*(?:,|et)\s*(?:n°\s*)?[A-Z]{0,4}\s?\d+)*)?"
    r"\s+(?:de|du|des)\s+(?:M\.|Mme|MM\.|Mmes|Monsieur|Madame)?\s*(?P<who>[^\n,.;]{2,60})", re.IGNORECASE)


def _starts_with(words: list[str], phrases) -> int:
    """Length of the longest phrase the words start with (0 if none)."""
    for phrase in phrases:
        if tuple(words[:len(phrase)]) == phrase:
            return len(phrase)
    return 0


def _ends_with(words: list[str], phrases) -> int:
    for phrase in phrases:
        if len(words) > len(phrase) and tuple(words[-len(phrase):]) == phrase:
            return len(phrase)
    return 0


def bare(label: str | None) -> list[str]:
    """Folded words of a name without leading honorifics and articles."""
    words = fold(label).split()
    while words and (words[0] in HONORIFICS or words[0] in ARTICLES):
        words = words[1:]
    return words


def _significant(words: list[str]) -> list[str]:
    return [w for w in words if w not in STOP and (len(w) >= 3 or w.isdigit() or w.isupper())]


def not_a_name(label: str | None, *, cased: bool = True) -> str | None:
    """Why a relation end is not a name (None if it may be one).

    ``cased``: the text uses capitals (automatic captions are all lower case, so there a missing
    capital says nothing).
    """
    raw = (label or "").strip()
    stripped = raw
    for _ in range(3):   # honorifics and articles as written
        stripped = re.sub(r"^(?:M\.|Mme|Mmes|MM\.|Me|Dr\.?|Pr\.?|Prof\.?|Mr\.?|Mrs\.?|Ms\.?|the|The|le|Le|la|La|"
                          r"les|Les|l'|L'|l’|L’|un|une|a|an)\s*", "", stripped)
    words = bare(raw)
    if not words:
        return "empty"
    if cased and not any(c.isupper() or c.isdigit() for c in stripped):
        lower_case_name = (words[0] in LAWS and len(words) >= 2) or (
            words[0] in INSTITUTIONS and any(w in ("de", "des", "du", "d") for w in words[1:-1]))
        if not lower_case_name:
            return "a description, not a name (no capital letter)"
    heads = PROFESSIONS + POSITIONS + TITLES + SITTING_ROLES
    lead = 1 if len(words) > 1 and words[0] in ("grand", "chief", "senior", "former", "ancien", "ancienne") else 0
    n = _starts_with(words[lead:], heads)
    if n:
        rest = words[lead + n:]
        if not rest or rest[0] in PREPOSITIONS:
            return "a profession, title or office, not a name"
    return None


def in_text(label: str | None, text: str) -> bool:
    """The name is written in the text (whole, or every significant word of it)."""
    words = bare(label)
    if not words:
        return False
    folded = f" {fold(text)} "
    if f" {' '.join(words)} " in folded:
        return True
    significant = _significant(words)
    vocabulary = set(folded.split())
    return bool(significant) and all(w in vocabulary for w in significant)


def cased(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return not letters or sum(c.isupper() for c in letters) / len(letters) >= 0.01


def is_document(label: str | None, documents: list[str]) -> bool:
    """The label names the document being read (its title, publisher or site)."""
    squeezed = "".join(bare(label))
    for d in documents:
        if same_body(label, d):
            return True
        host = fold(d).replace(" ", "")
        if len(squeezed) >= 5 and "." not in d and " " not in d.strip() and squeezed in host:
            return True
    return False


def same_body(label: str | None, other: str | None, registry: dict | None = None) -> bool:
    a, b = bare(label), bare(other)
    if not a or not b:
        return False
    x, y = " ".join(a), " ".join(b)
    if x == y or f" {x} " in f" {y} " or f" {y} " in f" {x} ":
        return True
    acronyms = set(re.findall(r"\b[A-Z]{3,}\b", label or "")) & set(re.findall(r"\b[A-Z]{3,}\b", other or ""))
    if acronyms:
        return True
    if registry and registry.get(x) and registry.get(x) == registry.get(y):
        return True
    sa, sb = set(_significant(a)), set(_significant(b))
    return bool(sa and sb) and len(sa & sb) / len(sa | sb) >= 0.6


# --------------------------------------------------------------------------- role lines

@dataclass
class RoleLine:
    line: str              # the entry as written (name, role and what follows)
    start: int             # where the entry starts and ends in the text
    end: int
    kind: str              # position | profession | sitting | none
    role: str              # the role words (folded)
    org: str | None        # a body named on the line, as written, if any
    internal: bool         # that body is part of the heading's organisation ("membre du bureau national")
    heading: str | None    # the list heading the entry sits under, if any
    label_form: bool       # "Name, role." (a transcript's turn label)


INTERNAL_WORDS = {w for phrase in INTERNAL for w in phrase}


def _name_pattern(label: str) -> re.Pattern | None:
    words = [w for w in re.split(r"[\s\u00a0]+", (label or "").strip()) if w]
    while words and fold(words[0]) in HONORIFICS | ARTICLES:
        words = words[1:]
    if not words:
        return None
    return re.compile(r"[\s\u00a0]+".join(re.escape(w) for w in words), re.IGNORECASE)


def _entry_end(text: str, start: int) -> int:
    """Where a list entry or signature line that starts at ``start`` ends."""
    end = min(len(text), start + 200)
    for m in re.finditer(r"\n\s*\n|\n\s*[" + re.escape(BULLETS) + r"\uf8e7]|\x0c|;", text[start:end]):
        return start + m.start()
    # a signature block: the next line that opens another "Name, Role" entry
    for m in re.finditer(r"\n(?=(?:M\.|Mme|Dr|Prof|Mr|Mrs|Ms)?\s*[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+){1,3},)",
                         text[start:end]):
        return start + m.start()
    return end


def _is_entry_bullet(ch: str) -> bool:
    return ch in BULLETS or ch == "\uf8e7"


def _heading(text: str, line_start: int) -> str | None:
    """The heading of the list an entry belongs to: the nearest earlier line opened by a heading bullet,
    with only other entries, continuation lines and page furniture in between. A heading may run onto
    the next line ("● Délégation … l'antisémitisme / et la haine anti-LGBT (DILCRAH)")."""
    entry = text[line_start:line_start + 3].lstrip()
    if not entry or not _is_entry_bullet(entry[0]) or entry[0] in HEADING_BULLETS:
        return None
    lines = text[max(0, line_start - HEADING_CHARS):line_start].split("\n")
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip().strip("\x0c").strip()
        if not stripped:
            continue
        if stripped[0] in HEADING_BULLETS:
            parts = [stripped[1:].strip()]
            for following in lines[i + 1:]:
                more = following.strip()
                if not more or _is_entry_bullet(more[0]):
                    break
                parts.append(more)
            return " ".join(p for p in parts if p) or None
        if stripped[0] == entry[0] or re.fullmatch(r"[–—-]\s*\d+\s*[–—-]", stripped) or len(stripped) <= 120:
            continue   # another entry, a page number, or the rest of an entry
        return None    # prose: not a list
    return None


def _names_a_body(org: str, cased_text: bool = True) -> bool:
    """'du CNEF', 'du collectif des victimes', "l'association Psygay" name a body; 'en charge de la lutte
    contre la haine', 'justice et relations internationales' describe a portfolio."""
    words = bare(org)
    if _starts_with(words, BODIES):
        return True
    tokens = re.findall(r"[\w'’-]+", org)
    return cased_text and any(any(c.isupper() for c in tok) for tok in tokens)


def _internal(org: str) -> bool:
    """'du bureau national', 'de la section éthique', "de l'association": a body inside the heading's
    organisation. 'du collectif des victimes', "l'association Psygay": a body of its own."""
    if not _starts_with(bare(org), INTERNAL):
        return False
    tokens = re.findall(r"[\w'’-]+", org)
    return not any(tok[0].isupper() and fold(tok) not in INTERNAL_WORDS for tok in tokens[1:])


def role_line(text: str, subject: str, near: tuple[int, int]) -> RoleLine | None:
    """Read "Name, role[, Organisation]" for the subject where the relation's quote is."""
    pattern = _name_pattern(subject.split(",")[0])
    if pattern is None:
        return None
    lo, hi = max(0, near[0] - SEARCH_CHARS), min(len(text), near[1] + SEARCH_CHARS)
    matches = list(pattern.finditer(text, lo, hi))
    if not matches:
        return None
    m = min(matches, key=lambda x: abs(x.start() - near[0]))
    # "Name, role", "Name CBE, role", or a signature block's "Name\nRole, Organisation"
    after = re.match(r"(?:[\s\u00a0]+(?:[A-Z]{2,}[a-z]?|PhD|MBE|OBE|CBE))*[\s\u00a0]*,[\s\u00a0]*", text[m.end():])
    if not after:
        after = re.match(r"[ \u00a0]*\n[ \u00a0]*(?=[A-Z])", text[m.end():])
        if not after or not _starts_with(fold(text[m.end() + after.end():m.end() + after.end() + 60]).split(),
                                         POSITIONS):
            return _role_before(text, m)
    role_start = m.end() + after.end()
    end = _entry_end(text, role_start)
    raw = text[role_start:end]
    label_form = bool(re.match(r"[^\n.]{1,60}?\.(?:\s|$)", raw))
    if label_form:
        raw = raw[:raw.index(".")]
        end = role_start + len(raw) + 1
    role_text = re.sub(r"\s+", " ", raw).strip(" ,.:")
    line_start = text.rfind("\n", 0, m.start()) + 1
    words = fold(role_text).split()
    while words and words[0] in ARTICLES:
        words = words[1:]
    kind, n = "none", 0
    for name, phrases in (("position", POSITIONS), ("profession", PROFESSIONS), ("sitting", SITTING_ROLES)):
        n = _starts_with(words, phrases)
        if n:
            kind = name
            break
    org = None
    if n:
        rest = words[n:]
        while rest and (rest[0] in PREPOSITIONS or rest[0] in ARTICLES):
            rest = rest[1:]
        org = _trim(_original(role_text, rest)) if rest else None
    else:   # "Exodus president": the role at the end, the body before it
        n = _ends_with(words, POSITIONS)
        if n:
            kind = "position"
            org = _original(role_text, words[:-n])
            words = words[-n:] + words[:-n]
    return RoleLine(re.sub(r"\s+", " ", text[line_start:end]).strip().lstrip(BULLETS + "\uf8e7 "), line_start, end, kind,
                    " ".join(words[:n]) if n else "", org, bool(org) and _internal(org),
                    _heading(text, line_start), label_form)


def _trim(org: str | None) -> str | None:
    """A body's name ends at a comma, a full stop, a bracket or a quotation mark (prose goes on after it)."""
    if not org:
        return org
    cut = re.split(r"\s*(?:[,;:(\"“”«»]|\.(?:\s|$))", org, maxsplit=1)[0].strip()
    return cut or None


def _role_before(text: str, m: re.Match) -> RoleLine | None:
    """'The president of SOS Homophobie, Joël Deumier' and 'Exodus President Alan Chambers'."""
    line_start = max(text.rfind("\n", 0, m.start()) + 1, m.start() - 120)
    before = text[line_start:m.start()]
    before = re.split(r"(?:[.;:!?]\s|[\"“”«»(])", before)[-1]
    lead = re.match(r"\s*(?:the|a|an|le|la|l'|l’)?\s*(?P<role>.+?)\s+(?:of|at|de|du|des|d'|d’)\s+(?P<org>[^,]+),\s*$",
                    before, re.IGNORECASE)
    if lead and _starts_with(fold(lead.group("role")).split(), POSITIONS) == len(fold(lead.group("role")).split()):
        role, org = fold(lead.group("role")), lead.group("org").strip()
    else:
        words = before.strip().split()
        folded = fold(before).split()
        n = _ends_with(folded, POSITIONS) if folded else 0
        if not n or len(folded) == n:
            return None
        role = " ".join(folded[-n:])
        org = " ".join(words[:len(words) - n]).strip(" ,")
        if not org or not org[0].isupper():
            return None
    end = m.end()
    return RoleLine(re.sub(r"\s+", " ", text[line_start:end]).strip(), line_start, end, "position", role, org,
                    _internal(org), None, False)


def _original(text: str, words: list[str]) -> str | None:
    """The span of ``text`` from the first of ``words`` (as folded) to the last, as written."""
    if not words:
        return None
    tokens = list(re.finditer(r"[\w'’-]+", text))
    flat = [(i, w) for i, t in enumerate(tokens) for w in fold(t.group()).split()]
    for k in range(len(flat) - len(words) + 1):
        if [w for _, w in flat[k:k + len(words)]] == words:
            first, last = tokens[flat[k][0]], tokens[flat[k + len(words) - 1][0]]
            return text[first.start():last.end()].strip()
    return " ".join(words)


# --------------------------------------------------------------------------- applying the rules

def _settled(conn: sqlite3.Connection, relation_id: str) -> bool:
    """Ruled on by a reviewer, the researcher or an earlier run of these rules: leave it alone."""
    from .reconcile import _effective_last, ACTOR_PREFIX as RECONCILE
    last = _effective_last(conn, "relation", relation_id, skip_audits=True)
    return bool(last) and last["action"] != "propose" and not last["actor_id"].startswith(RECONCILE)


def _open_relations(conn: sqlite3.Connection, source_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT DISTINCT r.* FROM relation r JOIN relation_usage ru ON ru.relation_id = r.id
           JOIN usage u ON u.id = ru.usage_id JOIN passage p ON p.id = u.passage_id
           JOIN source_version v ON v.id = p.version_id
           WHERE v.source_id = ? AND r.review_state IN ('proposed', 'disputed') ORDER BY r.id""", (source_id,))]


def _evidence(conn: sqlite3.Connection, relation_id: str) -> dict | None:
    row = conn.execute("""SELECT u.*, p.text AS passage_text, p.version_id, p.sequence, p.time_start AS passage_time FROM usage u
                          JOIN relation_usage ru ON ru.usage_id = u.id JOIN passage p ON p.id = u.passage_id
                          WHERE ru.relation_id = ? ORDER BY u.char_start LIMIT 1""", (relation_id,)).fetchone()
    return dict(row) if row else None


def _previous_text(conn: sqlite3.Connection, evidence: dict) -> str:
    row = conn.execute("SELECT text FROM passage WHERE version_id = ? AND sequence = ?",
                       (evidence["version_id"], evidence["sequence"] - 1)).fetchone()
    return row[0] if row else ""


def _document_names(conn: sqlite3.Connection, source_id: str) -> list[str]:
    """What names the document being read: title, publisher, site, and the file name in its URL."""
    s = conn.execute("SELECT url, title, publisher FROM source WHERE id = ?", (source_id,)).fetchone()
    url = s["url"] or ""
    host = re.sub(r"^www\.", "", re.sub(r"^https?://([^/]+).*$", r"\1", url))
    path = re.sub(r"[?#].*$", "", url).rstrip("/").rsplit("/", 1)[-1]
    file_name = re.sub(r"\.(pdf|html?|php|aspx?)$", "", path, flags=re.IGNORECASE).replace("-", " ").replace("_", " ")
    return [x for x in (s["title"], s["publisher"], host, host.split(".")[0], file_name) if x]


def _first_page(conn: sqlite3.Connection, source_id: str) -> str:
    row = conn.execute("""SELECT p.text FROM passage p JOIN source_version v ON v.id = p.version_id
                          WHERE v.source_id = ? ORDER BY v.created_at, p.sequence LIMIT 1""", (source_id,)).fetchone()
    return row[0] if row else ""


def _source_speakers(conn: sqlite3.Connection, source_id: str) -> list[str]:
    """Who speaks in this source, by the readers' statements (the name before any role)."""
    rows = conn.execute("""SELECT DISTINCT u.speaker FROM usage u JOIN passage p ON p.id = u.passage_id
                           JOIN source_version v ON v.id = p.version_id
                           WHERE v.source_id = ? AND u.entity_id IS NULL AND u.speaker IS NOT NULL""", (source_id,))
    return sorted({r[0].split(",")[0].strip() for r in rows if r[0] and r[0] != "unknown"})


def _registry(conn: sqlite3.Connection) -> dict:
    """Registry entities (seed vocabularies, not model proposals): folded name -> entity id."""
    index = {}
    for eid, label in conn.execute("SELECT id, label FROM entity WHERE id NOT LIKE 'ent_%'"):
        index.setdefault(" ".join(bare(label)), eid)
    for eid, label in conn.execute("SELECT entity_id, label FROM entity_variant WHERE entity_id NOT LIKE 'ent_%'"):
        index.setdefault(" ".join(bare(label)), eid)
    return index


def _names_of(registry: dict, label: str) -> list[str]:
    """Every registry name of the entity the label names (the label itself if none)."""
    eid = registry.get(" ".join(bare(label)))
    return [name for name, other in registry.items() if other == eid] if eid else [label]


def check_relation(conn: sqlite3.Connection, rel: dict, evidence: dict, *, documents: list[str],
                   registry: dict, first_page: str = "", speakers: list[str] = ()) -> list[tuple[str, dict, str]]:
    """What the rules say about one relation: [(action, values, reason, rule)], empty when they say nothing.

    action is 'reject', 'edit' (values: the fields to set) or 'confirm'; rule names which rule
    (end_not_a_name, end_not_in_text, speaker_label, profession_only, role_line, amendment, signer_to_signer).
    """
    text = evidence["passage_text"]
    quote = evidence["quote"]
    span = (evidence["char_start"], evidence["char_end"])
    # 1. ends are names in the text
    for side in ("subject", "object"):
        label = rel[f"{side}_label"]
        known = " ".join(bare(label)) in registry
        if not known:
            captions = evidence.get("passage_time") is not None   # automatic captions: capitals mean little
            why = not_a_name(label, cased=cased(text) and not captions)
            if why:
                return [("reject", {}, f"The {side} '{label}' is {why}; a relation links two named entities.",
                         "end_not_a_name")]
        around = _previous_text(conn, evidence) + "\n" + text
        written = any(in_text(name, around) for name in _names_of(registry, label))
        speaker = any(same_body(label.split(",")[0], s) for s in speakers)
        document = is_document(label, documents) or (first_page and in_text(label, first_page))
        if not written and not speaker and not document:
            return [("reject", {}, f"The {side} '{label}' is not written in the passage that states the relation or "
                                   f"the one before (nor is it a speaker in the source, or this document's title, "
                                   f"publisher, site or first page).", "end_not_in_text")]
    out: list[tuple[str, dict, str]] = []
    subject, obj, rtype = rel["subject_label"], rel["object_label"], rel["type"]
    # signatures link a signer to a text, never two signers
    if rtype == "co_signatory_of" and _kind(conn, rel["object_entity_id"]) == "person":
        return [("reject", {}, f"co_signatory_of links a signer to the text signed; '{obj}' is a person, so this "
                               "records two people named together, not a signature.", "signer_to_signer")]
    # 3. amendments and bills are authorship: text authored_by person
    if rtype in TEXT_TYPES:
        s_text, o_text = bool(TEXT_LABEL.search(subject)), bool(TEXT_LABEL.search(obj))
        if s_text != o_text:
            values = {}
            if o_text:   # person -> text: turn it round
                from .review import _reversed
                values.update(_reversed(conn, rel["id"]))
            if rtype != "authored_by":
                values["type"] = "authored_by"
            person = obj if s_text else subject
            if values:
                out.append(("edit", values, "An amendment or bill credited to someone is authorship: the text "
                                            "authored_by the person (not co_signatory_of, and not the other way round).",
                            "amendment"))
            by = AMENDMENT_BY.search(quote)
            if by and same_body(person, by.group("who")):
                out.append(("confirm", {}, f"The quote credits the amendment to {person} "
                                           f"(\"{by.group(0).strip()}\"): authorship stated by the text.", "amendment"))
            return out
    # 2. role lines
    if rtype in ROLE_TYPES:
        line = role_line(text, subject, span)
        if line is None or line.kind == "none":
            return []
        heading = line.heading
        if heading is None and line.start < HEADING_CHARS:   # the list may start on the page before
            previous = _previous_text(conn, evidence)
            if previous:
                shift = len(previous) + 1
                again = role_line(previous + "\n" + text, subject, (span[0] + shift, span[1] + shift))
                heading = again.heading if again else None
        only_the_line = line.start - 2 <= span[0] and span[1] <= line.end + 2 or (
            heading is not None and fold(quote) in fold(heading + " " + line.line))
        named_org = line.org if line.org and not line.internal and _names_a_body(line.org, cased(text)) else None
        sitting = _starts_with(line.role.split(), SITTING_ROLES) > 0
        if line.label_form and sitting and line.org is None and heading is None and only_the_line:
            return [("reject", {}, f"'{_short(line.line)}' is a speaker label introducing a turn; it names no body, "
                                   "so it states no relation.", "speaker_label")]
        if line.kind == "profession" and named_org is None and only_the_line:
            return [("reject", {}, f"'{_short(line.line)}' gives a profession only; a profession on its own states no "
                                   f"relation{' to the heading ' + repr(heading) if heading else ''}.", "profession_only")]
        if line.kind not in ("position", "profession"):
            return []
        target = named_org or (heading if line.kind == "position" else None)
        if target and same_body(obj, target, registry):
            if rtype != "employed_by":
                out.append(("edit", {"type": "employed_by"},
                            f"A stated role ('{_short(line.line)}') is employed_by, not {rtype}.", "role_line"))
            where = f"under the heading '{heading}'" if not named_org else "on the same line"
            out.append(("confirm", {}, f"Role line '{_short(line.line)}' ({line.kind} '{line.role}', organisation named "
                                       f"{where}): {subject} employed_by {obj}.", "role_line"))
        return out
    return []


def _short(line: str, limit: int = 160) -> str:
    return line if len(line) <= limit else line[:limit].rsplit(" ", 1)[0] + " …"


def _kind(conn: sqlite3.Connection, entity_id: str | None) -> str | None:
    row = conn.execute("SELECT kind FROM entity WHERE id = ?", (entity_id,)).fetchone() if entity_id else None
    return row[0] if row else None


def apply_rules(conn: sqlite3.Connection, source_ids: list[str]) -> dict:
    """Apply the rules to the open relations of these sources, in one run (caller need not commit)."""
    conn.row_factory = sqlite3.Row
    run_id = f"run_{uuid.uuid4().hex}"
    counts: Counter = Counter()
    registry = _registry(conn)
    with conn:
        conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                     (run_id, canonical_json({"job": "rules", "rule": RULE, "sources": len(source_ids)}), utc_now()))
        for sid in source_ids:
            documents, first, speakers = _document_names(conn, sid), _first_page(conn, sid), _source_speakers(conn, sid)
            for rel in _open_relations(conn, sid):
                if _settled(conn, rel["id"]):
                    counts["kept_settled"] += 1
                    continue
                evidence = _evidence(conn, rel["id"])
                if evidence is None:
                    continue
                rulings = check_relation(conn, rel, evidence, documents=documents, registry=registry,
                                         first_page=first, speakers=speakers)
                for action, values, reason, rule in rulings:
                    _record(conn, run_id, rel["id"], action, values, reason, evidence["quote"], rule)
                    counts[f"{rule}:{action}"] += 1
                if any(r[0] == "confirm" for r in rulings):
                    counts["folded"] += _fold(conn, run_id, sid, rel["id"])
        conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ? WHERE id = ?", (utc_now(), run_id))
    return {"run_id": run_id, "sources": len(source_ids), "counts": dict(sorted(counts.items()))}


def _record(conn, run_id: str, relation_id: str, action: str, values: dict, reason: str, quote: str,
            rule: str) -> None:
    from .review import _set
    state = {"reject": "rejected", "confirm": "confirmed"}.get(action)
    new = dict(values) | ({"review_state": state} if state else {})
    before = _set(conn, "relation", relation_id, new)
    record_decision(conn, actor_kind="model", actor_id=ACTOR_ID, run_id=run_id, action=action,
                    target_table="relation", target_id=relation_id, quote=quote, reason=f"[{rule}] {reason}",
                    audit_verdict={"reject": "fail", "confirm": "pass"}.get(action, "not_audited"),
                    previous_state=before, new_state=new | {"rule": rule})


def _fold(conn, run_id: str, source_id: str, relation_id: str) -> int:
    """One record per relation: a just-confirmed relation identical (same names, same type) to one already
    confirmed in the source is folded into that one; open copies are folded into it."""
    from .review import _set
    row = conn.execute("SELECT * FROM relation WHERE id = ?", (relation_id,)).fetchone()
    copies = [other for other in conn.execute(
        """SELECT DISTINCT r.* FROM relation r JOIN relation_usage ru ON ru.relation_id = r.id
           JOIN usage u ON u.id = ru.usage_id JOIN passage p ON p.id = u.passage_id
           JOIN source_version v ON v.id = p.version_id
           WHERE v.source_id = ? AND r.id <> ? AND r.review_state IN ('proposed', 'disputed', 'confirmed')
           AND r.type = ? ORDER BY r.created_at, r.id""", (source_id, relation_id, row["type"])).fetchall()
              if bare(other["subject_label"]) == bare(row["subject_label"])
              and same_body(other["object_label"], row["object_label"]) and not _settled_by_people(conn, other["id"])]
    kept = next((c for c in copies if c["review_state"] == "confirmed"), None)
    pairs = [(relation_id, kept["id"])] if kept else [(c["id"], relation_id) for c in copies]
    for folded, into in pairs:
        before = _set(conn, "relation", folded, {"review_state": "superseded"})
        record_decision(conn, actor_kind="model", actor_id=ACTOR_ID, run_id=run_id, action="merge",
                        target_table="relation", target_id=folded, audit_verdict="pass",
                        reason=f"Same relation as {into}, confirmed in this source; folded into it so it is "
                               "counted once.",
                        previous_state=before, new_state={"review_state": "superseded", "merged_into": into,
                                                          "rule": "fold"})
    return len(pairs)


def _settled_by_people(conn, relation_id: str) -> bool:
    """A reviewer's or the researcher's ruling (not a rule's)."""
    from .reconcile import _effective_last
    last = _effective_last(conn, "relation", relation_id, skip_audits=True)
    return bool(last) and last["action"] != "propose" and not last["actor_id"].startswith("rule:")
