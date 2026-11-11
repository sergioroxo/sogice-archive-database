"""Source authors: who wrote a text, from what its preserved copies and its DOI record say.

The readers were told a source's title and publisher but never its author. So an essay a person wrote
and an organisation published was credited to the organisation, and the two readers disagreed over
whether a journal or "the authors" speak a scholarly article: most of the holdout's 254 disputes
(docs/TESTSET.md). Software only, no model; the source's own copies and its merged copies are used:

1. **DOI record**: the authors registered with Crossref (or OpenAlex), from the reply stored when the
   source was fetched.
2. **Page code**: ``citation_author`` tags, a structured-data author who is a person, the author meta
   tags (author, article:author, dc.creator, parsely-author, byl), Yoast's "Written by" label, a
   ``rel="author"`` link. A name from page code is used only when the page also prints it: a theme's or
   webmaster's name that the page never shows is not the text's author.
3. **PDF metadata**: the Author field, only when the first page prints it.
4. **First page, read by the titles model** (``archive/titles.py``, prompt title-v2): the people it named
   as authors of a PDF, Word or text copy, each located on that page by software, when the earlier
   reading (title-v1) had named one of them as the publisher. Only when 1–3 name no one.

Names that are not a person's are dropped: generic ones ("admin", "Staff"), addresses, links, the site's
or publisher's own name. Copies that name different authors are left alone. Each author set is an
``edit`` decision on the source with the method, the copy and the span, so it can be undone with
``review undo --run``; an author a researcher set is never changed.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import subprocess
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from . import paths
from .db import canonical_json, record_decision, utc_now

RULE = "authors/v1"
ACTOR_ID = f"rule:{RULE}"
MAX_NAMES = 10
SHOWN_NAMES = 5          # names the readers see; the rest are counted
FIRST_PAGE_CHARS = 3000

GENERIC = frozenset({
    "admin", "administrator", "staff", "editor", "editors", "editorial", "editorial team", "editorial staff",
    "webmaster", "web master", "web team", "web editor", "user", "guest", "guest author", "guest post", "author",
    "authors", "unknown", "anonymous", "anon", "team", "the team", "our team", "redaction", "rédaction",
    "redacción", "redação", "redazione", "redaktion", "redactie", "staff writer", "staff reporter", "news desk",
    "newsdesk", "news team", "press office", "communications", "comms", "info", "contact", "office", "owner",
    "default", "wordpress", "microsoft office user", "microsoft word", "windows user", "acrobat", "adobe",
    "pdf", "home", "blog", "news", "site", "website", "contributor", "contributors", "correspondent",
    "agency", "agencies", "afp", "reuters", "associated press", "ap", "lusa", "efe", "dpa", "ansa",
    "pa media", "press association", "europa press", "the editors", "guest writer", "guest contributor", "guest blogger",
    "editor in chief", "editor-in-chief", "admin user", "site admin", "site administrator"})
# Words that make a name an organisation's or a topic's, never a person's ("McDermott Will & Emery LLP",
# "Queer theory"). Kept to words that are not also common surnames.
ORG_WORDS = frozenset({
    "llp", "inc", "ltd", "llc", "gmbh", "plc", "corp", "corporation", "company", "society", "association",
    "institute", "institution", "foundation", "university", "universidad", "université", "universität",
    "universidade", "college", "academy", "ministry", "ministries", "council", "committee", "commission",
    "centre", "center", "network", "coalition", "alliance", "federation", "department", "government",
    "parliament", "assembly", "project", "campaign", "initiative", "journal", "magazine", "services", "agency",
    "organisation", "organization", "fellowship", "laboratory", "theory", "identity", "sexual", "gender",
    "participant", "respondent", "witness", "international", "global"})
# Words that make a comma-separated part a role or affiliation ("Jaco van Zyl, Clinical Psychologist").
ROLE_WORDS = frozenset({
    "psychologist", "psychotherapist", "therapist", "counsellor", "counselor", "psychiatrist", "professor", "prof",
    "lecturer", "researcher", "director", "editor", "writer", "reporter", "correspondent", "journalist",
    "contributor", "columnist", "staff", "chair", "chairman", "chairwoman", "ceo", "president", "founder", "pastor",
    "minister", "phd", "ph.d", "md", "lcsw", "lmft", "lpc", "msc", "ma", "ba", "mba", "rn", "clinical", "senior",
    "associate", "assistant", "intern", "student", "fellow", "member", "volunteer", "coordinator", "manager",
    "officer", "executive", "trustee", "spokesperson", "spokesman", "spokeswoman", "jornalista", "periodista",
    "journaliste", "rédacteur", "redactor", "correspondente", "correspondant", "university", "universidade",
    "universidad", "université", "college", "institute", "school", "department", "centre", "center"})
# A name does not end in a function word: "Council on" is a fragment.
_TAIL = frozenset({"on", "of", "for", "and", "the", "to", "in", "at", "de", "del", "la", "le", "du", "des", "da",
                   "do", "von", "van", "y", "e", "et", "und", "a", "an"})
_LEAD = re.compile(r"^(?:(?:view|see|read)\s+(?:all\s+)?(?:posts|articles|stories)\s+by|all\s+posts\s+by|"
                   r"posts\s+by|more\s+(?:posts|articles|stories)\s+(?:by|from)|articles\s+by|written\s+by|"
                   r"posted\s+by|published\s+by|by|par|por|von|door|di|av|af|author|autor|auteur|autore)\s*:?\s+",
                   re.I)
ARTICLE_TYPES = frozenset({"article", "newsarticle", "blogposting", "scholarlyarticle", "report", "opinionnewsarticle",
                           "analysisnewsarticle", "reportagenewsarticle", "reviewnewsarticle", "backgroundnewsarticle",
                           "socialmediaposting", "creativework", "book", "chapter", "techarticle", "posting"})
AUTHOR_META = ("author", "article:author", "dc.creator", "dcterms.creator", "dc.contributor.author",
               "parsely-author", "sailthru.author", "byl")
METHODS = ("citation_author", "structured data", "author meta tag", "written-by label", "author link")

_META_TAG = re.compile(r"<meta\b[^>]*>", re.I | re.S)
_ATTR = re.compile(r"([\w:.-]+)\s*=\s*(['\"])(.*?)\2", re.S)
_LDJSON = re.compile(r"<script\b[^>]*application/ld\+json[^>]*>(.*?)</script\s*>", re.I | re.S)
_AUTHOR_LINK = re.compile(r"<a\b[^>]*\brel=['\"][^'\"]*\bauthor\b[^'\"]*['\"][^>]*>(.*?)</a\s*>", re.I | re.S)
_WORD_SEPARATORS = re.compile(r"\s+(?:and|&|e|y|et|und)\s+", re.I)


# --------------------------------------------------------------------------- names

def clean_name(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    value = re.sub(r"\s+", " ", value).strip(" \t\n|–—-·,;:")
    return _LEAD.sub("", value).strip(" \t\n|–—-·,;:")


def _squash(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.casefold())


def plausible(name: str, others: set[str], *, one_word_ok: bool = False) -> bool:
    """A person's name: not generic, not an address or link, not the site's or publisher's own name."""
    if not (3 <= len(name) <= 80) or not re.search(r"[^\W\d_]", name):
        return False
    low = name.casefold()
    if low in GENERIC or re.search(r"@|https?:|www\.|/|\\|\d|\.(com|org|net)\b|profil", low):
        return False
    words = name.split()
    if len(words) > 8 or (len(words) < 2 and not one_word_ok) or words[-1].casefold() in _TAIL:
        return False
    if {w.strip(".,()&").casefold() for w in words} & ORG_WORDS:
        return False
    squashed = _squash(name)
    return not any(o and (squashed == o or squashed in o or o in squashed) for o in others if len(o) >= 4)


def split_names(value: str, *, one_name: bool = False) -> list[str]:
    """The names in an author value. 'Jane Doe; John Smith' and 'Jane Doe and John Smith' are two names
    (the second only when every part is a full name); after a comma, a part that is a role, an affiliation
    or a single word is dropped ('Sílvia Maia, Lusa'). ``one_name``: the value is one name, perhaps
    'Last, First' (citation_author, dc.creator)."""
    value = clean_name(value)
    if not value:
        return []
    if one_name:
        m = re.fullmatch(r"([^,]+),\s*([^,]+)", value)
        return [f"{m.group(2).strip()} {m.group(1).strip()}" if m else value]
    names = []
    for part in (clean_name(x) for x in value.split(";")):
        pieces = [clean_name(x) for x in part.split(",")]
        kept = [pieces[0]] + [r for r in pieces[1:] if len(r.split()) >= 2 and r.casefold() not in GENERIC
                              and not ({w.strip(".()").casefold() for w in r.split()} & ROLE_WORDS)]
        for piece in (k for k in kept if k):
            words = [w for w in (clean_name(x) for x in _WORD_SEPARATORS.split(piece)) if w]
            organisation = {w.strip(".,()&").casefold() for w in piece.split()} & ORG_WORDS   # "… Will & Emery LLP"
            names += words if len(words) > 1 and all(len(w.split()) >= 2 for w in words) and not organisation \
                else [piece]
    return names


def as_text(names: list[str]) -> str:
    names = list(dict.fromkeys(names))
    kept = names[:MAX_NAMES]
    return "; ".join(kept) + (f"; and {len(names) - len(kept)} others" if len(names) > len(kept) else "")


def shown(author: str) -> str:
    """The author line the readers see: up to SHOWN_NAMES names, the rest counted."""
    names = [n for n in author.split("; ") if not n.startswith("and ")]
    extra = re.search(r"; and (\d+) others$", author)
    more = len(names) - SHOWN_NAMES + (int(extra.group(1)) if extra else 0)
    return "; ".join(names[:SHOWN_NAMES]) + (f"; and {more} others" if more > 0 else "")


# --------------------------------------------------------------------------- page code

def _metas(page: str) -> list[dict]:
    out = []
    for tag in _META_TAG.finditer(page):
        attrs = {}
        for a in _ATTR.finditer(tag.group(0)):
            attrs[a.group(1).casefold()] = (a.group(3), tag.start() + a.start(3), tag.start() + a.end(3))
        key = (attrs.get("name") or attrs.get("property") or (None,))[0]
        if key and "content" in attrs:
            value, start, end = attrs["content"]
            out.append({"key": key.casefold().strip(), "value": value, "start": start, "end": end})
    return out


def _types(node: dict) -> set[str]:
    t = node.get("@type")
    return {str(x).casefold() for x in (t if isinstance(t, list) else [t]) if x}


def _ld_authors(page: str) -> list[tuple[str, int, int]]:
    """Authors who are people, of the article objects in the page's structured data (name, start, end)."""
    found = []
    for block in _LDJSON.finditer(page):
        try:
            data = json.loads(block.group(1), strict=False)
        except ValueError:
            continue
        nodes: list[dict] = []

        def walk(x):
            if isinstance(x, dict):
                nodes.append(x)
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(data)
        by_id = {n["@id"]: n for n in nodes if isinstance(n.get("@id"), str) and len(n) > 1}
        articles = [n for n in nodes if _types(n) & ARTICLE_TYPES and n.get("author")]
        for art in articles:
            authors = art["author"] if isinstance(art["author"], list) else [art["author"]]
            for a in authors:
                if isinstance(a, dict) and a.get("@id") in by_id and not a.get("name"):
                    a = by_id[a["@id"]]
                if isinstance(a, dict):
                    if _types(a) and "person" not in _types(a):
                        continue                  # an organisation as author: that is the publisher
                    name = a.get("name")
                else:
                    name = a
                if not isinstance(name, str) or not name.strip():
                    continue
                start = page.find(name, block.start(1), block.end(1))
                if start < 0:
                    escaped = json.dumps(name)[1:-1]
                    start = page.find(escaped, block.start(1), block.end(1))
                    end = start + len(escaped)
                else:
                    end = start + len(name)
                if start < 0:
                    start, end = block.start(1), block.end(1)
                found.append((name, start, end))
    return found


def page_candidates(page: str, url: str, others: set[str]) -> list[dict]:
    """Author candidates in a web page's code, best first, each with names the page also prints."""
    from .fingerprints import visible_text
    printed = _squash(visible_text(page))
    out = []

    def add(method: str, raw: list[tuple[str, int, int]]) -> None:
        names, spans = [], []
        for value, start, end in raw:
            for name in split_names(value, one_name=method == "citation_author"):
                if plausible(name, others) and _squash(name) in printed and name not in names:
                    names.append(name)
                    spans.append((start, end))
        if names:
            start, end = spans[0]
            out.append({"method": method, "names": names, "char_start": start, "char_end": end,
                        "snippet": page[start:end][:500]})

    metas = _metas(page)
    add("citation_author", [(m["value"], m["start"], m["end"]) for m in metas if m["key"] == "citation_author"])
    add("structured data", _ld_authors(page))
    add("author meta tag", [(m["value"], m["start"], m["end"]) for m in metas if m["key"] in AUTHOR_META
                            and m["key"] not in ("dc.creator", "dcterms.creator", "dc.contributor.author")])
    add("author meta tag", [(m["value"], m["start"], m["end"]) for m in metas  # one name per tag
                            if m["key"] in ("dc.creator", "dcterms.creator", "dc.contributor.author")])
    labels = {m["key"][-1]: m["value"].casefold() for m in metas if re.fullmatch(r"twitter:label\d", m["key"])}
    add("written-by label", [(m["value"], m["start"], m["end"]) for m in metas
                             if re.fullmatch(r"twitter:data\d", m["key"])
                             and labels.get(m["key"][-1], "").startswith(("written by", "escrito por", "écrit par"))])
    links = [(m.group(1), m.start(1), m.end(1)) for m in _AUTHOR_LINK.finditer(page)]
    if len({_squash(clean_name(v)) for v, _, _ in links}) <= 2:     # more: a listing of several posts
        add("author link", links)
    return out


# --------------------------------------------------------------------------- the archive

def _family(conn: sqlite3.Connection, source_id: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "WITH RECURSIVE family(id) AS (SELECT ? UNION SELECT s.id FROM source s JOIN family f ON s.merged_into = f.id)"
        " SELECT id FROM family", (source_id,))]


def _others(source: sqlite3.Row | dict) -> set[str]:
    """The site's and publisher's own names: an author equal to them is the organisation, not a person."""
    from .fingerprints import site_of
    site = site_of(source["url"] or "")
    host = (urlsplit(source["url"] or "").hostname or "").lower().removeprefix("www.")
    return {_squash(x) for x in (source["publisher"], site, site.split(".")[0], host) if x}


def doi_authors(conn: sqlite3.Connection, family: list[str], base: Path) -> dict | None:
    """Authors in the Crossref (or OpenAlex) reply stored when a DOI source was fetched."""
    marks = ",".join("?" for _ in family)
    rows = conn.execute(f"""SELECT fa.detail FROM fetch_attempt fa JOIN source_target t ON t.id = fa.target_id
                            WHERE t.source_id IN ({marks}) AND (json_extract(fa.detail, '$.crossref_path') IS NOT NULL
                            OR json_extract(fa.detail, '$.openalex_path') IS NOT NULL)
                            ORDER BY fa.rowid DESC""", family).fetchall()
    for (detail,) in rows:
        d = json.loads(detail)
        for key, label in (("crossref_path", "Crossref record"), ("openalex_path", "OpenAlex record")):
            if not d.get(key):
                continue
            try:
                record = json.loads((base / d[key]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if key == "crossref_path":
                names = [" ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name", "")
                         for a in record.get("author") or [] if a.get("sequence") in (None, "first", "additional")]
            else:
                names = [(a.get("author") or {}).get("display_name", "") for a in record.get("authorships") or []]
            names = [clean_name(n) for n in names if n and plausible(clean_name(n), set(), one_word_ok=True)]
            if names:
                return {"method": label, "names": names, "path": d[key], "doi": d.get("doi"),
                        "snippet": "; ".join(names)[:500]}
    return None


def _pdf_field(path: Path, field: str) -> str | None:
    try:
        out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(rf"^{field}:\s*(.+)$", out, re.M)
    return m.group(1).strip() if m else None


def pdf_authors(conn: sqlite3.Connection, version: dict, base: Path, others: set[str]) -> dict | None:
    from .titles import first_page
    value = _pdf_field(base / version["path"], "Author")
    if not value:
        return None
    page = first_page(conn, version["id"])
    printed = _squash(page)
    names = [n for n in split_names(value) if plausible(n, others) and _squash(n) in printed]
    if not names:
        return None
    start = page.casefold().find(names[0].casefold())
    return {"method": "PDF metadata, printed on page 1", "names": names, "version_id": version["id"],
            "char_start": start if start >= 0 else None, "char_end": start + len(names[0]) if start >= 0 else None,
            "snippet": value[:500]}


def _shares_a_name(a: list[str], b: list[str]) -> bool:
    words = lambda names: {w for n in names for w in re.findall(r"\w+", n.casefold()) if len(w) > 2}
    return bool(words(a) & words(b))


def first_page_authors(conn: sqlite3.Connection, family: list[str], others: set[str]) -> dict | None:
    """The people the titles model named on a copy's first page, as software located them (archive/titles.py),
    when two readings agree: title-v1 had put one of them in the publisher field. Used only when nothing
    stronger names anyone. A single reading's names are kept in its decision but not used: on first
    pages it also names addressees and meeting members."""
    marks = ",".join("?" for _ in family)
    for d in conn.execute(f"""SELECT * FROM decision WHERE target_table = 'source' AND target_id IN ({marks})
                              AND action = 'edit' AND json_extract(new_state, '$.rule') LIKE 'titles/%'
                              AND json_extract(new_state, '$.authors') IS NOT NULL
                              AND json_extract(new_state, '$.authors_agree') = 1 ORDER BY seq DESC""", family):
        if conn.execute("SELECT 1 FROM decision WHERE reverts_decision_id = ?", (d["id"],)).fetchone():
            continue
        state = json.loads(d["new_state"])
        kept = [a for a in state["authors"] if plausible(a["name"], others)]
        if kept:
            return {"method": "first page, titles model", "names": [a["name"] for a in kept], "version_id": state.get("version_id"),
                    "char_start": kept[0]["char_start"], "char_end": kept[0]["char_end"],
                    "snippet": "; ".join(a["name"] for a in kept), "read_by": state.get("method"),
                    "titles_decision": d["id"]}
    return None


def find_author(conn: sqlite3.Connection, source: dict, base: Path) -> dict | None:
    """The best author evidence for a source and its merged copies, or None (``source['why']`` says why)."""
    family = _family(conn, source["id"])
    found = doi_authors(conn, family, base)
    if found:
        return found
    others = _others(source)
    marks = ",".join("?" for _ in family)
    versions = conn.execute(f"""SELECT id, path, lower(mime_type) AS mime, captured_at, created_at, quality_flags
                                FROM source_version WHERE source_id IN ({marks})
                                ORDER BY coalesce(captured_at, created_at) DESC""", family).fetchall()
    picks = []
    for v in versions:
        if "challenge_page" in (v["quality_flags"] or ""):
            continue
        if "html" in (v["mime"] or ""):
            try:
                page = (base / v["path"]).read_bytes().decode("utf-8", "replace")
            except OSError:
                continue
            candidates = page_candidates(page, source["url"] or "", others)
            if candidates:
                picks.append({**candidates[0], "version_id": v["id"]})
        elif "pdf" in (v["mime"] or ""):
            pick = pdf_authors(conn, dict(v), base, others)
            if pick:
                picks.append(pick)
    if not picks:
        found = first_page_authors(conn, family, others)
        if found:
            return found
        source["why"] = "no author found"
        return None
    best = min(picks, key=lambda p: METHODS.index(p["method"]) if p["method"] in METHODS else len(METHODS))
    if any(not _shares_a_name(p["names"], best["names"]) for p in picks):
        source["why"] = "copies disagree"
        source["candidates"] = [p["names"] for p in picks]
        return None
    return best


def sources_to_do(conn: sqlite3.Connection, source_ids: list[str] | None = None, again: bool = False) -> list[dict]:
    """Sources (not merged) without an author, or every one with ``again``; never one a researcher set."""
    from .reconcile import _effective_last
    where, params = ["s.merged_into IS NULL"], []
    if not again:
        where.append("(s.author IS NULL OR s.author = '')")
    if source_ids:
        where.append(f"s.id IN ({','.join('?' for _ in source_ids)})")
        params += source_ids
    out = []
    for s in conn.execute(f"SELECT * FROM source s WHERE {' AND '.join(where)} ORDER BY s.id", params).fetchall():
        last = _effective_last(conn, "source", s["id"], skip_audits=True)
        if last and last["actor_kind"] == "researcher":
            continue
        out.append(dict(s))
    return out


def _record(conn: sqlite3.Connection, *, run_id: str, source: dict, found: dict) -> None:
    from .review import _set
    author = as_text(found["names"])
    before = _set(conn, "source", source["id"], {"author": author})
    record_decision(conn, actor_kind="model", actor_id=ACTOR_ID, run_id=run_id, action="edit",
                    target_table="source", target_id=source["id"],
                    reason=f"[{RULE}] Author from the {found['method']}.", quote=found["snippet"][:1000],
                    previous_state=before,
                    new_state={"author": author, "method": found["method"], "rule": RULE,
                               **{k: found[k] for k in ("version_id", "char_start", "char_end", "path", "doi", "read_by",
                                                                 "titles_decision")
                                  if found.get(k) is not None}})


def assign(conn: sqlite3.Connection, *, source_ids: list[str] | None = None, again: bool = False,
           data_dir: Path | None = None, dry_run: bool = False, log=None) -> dict:
    """Set the author of every source that has none (or of all, with ``again``) where the evidence names one."""
    base = data_dir or paths.data_dir()
    sources = sources_to_do(conn, source_ids, again)
    counts: Counter = Counter(sources=len(sources))
    changes: list[dict] = []
    run_id = None
    if not dry_run:
        run_id = f"run_{uuid.uuid4().hex}"
        with conn:
            conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                         (run_id, canonical_json({"job": "authors", "rule": RULE, "sources": len(sources)}), utc_now()))
    for n, s in enumerate(sources, 1):
        found = find_author(conn, s, base)
        if not found:
            counts[s.get("why", "no author found")] += 1
            if s.get("why") == "copies disagree":
                changes.append({"source": s, "names": None, "method": "copies disagree", "candidates": s["candidates"]})
            continue
        if as_text(found["names"]) == (s.get("author") or ""):
            counts["unchanged"] += 1
            continue
        counts[found["method"]] += 1
        changes.append({**found, "source": s})
        if not dry_run:
            with conn:
                _record(conn, run_id=run_id, source=s, found=found)
        if log and n % 500 == 0:
            log(f"  {n}/{len(sources)} sources")
    if run_id:
        with conn:
            conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ?, inputs = ? WHERE id = ?",
                         (utc_now(), canonical_json({"job": "authors", "rule": RULE, "sources": len(sources),
                                                     "counts": dict(counts)}), run_id))
    return {"runs": [run_id] if run_id else [], "counts": dict(counts), "changes": changes}


def write_report(result: dict, folder: Path | None = None, *, dry_run: bool = False) -> Path:
    folder = folder or paths.reports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"authors-{time.strftime('%Y%m%d-%H%M%S')}{'-dry' if dry_run else ''}.md"
    cell = lambda v: (v or "").replace("|", "/")[:150]
    lines = ["# Source authors" + (" (dry run: nothing written)" if dry_run else ""), "",
             f"Rule {RULE}. Runs: {', '.join(result['runs']) or '–'} (undo with `python -m archive review undo --run …`).",
             "", "| Count | |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in result["counts"].items()]
    lines += ["", "| Source | Title | Author | How |", "|---|---|---|---|"]
    for c in result["changes"]:
        s = c["source"]
        author = as_text(c["names"]) if c.get("names") else " / ".join(as_text(x) for x in c.get("candidates", []))
        lines.append(f"| [{s['id']}]({s['url']}) | {cell(s.get('title'))} | {cell(author)} | {c['method']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
