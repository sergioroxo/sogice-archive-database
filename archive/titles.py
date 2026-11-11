"""Source titles: repair missing and poor titles from what the preserved copies say about themselves.

A source's title is shown to the readers in the header of every section, so a poor one becomes a poor
reading (a catalogue code, "r4501", was taken as the French report's name and became a speaker 111
times). Many titles came from the old queue and are missing ("Untitled"), generic ("Home"), a file name
("Microsoft Word - 127593789_8.docx"), a domain or a code.

Software first, then the small local model, and every title is located in a preserved copy:

1. **Page code** (no model): a web page's citation title, ``og:title``, ``twitter:title``, structured-data
   headline, ``<title>`` without the site's name, or first heading, in that order; the first that is not
   itself poor. A PDF's own metadata title counts only when it is also printed on its first page.
2. **First page** (``titles`` role in ``config/models.yaml``, prompt ``title-v2``): for PDFs, Word files
   and plain text, the model copies the title, the organisation that issued it and the people who wrote
   it from the first page; software locates each in that page's text, and what it cannot find is not
   used. The model quotes; software locates. The authors are kept in the decision for
   ``archive/authors.py``, which sets them when no stronger evidence names anyone.

Prompt ``title-v1`` asked for the issuer as "an institution, organisation, publisher or author", so a
third of the publishers it set were people. ``reissue`` re-reads those first pages with ``title-v2`` and
sets the publisher to the organisation it names, or clears it; the title is left as it is.

Each repair is an ``edit`` decision on the source with the old title (and publisher) as the previous
state, the method, the copy and the quoted span, so it can be undone with ``review undo --run``. A title a
researcher has set is never changed; a title that is not poor is never changed.
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
from .quote_locator import locate_quote

RULE = "titles/v2"
ACTOR_ID = f"rule:{RULE}"
PROMPT = "title-v2"
OLD_RULE = "titles/v1"          # its publishers mix issuers and authors: see reissue()
ROLE = "titles"
FIRST_PAGE_CHARS = 3000
MAX_TITLE = 300

GENERIC = frozenset({"untitled", "home", "accueil", "index", "inicio", "startseite", "welcome", "page", "document",
                     "untitled document", "home page", "homepage", "pdf", "resources", "news", "blog", "error",
                     "404", "page not found", "not found", "untitled-1", "document1", "titre", "titel", "título",
                     "sans titre", "ohne titel", "sin título", "presentation", "powerpoint presentation",
                     "voorpagina", "startpagina", "front page", "frontpage", "página inicial", "pagina iniziale",
                     "forside", "hjem", "start", "inici", "portada", "main page", "dive into anything",
                     "about", "about us", "contact", "contact us", "current issue", "faq", "donate", "events", "links",
                     "media", "videos", "who we are", "our story", "qui sommes-nous", "über uns", "quiénes somos",
                     "om oss", "sobre nós", "google drive", "google disk", "google docs"})
KINDS = ("report", "article", "book", "chapter", "law", "letter", "statement", "presentation", "form", "web page",
         "other")


# --------------------------------------------------------------------------- what counts as poor

def poor_reason(title: str | None, url: str = "") -> str | None:
    """Why a title is not a usable title, or None when it is fine."""
    t = re.sub(r"\s+", " ", title or "").strip()
    if not t:
        return "missing"
    low = t.casefold()
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if low in GENERIC:
        return "generic"
    if low.startswith("microsoft word - ") or low.startswith("microsoft powerpoint - ") or \
            re.search(r"\.(pdf|docx?|pptx?|x?html?|php|aspx?)(\s*[-–|]\s*google\s+(drive|disk|docs))?$", low):
        return "file name"
    if host and low.removeprefix("www.") in (host, host.split(".")[0]):
        return "domain"
    if not re.search(r"\s", t) and re.search(r"[\d_%]", t):
        return "code"
    if not re.search(r"\s", t) and len(t) <= 8:
        return "one short word"
    return None


# --------------------------------------------------------------------------- page code

def _meta(name: str) -> re.Pattern:
    return re.compile(r"<meta\b[^>]*?(?:name|property)=['\"]" + re.escape(name) + r"['\"][^>]*?content="
                      r"(?P<q>['\"])(?P<v>(?:(?!(?P=q)).){1,600})(?P=q)", re.I | re.S)


_CANDIDATES = (("citation_title", _meta("citation_title")), ("og:title", _meta("og:title")),
               ("twitter:title", _meta("twitter:title")))
_SITE_NAME = _meta("og:site_name")
_HEADLINE = re.compile(r"\"headline\"\s*:\s*\"(?P<v>(?:[^\"\\]|\\.){1,600})\"")
_TITLE = re.compile(r"<title[^>]*>(?P<v>.{1,600}?)</title>", re.I | re.S)
_H1 = re.compile(r"<h1\b[^>]*>(?P<v>.{1,600}?)</h1>", re.I | re.S)
_SEPARATORS = re.compile(r"\s+[|–—\-:·»>/]\s+")
_DATELINE = re.compile(r"^(published|posted|updated|last updated|date)\b", re.I)
# A parked or for-sale domain page says nothing about the source that used to be there.
_PARKED = re.compile(r"\b(buy\s+\S+\.[a-z]{2,}|\S+\.[a-z]{2,}\s+(?:is\s+)?for\s+sale|"
                     r"domain\s+(?:name\s+)?(?:is\s+)?for\s+sale|this\s+domain|"
                     r"parked\s+(?:free|domain)|is\s+for\s+sale|domain\s+expired|account\s+suspended)\b", re.I)


def _clean(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"\s+", " ", value).strip(" \t\n|–—-·»/")


def _without_site(title: str, site_name: str, host: str) -> str:
    """'Article title | Site name' -> 'Article title' when a leading or trailing part names the site.
    The kept part is sliced from the title as written (separators inside it stay as they were)."""
    cuts = [(m.start(), m.end()) for m in _SEPARATORS.finditer(title)]
    if not cuts:
        return title
    bounds = [0] + [e for _, e in cuts]
    ends = [a for a, _ in cuts] + [len(title)]
    parts = [(b, e) for b, e in zip(bounds, ends)]
    names = {site_name.casefold(), host.casefold(), host.split(".")[0].casefold()} - {""}

    def is_site(span: tuple[int, int]) -> bool:
        part = title[span[0]:span[1]].strip()
        if part.casefold() in GENERIC:          # "Home", "Accueil": not part of the page's title
            return True
        p = part.casefold().replace(" ", "")
        return len(p) >= 3 and any(n.replace(" ", "") in p or p in n.replace(" ", "") for n in names)

    while len(parts) > 1 and is_site(parts[-1]):
        parts.pop()
    while len(parts) > 1 and is_site(parts[0]):
        parts.pop(0)
    return title[parts[0][0]:parts[-1][1]].strip(" \t|–—-·»/>")


def page_candidates(page: str, url: str) -> list[dict]:
    """Title candidates in a web page's code, best first: method, title, char_start, char_end, snippet."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    root = urlsplit(url).path in ("", "/")
    site = _SITE_NAME.search(page)
    site_name = _clean(site.group("v")) if site else ""
    out = []

    def add(method: str, m: re.Match, value: str) -> None:
        title = value[:MAX_TITLE].strip(" \t|–—-·»/>")
        if poor_reason(title, url) or len(title) < 4 or _PARKED.search(title) or _DATELINE.match(title):
            return
        if not root and site_name and title.casefold() == site_name.casefold():
            return                                   # the site's name on an inner page is not the page's title
        out.append({"method": method, "title": title, "char_start": m.start("v"), "char_end": m.end("v"),
                    "snippet": page[m.start("v"):m.end("v")]})

    for method, pattern in _CANDIDATES:
        m = pattern.search(page)
        if m:
            add(method, m, _without_site(_clean(m.group("v")), site_name, host))
    m = _HEADLINE.search(page)
    if m:
        try:
            add("structured headline", m, _clean(json.loads(f'"{m.group("v")}"')))
        except ValueError:
            pass
    m = _TITLE.search(page)
    if m:
        add("title element", m, _without_site(_clean(m.group("v")), site_name, host))
    m = _H1.search(page)
    if m:
        add("first heading", m, _clean(m.group("v")))
    return out


def pdf_metadata_title(path: Path) -> str | None:
    """The title in a PDF's own metadata (poppler's pdfinfo), if any."""
    try:
        out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^Title:\s*(.+)$", out, re.M)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------- the archive

def first_page(conn: sqlite3.Connection, version_id: str) -> str:
    """The text of a copy's first page (up to the first page break), or its opening for unpaged text."""
    parts, size = [], 0
    for (text,) in conn.execute("SELECT text FROM passage WHERE version_id = ? ORDER BY sequence", (version_id,)):
        cut = text.find("\f")
        parts.append(text if cut < 0 else text[:cut])
        size += len(parts[-1])
        if cut >= 0 or size >= FIRST_PAGE_CHARS:
            break
    return "".join(parts)[:FIRST_PAGE_CHARS]


def poor_sources(conn: sqlite3.Connection, source_ids: list[str] | None = None) -> list[dict]:
    """Sources (not merged) whose title is poor, with their copies, skipping titles a researcher set."""
    from .reconcile import _effective_last
    where, params = ["s.merged_into IS NULL"], []
    if source_ids:
        where.append(f"s.id IN ({','.join('?' for _ in source_ids)})")
        params += source_ids
    out = []
    for s in conn.execute(f"SELECT * FROM source s WHERE {' AND '.join(where)} ORDER BY s.id", params).fetchall():
        reason = poor_reason(s["title"], s["url"])
        if not reason:
            continue
        last = _effective_last(conn, "source", s["id"], skip_audits=True)
        if last and last["actor_kind"] == "researcher":
            continue
        versions = conn.execute("""SELECT id, path, lower(mime_type) AS mime, acquisition_route, captured_at, created_at
                                   FROM source_version WHERE source_id = ?
                                   ORDER BY coalesce(captured_at, created_at) DESC""", (s["id"],)).fetchall()
        out.append({"id": s["id"], "url": s["url"], "title": s["title"], "publisher": s["publisher"],
                    "reason": reason, "versions": [dict(v) for v in versions]})
    return out


def html_candidates(source: dict, base: Path) -> list[list[dict]]:
    """Title candidates from each web-page copy (newest first), one list per copy that has any."""
    out = []
    for v in source["versions"]:
        if "html" not in (v["mime"] or ""):
            continue
        try:
            page = (base / v["path"]).read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        found = page_candidates(page, source["url"])
        if found:
            out.append([{**f, "version_id": v["id"]} for f in found])
    return out


def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"\w+", title.casefold()) if len(w) > 3}


def site_wide(candidates: dict[str, list[dict]], sources: dict[str, dict]) -> set[tuple[str, str]]:
    """(site, title) pairs that two or more different pages of one site give: a slogan or a section name
    ("Dive into anything"), not a page's title."""
    from .fingerprints import site_of
    seen: dict[tuple[str, str], set[str]] = {}
    for sid, copies in candidates.items():
        site = site_of(sources[sid]["url"])
        for f in (f for found in copies for f in found):
            seen.setdefault((site, f["title"].casefold()), set()).add(sid)
    return {key for key, sids in seen.items() if len(sids) >= 2}


def from_page_code(conn: sqlite3.Connection, source: dict, base: Path, copies: list[list[dict]] | None = None,
                   shared: set[tuple[str, str]] = frozenset()) -> dict | None:
    """The best title the copies' own code gives: web pages first (a title other pages of the site also
    give is skipped; copies whose titles share no word, such as an old capture and a domain now used by
    someone else, are left alone), then a PDF's metadata title when its first page prints it."""
    from .fingerprints import site_of
    site = site_of(source["url"])
    best = []
    for found in (copies if copies is not None else html_candidates(source, base)):
        pick = next((f for f in found if (site, f["title"].casefold()) not in shared), None)
        if pick:
            best.append(pick)
    if best:
        if any(not (_words(b["title"]) & _words(best[0]["title"])) for b in best[1:]):
            source["copies_disagree"] = [b["title"] for b in best]
            return None
        return best[0]
    for v in source["versions"]:
        if "pdf" not in (v["mime"] or ""):
            continue
        meta = pdf_metadata_title(base / v["path"])
        if not meta or poor_reason(meta, source["url"]):
            continue
        located = locate_quote(meta, [{"unit_id": "page1", "text": first_page(conn, v["id"])}])
        if located:
            return {"method": "PDF metadata, printed on page 1", "title": _clean(located["quote"]),
                    "char_start": located["start"], "char_end": located["end"], "snippet": located["quote"],
                    "version_id": v["id"]}
    return None


def _text_version(conn: sqlite3.Connection, source: dict) -> tuple[dict, str] | None:
    """The copy whose first page the model reads (PDF, Word, plain text), with that page's text."""
    order = {"pdf": 0, "word": 1, "msword": 1, "plain": 2}
    ranked = sorted((v for v in source["versions"] if any(k in (v["mime"] or "") for k in order)),
                    key=lambda v: min(order[k] for k in order if k in (v["mime"] or "")))
    for v in ranked:
        text = first_page(conn, v["id"])
        if len(text.strip()) >= 40:
            return v, text
    return None


def load_prompt(version: str = PROMPT) -> str:
    return (paths.REPO_ROOT / "prompts" / f"{version}.md").read_text(encoding="utf-8")


def response_schema() -> dict:
    return {"type": "object", "properties": {
        "title": {"type": "string"}, "issued_by": {"type": "string"},
        "authors": {"type": "array", "items": {"type": "string"}}, "kind": {"type": "string", "enum": list(KINDS)}},
        "required": ["title", "issued_by", "authors", "kind"]}


def ask_first_page(client, role, source: dict, text: str, prompt: str) -> tuple[dict, dict]:
    """Ask the model for title, issuer and authors on the first page; locate each. Returns (found, call info):
    ``found`` has ``title`` (located span or None), ``issuer`` (span or None) and ``authors`` (spans)."""
    from .authors import _squash, clean_name, plausible, split_names
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": f"Source URL: {source['url']}\n\nFirst page:\n{text}"}]
    extra = getattr(role, "extra", {}) or {}
    result = client.chat_json(role.model, messages, schema=response_schema(), temperature=role.temperature or 0,
                              num_predict=int(extra.get("num_predict", 512)), num_ctx=extra.get("num_ctx"),
                              keep_alive=extra.get("keep_alive", "10m"))
    info = {"seconds": result.seconds, "prompt_tokens": result.prompt_tokens, "output_tokens": result.output_tokens}
    data = result.data if isinstance(result.data, dict) else {}
    page = [{"unit_id": "page1", "text": text}]
    found = {"kind": data.get("kind"), "title": None, "issuer": None, "authors": [], "asked": data}
    if data.get("title"):
        found["title"] = locate_quote(data["title"], page)
    names = []
    for raw in (r for r in data.get("authors") or [] if isinstance(r, str)):
        for name in split_names(raw):              # "Jane Doe, Ph.D." -> "Jane Doe"
            located = locate_quote(name, page)
            if not located:
                continue
            name = clean_name(located["quote"])
            if plausible(name, set()) and _squash(name) not in {_squash(n["name"]) for n in names}:
                names.append({"name": name, "char_start": located["start"], "char_end": located["end"]})
    found["authors"] = names
    if data.get("issued_by"):
        issuer = locate_quote(data["issued_by"], page)
        if issuer and _squash(_clean(issuer["quote"])) not in {_squash(n["name"]) for n in names}:
            found["issuer"] = issuer                   # an issuer that is one of the authors is not an issuer
    return found, info


def from_first_page(client, role, source: dict, version: dict, text: str, prompt: str) -> tuple[dict | None, dict]:
    """A title repair from the first page, or None. Returns (repair or None, call info)."""
    found, info = ask_first_page(client, role, source, text, prompt)
    located = found["title"]
    if not located:
        return None, {**info, "outcome": "title not located" if found["asked"].get("title") else "no title"}
    title = _clean(located["quote"])[:MAX_TITLE]
    if poor_reason(title, source["url"]) or len(title) < 4:
        return None, {**info, "outcome": "title still poor"}
    issuer = found["issuer"]
    return ({"method": f"first page, read by {role.model} ({PROMPT})", "title": title,
             "publisher": _clean(issuer["quote"])[:200] if issuer else None, "kind": found["kind"],
             "authors": found["authors"],
             "char_start": located["start"], "char_end": located["end"], "snippet": located["quote"],
             "version_id": version["id"]}, {**info, "outcome": "located"})


def _record(conn: sqlite3.Connection, *, run_id: str, actor_id: str, source: dict, repair: dict) -> None:
    from .review import _set
    values = {"title": repair["title"]}
    if repair.get("publisher") and not source["publisher"]:
        values["publisher"] = repair["publisher"]
    before = _set(conn, "source", source["id"], values)
    record_decision(conn, actor_kind="model", actor_id=actor_id, run_id=run_id, action="edit",
                    target_table="source", target_id=source["id"],
                    reason=(f"[{RULE}] Title was {source['reason']} ({source['title']!r}); "
                            f"set from the copy's {repair['method']}."),
                    quote=repair["snippet"][:1000], previous_state=before,
                    new_state={**values, "method": repair["method"], "version_id": repair["version_id"],
                               "char_start": repair["char_start"], "char_end": repair["char_end"], "rule": RULE,
                               **({"authors": repair["authors"]} if repair.get("authors") else {})})


def repair(conn: sqlite3.Connection, *, client=None, role=None, source_ids: list[str] | None = None,
           exclude: set[str] = frozenset(), limit: int | None = None, data_dir: Path | None = None,
           dry_run: bool = False, log=None) -> dict:
    """Repair poor titles: page code first (software), then the first page (model, if a client is given).
    ``exclude``: sources to leave out of this run (found wrong when a trial was checked)."""
    base = data_dir or paths.data_dir()
    sources = [s for s in poor_sources(conn, source_ids) if s["id"] not in exclude]
    if limit:
        sources = sources[:limit]
    counts: Counter = Counter(poor=len(sources))
    changes: list[dict] = []
    soft_run = model_run = None
    if not dry_run:
        soft_run = f"run_{uuid.uuid4().hex}"
        with conn:
            conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                         (soft_run, canonical_json({"job": "titles", "rule": RULE, "sources": len(sources),
                                                        "excluded": sorted(exclude)}), utc_now()))
    left = []
    candidates = {s["id"]: html_candidates(s, base) for s in sources}
    shared = site_wide(candidates, {s["id"]: s for s in sources})
    for s in sources:
        found = from_page_code(conn, s, base, candidates[s["id"]], shared)
        if not found:
            if s.get("copies_disagree"):
                counts["copies disagree (left alone)"] += 1
                continue
            left.append(s)
            continue
        counts["page code"] += 1
        changes.append({**found, "source": s})
        if not dry_run:
            with conn:
                _record(conn, run_id=soft_run, actor_id=ACTOR_ID, source=s, repair=found)
    if client is not None and role is not None:
        prompt = load_prompt()
        started = time.monotonic()
        tokens = Counter()
        if not dry_run:
            model_run = f"run_{uuid.uuid4().hex}"
            with conn:
                conn.execute("INSERT INTO run(id, role, model, prompt_version, inputs, started_at) "
                             "VALUES (?, 'extraction', ?, ?, ?, ?)",
                             (model_run, role.model, PROMPT, canonical_json({"job": "titles", "rule": RULE}), utc_now()))
        try:
            for n, s in enumerate(left, 1):
                picked = _text_version(conn, s)
                if not picked:
                    counts["no text copy"] += 1
                    continue
                version, text = picked
                try:
                    found, info = from_first_page(client, role, s, version, text, prompt)
                except Exception as exc:                 # one failed call: retried once, then skipped
                    try:
                        found, info = from_first_page(client, role, s, version, text, prompt)
                    except Exception:
                        counts["model call failed"] += 1
                        if log:
                            log(f"  {s['id']}: failed ({type(exc).__name__})")
                        continue
                tokens["input"] += info.get("prompt_tokens") or 0
                tokens["output"] += info.get("output_tokens") or 0
                counts[f"first page: {info['outcome']}"] += 1
                if found:
                    changes.append({**found, "source": s})
                    if not dry_run:
                        with conn:
                            _record(conn, run_id=model_run, actor_id=role.model, source=s, repair=found)
                if log and n % 25 == 0:
                    log(f"  {n}/{len(left)} first pages read")
        finally:
            try:
                client.unload(role.model)
            except Exception:
                pass
        if not dry_run:
            with conn:
                conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ?, seconds = ?, input_tokens = ?,"
                             " output_tokens = ?, inputs = ? WHERE id = ?",
                             (utc_now(), round(time.monotonic() - started, 1), tokens["input"], tokens["output"],
                              canonical_json({"job": "titles", "rule": RULE, "sources": len(left),
                                              "counts": dict(counts)}), model_run))
    else:
        counts["left for the model"] = len(left)
    if soft_run:
        with conn:
            conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ?, inputs = ? WHERE id = ?",
                         (utc_now(), canonical_json({"job": "titles", "rule": RULE, "sources": len(sources),
                                                     "counts": dict(counts)}), soft_run))
    return {"runs": [r for r in (soft_run, model_run) if r], "counts": dict(counts), "changes": changes}


def reissue_sources(conn: sqlite3.Connection, source_ids: list[str] | None = None) -> list[dict]:
    """Sources whose publisher title-v1 set and nobody has changed since, with the copy it read."""
    from .reconcile import _effective_last
    out, seen = [], set()
    for d in conn.execute("""SELECT * FROM decision WHERE target_table = 'source' AND action = 'edit'
                             AND json_extract(new_state, '$.rule') = ?
                             AND json_extract(new_state, '$.publisher') IS NOT NULL ORDER BY seq DESC""", (OLD_RULE,)):
        sid = d["target_id"]
        if sid in seen or (source_ids and sid not in source_ids):
            continue
        seen.add(sid)
        state = json.loads(d["new_state"])
        s = conn.execute("SELECT * FROM source WHERE id = ?", (sid,)).fetchone()
        if s is None or s["merged_into"] or s["publisher"] != state["publisher"]:
            continue
        if conn.execute("SELECT 1 FROM decision WHERE reverts_decision_id = ?", (d["id"],)).fetchone():
            continue
        if conn.execute("""SELECT 1 FROM decision e WHERE e.target_table = 'source' AND e.target_id = ? AND e.seq > ?
                           AND json_extract(e.new_state, '$.rule') = ?
                           AND NOT EXISTS (SELECT 1 FROM decision u WHERE u.reverts_decision_id = e.id)""",
                        (sid, d["seq"], RULE)).fetchone():
            continue                                     # already read again with title-v2
        last = _effective_last(conn, "source", sid, skip_audits=True)
        if last and last["actor_kind"] == "researcher":
            continue
        out.append({"id": sid, "url": s["url"], "title": s["title"], "publisher": s["publisher"],
                    "version_id": state["version_id"], "decision_id": d["id"]})
    return sorted(out, key=lambda x: x["id"])


def _reissue_outcome(old: str, found: dict, url: str = "") -> tuple[str, str | None]:
    """What a second reading of the first page does to a publisher title-v1 set: (outcome, new publisher).

    - The old publisher is a person the new reading names as an author: the two readings agree on the
      author. The new issuer replaces the publisher only when it is the site's own name or domain (first
      pages often name an addressee, a committee or a court, as issuer); otherwise the publisher is cleared.
    - Both readings give the same publisher: kept.
    - Otherwise the old publisher stays and the source is listed for review: two readings that disagree
      are not settled here."""
    from .authors import _squash
    from .fingerprints import site_of
    was = _squash(old)
    same = lambda a, b: bool(a and b) and (a == b or (min(len(a), len(b)) >= 6 and (a in b or b in a)))
    issuer = _clean(found["issuer"]["quote"])[:200] if found["issuer"] else None
    if any(same(was, _squash(a["name"])) for a in found["authors"]):
        site = site_of(url) if url else ""                 # Wayback URLs count as the site they captured
        marks = {_squash(x) for x in (site, site.split(".")[0]) if x}
        if issuer and any(same(_squash(issuer), m) for m in marks):
            return "publisher was an author, replaced by the site's own name", issuer
        return "publisher was an author, cleared", None
    if issuer and same(was, _squash(issuer)):
        return "publisher kept", old
    return "readings differ, kept for review", old


def _record_reissue(conn: sqlite3.Connection, *, run_id: str, actor_id: str, source: dict, publisher: str | None,
                    outcome: str, found: dict, method: str) -> None:
    """A changed publisher is an edit, a kept one a confirmation, and a disagreement an 'uncertain' audit that
    changes nothing; each records the second reading, so the source is not asked about again."""
    from .review import _set
    before = _set(conn, "source", source["id"], {"publisher": publisher})
    names = [a["name"] for a in found["authors"]]
    span = found["issuer"]
    said = (f"issued by {_clean(span['quote'])!r}" if span else "no issuing organisation named") + \
           (f"; written by {'; '.join(names)}" if names else "")
    action, verdict = ("edit", "not_audited") if publisher != source["publisher"] else \
        ("confirm", "not_audited") if outcome == "publisher kept" else ("audit", "uncertain")
    record_decision(conn, actor_kind="model", actor_id=actor_id, run_id=run_id, action=action, audit_verdict=verdict,
                    target_table="source", target_id=source["id"],
                    reason=(f"[{RULE}] Publisher {source['publisher']!r} was set by {OLD_RULE}, whose prompt asked "
                            f"for an issuer 'or author'. The first page read again with {PROMPT}: {said}. "
                            f"Outcome: {outcome}."),
                    quote=(span["quote"] if span else "; ".join(names))[:1000] or None, previous_state=before,
                    new_state={"publisher": publisher, "method": method, "version_id": source["version_id"],
                               "rule": RULE, "outcome": outcome, "replaces_decision": source["decision_id"],
                               "issued_by": _clean(span["quote"])[:200] if span else None,
                               **({"char_start": span["start"], "char_end": span["end"]} if span else {}),
                               **({"authors": found["authors"]} if names else {}),
                               **({"authors_agree": True} if names and outcome.startswith("publisher was an author")
                                  else {})})


def reissue(conn: sqlite3.Connection, *, client, role, source_ids: list[str] | None = None,
            limit: int | None = None, dry_run: bool = False, log=None) -> dict:
    """Read again, with title-v2, the first pages whose publisher title-v1 set: the publisher becomes the
    organisation the page names as issuer, or none; the people who wrote it are kept for the authors step.
    The title is left as it is."""
    sources = reissue_sources(conn, source_ids)
    if limit:
        sources = sources[:limit]
    prompt = load_prompt()
    method = f"first page, read by {role.model} ({PROMPT})"
    counts: Counter = Counter(sources=len(sources))
    changes: list[dict] = []
    tokens: Counter = Counter()
    started = time.monotonic()
    run_id = None
    if not dry_run:
        run_id = f"run_{uuid.uuid4().hex}"
        with conn:
            conn.execute("INSERT INTO run(id, role, model, prompt_version, inputs, started_at) "
                         "VALUES (?, 'extraction', ?, ?, ?, ?)",
                         (run_id, role.model, PROMPT, canonical_json({"job": "titles reissue", "rule": RULE,
                                                                     "sources": len(sources)}), utc_now()))
    try:
        for n, s in enumerate(sources, 1):
            text = first_page(conn, s["version_id"])
            if len(text.strip()) < 40:
                counts["no text"] += 1
                continue
            try:
                found, info = ask_first_page(client, role, s, text, prompt)
            except Exception as exc:                     # one failed call: retried once, then skipped
                try:
                    found, info = ask_first_page(client, role, s, text, prompt)
                except Exception:
                    counts["model call failed"] += 1
                    if log:
                        log(f"  {s['id']}: failed ({type(exc).__name__})")
                    continue
            tokens["input"] += info.get("prompt_tokens") or 0
            tokens["output"] += info.get("output_tokens") or 0
            if found["authors"]:
                counts["authors named"] += 1
            outcome, issuer = _reissue_outcome(s["publisher"], found, s["url"] or "")
            counts[outcome] += 1
            changes.append({"source": s, "publisher": issuer, "outcome": outcome,
                            "asked_issuer": found["asked"].get("issued_by") or "",
                            "authors": [a["name"] for a in found["authors"]]})
            if not dry_run:                              # a kept publisher is confirmed, so it is not asked again
                with conn:
                    _record_reissue(conn, run_id=run_id, actor_id=role.model, source=s, publisher=issuer,
                                    outcome=outcome, found=found, method=method)
            if log and n % 25 == 0:
                log(f"  {n}/{len(sources)} first pages read")
    finally:
        try:
            client.unload(role.model)
        except Exception:
            pass
    if run_id:
        with conn:
            conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ?, seconds = ?, input_tokens = ?,"
                         " output_tokens = ?, inputs = ? WHERE id = ?",
                         (utc_now(), round(time.monotonic() - started, 1), tokens["input"], tokens["output"],
                          canonical_json({"job": "titles reissue", "rule": RULE, "sources": len(sources),
                                          "counts": dict(counts)}), run_id))
    return {"runs": [run_id] if run_id else [], "counts": dict(counts), "changes": changes}


def write_reissue_report(result: dict, folder: Path | None = None, *, dry_run: bool = False) -> Path:
    folder = folder or paths.reports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"publishers-{time.strftime('%Y%m%d-%H%M%S')}{'-dry' if dry_run else ''}.md"
    cell = lambda v: (v or "").replace("|", "/")[:120]
    lines = ["# Publishers set by title-v1, read again" + (" (dry run: nothing written)" if dry_run else ""), "",
             f"Rule {RULE}, prompt {PROMPT}. Runs: {', '.join(result['runs']) or '–'} "
             "(undo with `python -m archive review undo --run …`).", "", "| Count | |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in result["counts"].items()]
    lines += ["", "| Source | Title | Publisher was | Publisher now | Issuer, second reading | Authors on page 1 | Outcome |",
              "|---|---|---|---|---|---|---|"]
    for c in sorted(result["changes"], key=lambda c: c["outcome"]):
        s = c["source"]
        lines.append(f"| [{s['id']}]({s['url']}) | {cell(s['title'])} | {cell(s['publisher'])} | "
                     f"{cell(c['publisher']) or '–'} | {cell(c['asked_issuer']) or '–'} | "
                     f"{cell('; '.join(c['authors']))} | {c['outcome']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_report(result: dict, folder: Path | None = None, *, dry_run: bool = False) -> Path:
    folder = folder or paths.reports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"titles-{time.strftime('%Y%m%d-%H%M%S')}{'-dry' if dry_run else ''}.md"
    lines = ["# Source titles repaired" + (" (dry run: nothing written)" if dry_run else ""), "",
             f"Rule {RULE}. Runs: {', '.join(result['runs']) or '–'} (undo with `python -m archive review undo --run …`).",
             "", "| Count | |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in result["counts"].items()]
    lines += ["", "| Source | Was | Now | Publisher | How |", "|---|---|---|---|---|"]
    for c in result["changes"]:
        s = c["source"]
        cell = lambda v: (v or "").replace("|", "/")[:120]
        lines.append(f"| [{s['id']}]({s['url']}) | {cell(s['title'])} ({s['reason']}) | {cell(c['title'])} | "
                     f"{cell(c.get('publisher'))} | {c['method']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
