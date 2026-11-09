"""Academic articles by DOI: metadata, abstract and open-access copies.

Publisher sites (Taylor & Francis, SAGE, Wiley...) often block the fetcher,
and their Wayback captures are paywall pages or redirect loops. When a
target's URL contains a DOI:

* Crossref (``api.crossref.org/works/{doi}``) gives the registered title,
  journal, publisher, date and authors, and sometimes the abstract (JATS).
* OpenAlex (``api.openalex.org/works/doi:{doi}``) gives the abstract as a
  word-position index (rebuilt word by word, flagged
  ``abstract_openalex``) and the known open-access locations.

The abstract (title + abstract, the article's own words) is stored as a text
version flagged ``doi_abstract``; an open-access copy, preferring repository
copies, is fetched like any other page (robots.txt obeyed) and flagged
``open_access_copy``. A researcher's own PDF of the article, attached later,
is the full text for closed articles.

No email address or API key is sent. Both APIs are reached through the
Fetcher (honest User-Agent, per-host pacing); robots.txt does not apply to
these APIs, which exist for programs.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote

from .extract import canonicalize
from .fetch import Fetcher, FetchResult
from .urls import current_url

CROSSREF = "https://api.crossref.org/works/"
OPENALEX = "https://api.openalex.org/works/doi:"
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>#?&]+)", re.IGNORECASE)
_TRAILING = re.compile(r"(?:/(?:full|abstract|abs|pdf|epdf|pdfdirect|html|fulltext|summary|"
                       r"references|figures|meta|suppl_file))+/?$|~.*$|[.,;:)\]]+$|/$", re.IGNORECASE)
MAX_OA_COPIES = 2


def doi_candidates(url: str) -> list[str]:
    """DOIs to try for a URL, most specific first (trailing slugs dropped one by one)."""
    match = _DOI.search(unquote(url or ""))
    if not match:
        return []
    doi = match.group(1)
    previous = None
    while previous != doi:
        previous, doi = doi, _TRAILING.sub("", doi)
    parts = doi.split("/")
    candidates = []
    while len(parts) >= 2 and parts[1]:
        candidate = "/".join(parts).lower()
        if candidate not in candidates:
            candidates.append(candidate)
        if len(parts) == 2:
            break
        parts = parts[:-1]
    return candidates[:4]


def jats_to_text(value: str | None) -> str:
    """Crossref JATS abstract -> plain text (paragraphs kept, heading 'Abstract' dropped)."""
    if not value:
        return ""
    text = re.sub(r"<jats:title>\s*(abstract|summary)\s*</jats:title>", "", value, flags=re.I)
    text = re.sub(r"</(jats:)?(p|title|sec)>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return canonicalize(html.unescape(text))


def rebuild_inverted_index(index: dict | None) -> str:
    """OpenAlex abstract_inverted_index -> words in order, single spaces."""
    if not index:
        return ""
    positions = sorted((p, word) for word, places in index.items() for p in places)
    return canonicalize(" ".join(word for _, word in positions))


def clean_markup(value: str | None) -> str | None:
    """Crossref titles carry HTML/JATS: 'Gay &amp; Lesbian', '<i>man</i>'."""
    if not value:
        return value
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", value))).strip() or None


def _date_parts(parts) -> str | None:
    try:
        values = [int(v) for v in parts[0] if v is not None]
    except (TypeError, IndexError, ValueError):
        return None
    if not values:
        return None
    return "-".join([f"{values[0]:04d}"] + [f"{v:02d}" for v in values[1:3]])


@dataclass
class DoiRecord:
    doi: str
    title: str | None = None
    container: str | None = None
    publisher: str | None = None
    date: str | None = None
    work_type: str | None = None
    authors: list = field(default_factory=list)
    abstract: str = ""
    abstract_source: str | None = None      # 'crossref' | 'openalex'
    is_oa: bool | None = None
    oa_urls: list = field(default_factory=list)   # (url, version, source type)
    crossref: dict | None = None
    openalex: dict | None = None

    def abstract_text(self) -> str:
        """Title and abstract, the article's own words."""
        return canonicalize("\n\n".join(p for p in (self.title, self.abstract) if p))


class DoiResolver:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher

    def _json(self, url: str) -> tuple[dict | None, FetchResult]:
        result = self.fetcher.get(url, route="api")
        if not result.ok:
            return None, result
        try:
            return json.loads(result.body), result
        except ValueError:
            result.outcome, result.error, result.body = "http_error", "reply is not JSON", None
            return None, result

    def lookup(self, url: str) -> tuple[DoiRecord | None, list[FetchResult]]:
        """Resolve the URL's DOI; returns the record (or None) and every API result."""
        results: list[FetchResult] = []
        record = None
        for doi in doi_candidates(url):
            data, result = self._json(CROSSREF + quote(doi, safe="/"))
            results.append(result)
            if data and isinstance(data.get("message"), dict):
                record = self._from_crossref(doi, data["message"])
                break
            if result.outcome not in ("not_found", "gone"):
                break                    # service trouble: do not guess shorter DOIs
        candidates = [record.doi] if record else doi_candidates(url)[:1]
        for doi in candidates:
            data, result = self._json(OPENALEX + quote(doi, safe="/"))
            results.append(result)
            if data and data.get("id"):
                record = record or DoiRecord(doi=doi)
                self._add_openalex(record, data)
        return record, results

    @staticmethod
    def _from_crossref(doi: str, m: dict) -> DoiRecord:
        date = None
        for key in ("issued", "published-print", "published-online", "created"):
            date = _date_parts((m.get(key) or {}).get("date-parts"))
            if date:
                break
        authors = [" ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name", "")
                   for a in m.get("author") or []]
        record = DoiRecord(
            doi=(m.get("DOI") or doi).lower(), title=clean_markup((m.get("title") or [None])[0]),
            container=clean_markup((m.get("container-title") or [None])[0]),
            publisher=clean_markup(m.get("publisher")),
            date=date, work_type=m.get("type"), authors=[a for a in authors if a], crossref=m)
        abstract = jats_to_text(m.get("abstract"))
        if abstract:
            record.abstract, record.abstract_source = abstract, "crossref"
        return record

    @staticmethod
    def _add_openalex(record: DoiRecord, d: dict) -> None:
        record.openalex = d
        record.title = record.title or clean_markup(d.get("title"))
        record.date = record.date or d.get("publication_date")
        record.work_type = record.work_type or d.get("type")
        if not record.authors:
            record.authors = [(a.get("author") or {}).get("display_name") for a in d.get("authorships") or []
                              if (a.get("author") or {}).get("display_name")]
        if not record.container:
            record.container = ((d.get("primary_location") or {}).get("source") or {}).get("display_name")
        if not record.abstract:
            abstract = rebuild_inverted_index(d.get("abstract_inverted_index"))
            if abstract:
                record.abstract, record.abstract_source = abstract, "openalex"
        record.is_oa = (d.get("open_access") or {}).get("is_oa")
        seen, urls = set(), []
        locations = [d.get("best_oa_location") or {}] + list(d.get("locations") or [])
        locations.sort(key=lambda loc: ((loc.get("source") or {}).get("type") != "repository"))
        for loc in locations:
            url = loc.get("pdf_url") or (loc.get("landing_page_url") if loc.get("is_oa") else None)
            url = current_url(url) if url else url
            if loc.get("is_oa") and url and url not in seen and url.startswith("http"):
                seen.add(url)
                urls.append((url, loc.get("version") or "unknown", (loc.get("source") or {}).get("type")))
        record.oa_urls = urls
