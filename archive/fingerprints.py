"""Fingerprints: identifiers in a page's own code that can show two sites are run by the same people.

Software only, no model, no network. Every preserved web page is scanned for identifiers that only a
site's owner, or whoever manages the site for them, can put there: analytics and tag-manager accounts,
Facebook apps and pixels, advertising, newsletter and donation accounts, charity, company and tax
numbers, the profiles a site declares as its own, and contact emails. The page's own self-description
(title, site name, the organisation named in its structured data, its copyright line) is kept too.

Each fingerprint is located in a preserved copy: ``char_start``/``char_end`` index the page code decoded
as UTF-8 (undecodable bytes replaced), so the snippet can always be found again in the stored object. It
is dated by the copy's capture and records where it sits in the page:

- ``script``: inside a script (the site's own code: analytics set-up, tracking);
- ``data``: inside structured data (schema.org JSON-LD, the site describing itself);
- ``meta``: in a meta tag;
- ``link``: in a tag's attribute (a link, a form, a button);
- ``comment``: inside an HTML comment (switched-off code);
- ``text``: in the visible text.

``shown`` says the value also appears in the page's visible text. A donation-button or tracking ID
that a page shows as text may be quoted by a page that documents another site (a watchdog report
listing a ministry's donation link), not used by it.

A shared fingerprint is a finding about page code, not a relation between organisations. The report
says "the page code of A and B carries the same X", with the strength of the kind of identifier:

- strong: charity, company and tax numbers; donation, newsletter and advertising accounts (one legal
  or financial owner);
- medium: analytics, tag-manager, Facebook app and pixel accounts, declared profiles (one web manager,
  which may be an agency working for several clients);
- weak: contact emails (directories and articles list other groups' addresses; personal addresses are
  personal data, used for matching only, never published).

Identifiers seen on a hosting platform's own site, or on more than ``MAX_SITES`` sites, belong to the
platform or a shared template and are listed apart. See ``docs/FINGERPRINTS.md``.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import paths
from .db import canonical_json, utc_now
from .urls import parse_wayback

SCANNER = "fingerprints/v1"
MAX_SITES = 15                      # more sites than this: a platform's or a template's identifier
MAX_PER_KIND = 50                   # occurrences kept per kind and page (directories list hundreds of emails)

# Hosting platforms: a site on one is its full host name (x.wordpress.com), and an identifier found on the
# platform's own site is the platform's, not evidence about the sites it hosts.
PLATFORMS = frozenset({
    "wordpress.com", "home.blog", "blogspot.com", "blogger.com", "tumblr.com", "teachable.com", "thinkific.com",
    "kajabi.com", "wixsite.com", "wix.com", "squarespace.com", "weebly.com", "webnode.com", "webnode.sk",
    "jimdo.com", "jimdofree.com", "godaddysites.com", "mailchimpsites.com", "substack.com", "medium.com",
    "github.io", "netlify.app", "vercel.app", "webs.com", "yolasite.com", "site123.me", "strikingly.com",
    "carrd.co", "podbean.com", "libsyn.com", "buzzsprout.com", "simplecast.com", "anchor.fm", "geocities.com",
    "angelfire.com", "tripod.com", "aol.com", "youtube.com", "facebook.com", "archive.org", "doi.org",
})
# Second-level labels under a country code that are not a site of their own (co.uk, org.au, gob.mx, …).
_SECOND_LEVEL = frozenset({"co", "org", "com", "ac", "gov", "net", "edu", "or", "ne", "gob", "nom", "ltd", "plc"})
_WEBMAIL = frozenset({"gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com",
                      "aol.com", "icloud.com", "me.com", "msn.com", "juno.com", "compuserve.com",
                      "btinternet.com", "earthlink.net", "comcast.net", "gmx.de", "web.de", "free.fr",
                      "orange.fr", "wanadoo.fr", "laposte.net", "proton.me", "protonmail.com"})
_EMAIL_NOISE = re.compile(r"(example\.|sentry|wixpress|\.(png|jpe?g|gif|webp|svg)$|@\dx|archive\.org|w3\.org|"
                          r"schema\.org|domain\.com|email\.com|yourdomain|yoursite|wordpress\.(org|com)|jquery)", re.I)
_PROFILE_HOSTS = ("facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "linkedin.com",
                  "tiktok.com", "vimeo.com", "t.me", "rumble.com", "patreon.com")


@dataclass(frozen=True)
class Kind:
    pattern: re.Pattern | None      # None: found by reading the page's structured data (JSON-LD)
    strength: str          # strong | medium | weak | self (self-description: never clustered)
    label: str
    shown_is_normal: bool = False   # printed on the page by its owner as a matter of course


def _p(regex: str, flags: int = 0) -> re.Pattern:
    return re.compile(regex, flags)


KINDS: dict[str, Kind] = {
    "uk_charity_number": Kind(_p(r"[Cc]harity\s+(?:[Rr]eg(?:istration|istered)?\.?\s*)?(?:[Nn]o\.?|[Nn]umber)"
                                 r"[:.\s]*(?P<v>[0-9]{6,7})(?![0-9])"), "strong", "UK charity number", True),
    "uk_company_number": Kind(_p(r"[Cc]ompany\s+(?:[Rr]eg(?:istration|istered)?\.?\s*)?(?:[Nn]o\.?|[Nn]umber)"
                                 r"[:.\s]*(?P<v>[0-9]{8}|SC[0-9]{6})(?![0-9])"), "strong", "UK company number", True),
    "us_ein": Kind(_p(r"\b(?:EIN|Tax\s*ID|[Tt]ax[- ]exempt\s+ID|[Ff]ederal\s+[Tt]ax\s+ID)[^0-9<]{0,30}"
                      r"(?P<v>\d{2}-\d{7})(?![0-9])"), "strong", "US tax number (EIN)", True),
    "paypal_button": Kind(_p(r"hosted_button_id(?:=|['\"]\s*value=['\"])(?P<v>[A-Z0-9]{10,16})\b"),
                          "strong", "PayPal donation button"),
    "mailchimp_account": Kind(_p(r"list-manage\.com/subscribe(?:/post)?(?:-json)?\?u=(?P<v>[0-9a-f]{20,32})"),
                              "strong", "Mailchimp account"),
    "adsense_publisher": Kind(_p(r"(?<!host-)\b(?P<v>(?:ca-)?pub-\d{12,20})\b"), "strong", "AdSense publisher"),
    "google_analytics_account": Kind(_p(r"\b(?P<v>UA-\d{4,10}-\d{1,4})\b"), "medium", "Google Analytics account"),
    "google_analytics_4": Kind(_p(r"\b(?P<v>G-[A-Z0-9]{8,12})\b"), "medium", "Google Analytics 4 stream"),
    "google_tag_manager": Kind(_p(r"\b(?P<v>GTM-[A-Z0-9]{5,8})\b"), "medium", "Google Tag Manager container"),
    "facebook_pixel": Kind(_p(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](?P<v>\d{12,20})"), "medium", "Facebook pixel"),
    "facebook_app": Kind(_p(r"fb:app_id['\"]\s+content=['\"](?P<v>\d{8,20})"), "medium", "Facebook app"),
    "declared_profile": Kind(None, "medium", "profile the site declares as its own (structured data)"),
    "email": Kind(_p(r"(?<![\w.%+-])(?P<v>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"), "weak",
                  "contact email", True),
    "asset_host": Kind(_p(r"<(?:script|img|link|iframe|source)\b[^>]*?\s(?:src|href)=['\"](?:https?:)?//"
                          r"(?P<v>[A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.I), "self", "server the page loads files from"),
    "title": Kind(_p(r"<title[^>]*>(?P<v>[^<]{1,300})</title>", re.I), "self", "page title", True),
    "site_name": Kind(_p(r"<meta[^>]+property=['\"]og:site_name['\"][^>]+content=(?P<q>['\"])(?P<v>(?:(?!(?P=q)).){1,200})(?P=q)",
                         re.I),
                      "self", "site name (og:site_name)"),
    "structured_name": Kind(None, "self", "organisation named in structured data"),
    "copyright": Kind(_p(r"(?:©|&copy;|&#169;|Copyright)\s*(?:\d{4}\s*[-–]\s*)?\d{4}\s*,?\s*(?:by\s+)?"
                         r"(?P<v>[A-Z][^<|\n\"]{2,80}?)(?=\s*(?:\.\s|\.?$|\.?\s*All\s+[Rr]ights|<|\||\n|&))"),
                    "self", "copyright line", True),
}
CLUSTERED = [k for k, v in KINDS.items() if v.strength != "self"]
# Identifiers a hosting platform or web provider puts on every site it serves (registration numbers,
# donation accounts and emails are the organisation's own and are never set apart this way).
PROVIDER_KINDS = frozenset({"google_analytics_account", "google_analytics_4", "google_tag_manager", "facebook_pixel",
                            "facebook_app", "adsense_publisher"})
# An identifier "follows a server" when most sites that load files from that server carry it: then it is
# probably the platform's or provider's (Weebly's own analytics on every Weebly site), not the sites' owners'.
PROVIDER_MIN_SITES = 3
PROVIDER_SHARE = 0.8
CONTEXT_MAX_SITES = 50              # servers used by more sites than this (fonts, big CDNs) are not worth showing
STRENGTH_ORDER = ("strong", "medium", "weak")

# schema.org types whose name and sameAs speak for the site itself (an article author's profiles do not).
_ORG_TYPES = frozenset({"Organization", "NGO", "NonprofitOrganization", "Church", "LocalBusiness", "Corporation",
                        "EducationalOrganization", "NewsMediaOrganization", "ReligiousOrganization", "WebSite",
                        "MedicalOrganization", "GovernmentOrganization", "ProfessionalService"})
_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
_STYLE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.S | re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TAG = re.compile(r"<[^>]*>")


# --------------------------------------------------------------------------- sites

def site_of(url: str) -> str:
    """The site a URL belongs to: its registrable domain (example.org, example.co.uk), or the full host for
    a site on a hosting platform (someone.wordpress.com). Wayback URLs count as the site they captured."""
    ref = parse_wayback(url)
    if ref:
        url = ref.original_url
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    for platform in PLATFORMS:
        if host.endswith("." + platform):
            return host
    if re.search(r"\.blogspot\.[a-z.]+$", host):
        return host
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_platform(site: str) -> bool:
    return site in PLATFORMS


def host_platform(site: str) -> str | None:
    """The hosting platform a site lives on (someone.wordpress.com -> wordpress.com), if any."""
    for platform in PLATFORMS:
        if site.endswith("." + platform):
            return platform
    m = re.search(r"\.(blogspot\.[a-z.]+)$", site)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- one page

def _spans(text: str) -> dict[str, list[tuple[int, int]]]:
    spans = {"script": [], "data": [], "comment": []}
    for m in _SCRIPT.finditer(text):
        place = "data" if "ld+json" in m.group(1).lower() else "script"
        spans[place].append((m.start(), m.end()))
    for m in _COMMENT.finditer(text):
        spans["comment"].append((m.start(), m.end()))
    return spans


def _inside(spans: list[tuple[int, int]], pos: int) -> bool:
    return any(a <= pos < b for a, b in spans)


def place_of(text: str, pos: int, spans: dict) -> str:
    """Where in the page code an offset sits (see the module docstring)."""
    for place in ("comment", "data", "script"):      # a script inside a comment is switched off
        if _inside(spans[place], pos):
            return place
    opened, closed = text.rfind("<", 0, pos), text.rfind(">", 0, pos)
    if opened > closed:
        return "meta" if re.match(r"<meta\b", text[opened:opened + 6], re.I) else "link"
    return "text"


def visible_text(text: str) -> str:
    text = _SCRIPT.sub(" ", text)
    text = _STYLE.sub(" ", _COMMENT.sub(" ", text))
    return html.unescape(_TAG.sub(" ", text))


def _normalise(kind: str, value: str) -> str | None:
    value = html.unescape(value).strip()
    if kind == "email":
        value = value.lower().rstrip(".")
        return None if _EMAIL_NOISE.search(value) or re.match(r"u00[0-9a-f]{2}", value) else value
    if kind == "adsense_publisher":
        return value.removeprefix("ca-")
    if kind == "google_analytics_account":
        return value.rsplit("-", 1)[0]               # the account; the snippet keeps the property (UA-…-29)
    if kind == "asset_host":
        return site_of("https://" + value.lower())
    if kind == "declared_profile":
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
        path = parts.path.rstrip("/").lower()
        segments = [s for s in path.split("/") if s]
        if not segments or segments[0] in ("sharer", "sharer.php", "share", "intent", "plugins", "dialog", "tr",
                                           "embed", "watch", "hashtag", "search", "home", "login", "signup",
                                           "privacy", "policies", "legal", "about", "help", "sharearticle",
                                           "p", "reel", "status", "events", "photo.php", "permalink.php"):
            return None
        if host in ("twitter.com", "x.com") and len(segments) > 1:
            return None                              # a single post, not the profile
        return f"{host}/{'/'.join(segments[:2] if segments[0] in ('channel', 'c', 'user', 'company', 'in', 'showcase', 'school') else segments[:1])}"
    if kind in ("title", "site_name", "structured_name", "copyright"):
        value = re.sub(r"\s+", " ", value).strip(" ,-–|")
        return value or None
    return value


def _walk(node, out: list) -> None:
    """Organisation-like objects anywhere in a JSON-LD document (top level, lists, @graph, nested)."""
    if isinstance(node, list):
        for item in node:
            _walk(item, out)
    elif isinstance(node, dict):
        types = node.get("@type")
        types = set(types) if isinstance(types, list) else {types}
        if types & _ORG_TYPES:
            out.append(node)
        for value in node.values():
            if isinstance(value, (list, dict)):
                _walk(value, out)


def _locate(text: str, start: int, end: int, value: str) -> tuple[int, int] | None:
    """Where a JSON string value is written inside a JSON-LD block (as is, or JSON-escaped)."""
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    for form in dict.fromkeys((value, escaped, json.dumps(value)[1:-1], escaped.replace("/", "\\/"),
                               json.dumps(value)[1:-1].replace("/", "\\/"), html.escape(value, quote=False))):
        pos = text.find(form, start, end)
        if pos >= 0:
            return pos, pos + len(form)
    return None


def _structured(text: str, spans: dict) -> list[tuple[str, str, int, int]]:
    """(kind, raw value, start, end) for organisation names and declared profiles in the page's JSON-LD."""
    out = []
    for start, end in spans["data"]:
        body = re.sub(r"^<script\b[^>]*>|</script\s*>$", "", text[start:end], flags=re.I)
        try:
            doc = json.loads(html.unescape(body) if "&quot;" in body else body)
        except ValueError:
            continue
        orgs: list = []
        _walk(doc, orgs)
        for org in orgs:
            name = org.get("name")
            types = org.get("@type")
            if isinstance(name, str) and name.strip() and types != "WebSite":
                where = _locate(text, start, end, name)
                if where:
                    out.append(("structured_name", name, *where))
            same = org.get("sameAs") or []
            for url in [same] if isinstance(same, str) else same:
                if isinstance(url, str) and any(h in url.lower() for h in _PROFILE_HOSTS):
                    where = _locate(text, start, end, url)
                    if where:
                        out.append(("declared_profile", url, *where))
    return out


def _matches(text: str, spans: dict, kind: str, spec: Kind, structured: list):
    if spec.pattern is None:
        return [(raw, a, b) for k, raw, a, b in structured if k == kind]
    return [(m.group("v"), *m.span("v")) for m in spec.pattern.finditer(text)]


def scan_page(text: str) -> list[dict]:
    """Fingerprints in one page's code: kind, value, offsets, snippet, place, shown."""
    spans = _spans(text)
    shown_in = visible_text(text)
    structured = _structured(text, spans)
    found, seen = [], set()
    for kind, spec in KINDS.items():
        count = 0
        for raw, start, end in _matches(text, spans, kind, spec, structured):
            value = _normalise(kind, raw)
            if not value:
                continue
            place = place_of(text, start, spans)
            key = (kind, value, place)
            if key in seen:
                continue
            seen.add(key)
            found.append({"kind": kind, "value": value, "char_start": start, "char_end": end,
                          "snippet": text[start:end], "place": place,
                          "shown": int(raw in shown_in or value in shown_in)})
            count += 1
            if count >= MAX_PER_KIND:
                break
    return found


# --------------------------------------------------------------------------- the archive

def _versions(conn: sqlite3.Connection, again: bool, source_ids: list[str] | None) -> list[sqlite3.Row]:
    where = ["v.mime_type LIKE '%html%'", "s.merged_into IS NULL"]
    params: list = []
    if not again:
        where.append("NOT EXISTS (SELECT 1 FROM fingerprint_scan f WHERE f.version_id = v.id AND f.scanner = ?)")
        params.append(SCANNER)
    if source_ids:
        where.append(f"s.id IN ({','.join('?' for _ in source_ids)})")
        params += source_ids
    return conn.execute(f"""SELECT v.id, v.source_id, v.path, v.acquisition_route, v.captured_at, v.created_at,
                                   s.url FROM source_version v JOIN source s ON s.id = v.source_id
                            WHERE {' AND '.join(where)} ORDER BY v.id""", params).fetchall()


def scan(conn: sqlite3.Connection, *, again: bool = False, source_ids: list[str] | None = None,
         data_dir: Path | None = None, log=None) -> dict:
    """Scan the preserved web pages not yet scanned (all of them with ``again``); returns counts."""
    base = data_dir or paths.data_dir()
    run_id = f"run_{uuid.uuid4().hex}"
    started = time.monotonic()
    counts: dict = {"pages": 0, "missing": 0, "fingerprints": 0, "by_kind": defaultdict(int)}
    versions = _versions(conn, again, source_ids)
    with conn:
        conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                     (run_id, canonical_json({"job": "fingerprints", "scanner": SCANNER, "pages": len(versions),
                                              "again": again}), utc_now()))
    for n, v in enumerate(versions, 1):
        try:
            text = (base / v["path"]).read_bytes().decode("utf-8", "replace")
        except OSError:
            counts["missing"] += 1        # quarantined or moved: nothing to scan
            continue
        found = scan_page(text)
        now = utc_now()
        with conn:
            if again:
                conn.execute("DELETE FROM fingerprint WHERE version_id = ?", (v["id"],))
                conn.execute("DELETE FROM fingerprint_scan WHERE version_id = ? AND scanner = ?", (v["id"], SCANNER))
            site = site_of(v["url"])
            for f in found:
                conn.execute("""INSERT OR IGNORE INTO fingerprint(id, version_id, source_id, site, kind, value,
                                char_start, char_end, snippet, place, shown, run_id, created_at)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (f"fpr_{uuid.uuid4().hex[:20]}", v["id"], v["source_id"], site, f["kind"], f["value"],
                              f["char_start"], f["char_end"], f["snippet"], f["place"], f["shown"], run_id, now))
                counts["by_kind"][f["kind"]] += 1
            conn.execute("INSERT INTO fingerprint_scan(version_id, scanner, run_id, found, scanned_at)"
                         " VALUES (?,?,?,?,?)", (v["id"], SCANNER, run_id, len(found), now))
        counts["pages"] += 1
        counts["fingerprints"] += len(found)
        if log and n % 500 == 0:
            log(f"  {n}/{len(versions)} pages")
    counts["by_kind"] = dict(sorted(counts["by_kind"].items()))
    with conn:
        conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ?, seconds = ?, inputs = ? WHERE id = ?",
                     (utc_now(), round(time.monotonic() - started, 1),
                      canonical_json({"job": "fingerprints", "scanner": SCANNER, "pages": len(versions),
                                      "again": again, "counts": counts}), run_id))
    return {"run_id": run_id, **counts}


# --------------------------------------------------------------------------- clusters

def clusters(conn: sqlite3.Connection) -> list[dict]:
    """Identifiers found on two or more sites, with each site's evidence, strongest kinds first."""
    rows = conn.execute(f"""SELECT f.*, v.captured_at, v.created_at AS version_created, v.acquisition_route, s.url
                            FROM fingerprint f JOIN source_version v ON v.id = f.version_id
                            JOIN source s ON s.id = f.source_id
                            WHERE f.kind IN ({','.join('?' for _ in CLUSTERED)}) AND s.merged_into IS NULL""",
                        CLUSTERED).fetchall()
    by_value: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_value[(r["kind"], r["value"])][r["site"]].append(r)
    names = _site_names(conn)
    servers = _servers(conn)
    out = []
    for (kind, value), sites in by_value.items():
        if len(sites) < 2:
            continue
        spec = KINDS[kind]
        entries = []
        for site, hits in sorted(sites.items()):
            years = sorted({(h["captured_at"] or h["version_created"] or "")[:4] for h in hits} - {""})
            entries.append({
                "site": site, "name": names.get(site, ""), "pages": len({h["source_id"] for h in hits}),
                "years": f"{years[0]}–{years[-1]}" if len(years) > 1 else (years[0] if years else ""),
                "places": sorted({h["place"] for h in hits}),
                "snippets": sorted({h["snippet"] for h in hits})[:3],
                "shown_only": all(h["shown"] for h in hits) and not spec.shown_is_normal,
                "example": hits[0]["url"], "source_id": hits[0]["source_id"],
                "wayback_only": all(h["acquisition_route"] == "wayback" for h in hits),
                "last_capture": max((h["captured_at"] or "" for h in hits if h["acquisition_route"] == "wayback"),
                                    default=""),
            })
        reason = _provider_reason(kind, entries, servers)
        webmail = kind == "email" and value.split("@")[-1] in _WEBMAIL
        out.append({"kind": kind, "label": spec.label, "value": value, "strength": spec.strength,
                    "sites": entries, "platform": reason is not None, "platform_reason": reason,
                    "webmail": webmail, "shared_servers": _shared_servers(entries, servers)})
    rank = {s: n for n, s in enumerate(STRENGTH_ORDER)}
    out.sort(key=lambda c: (c["platform"], rank[c["strength"]], -len(c["sites"]), c["kind"], c["value"]))
    return out


def _servers(conn: sqlite3.Connection) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Each site's outside servers (the ones its pages load files from), and each server's sites."""
    by_site: dict[str, set[str]] = defaultdict(set)
    users: dict[str, set[str]] = defaultdict(set)
    for r in conn.execute("SELECT DISTINCT site, value FROM fingerprint WHERE kind = 'asset_host'"):
        if r["value"] != r["site"]:
            by_site[r["site"]].add(r["value"])
            users[r["value"]].add(r["site"])
    return by_site, users


def _shared_servers(entries: list[dict], servers: tuple) -> list[tuple[str, int, int]]:
    """Outside servers every site of a cluster loads files from: (server, sites on it in the cluster, all its sites)."""
    by_site, users = servers
    carriers = {e["site"] for e in entries}
    shared = set.intersection(*(by_site.get(e["site"], set()) for e in entries))
    return [(h, len(users[h] & carriers), len(users[h])) for h in sorted(shared) if len(users[h]) <= CONTEXT_MAX_SITES]


def _provider_reason(kind: str, entries: list[dict], servers: tuple, carriers: set[str] | None = None) -> str | None:
    """Why an identifier shared by these sites may belong to a platform or web provider, not to them."""
    if len(entries) > MAX_SITES:
        return f"on more than {MAX_SITES} sites"
    if kind == "email":
        return None
    own = [e["site"] for e in entries if is_platform(e["site"])]
    if own:
        return f"seen on the hosting platform's own site ({own[0]})"
    if kind not in PROVIDER_KINDS:
        return None
    hosted = [host_platform(e["site"]) for e in entries if host_platform(e["site"])]
    if hosted:
        return f"seen on a site hosted by {hosted[0]}"
    by_site, users = servers
    carriers = carriers or {e["site"] for e in entries}
    for server in sorted(set.intersection(*(by_site.get(e["site"], set()) for e in entries))):
        served = users[server]
        if len(served) >= PROVIDER_MIN_SITES and len(served & carriers) / len(served) >= PROVIDER_SHARE:
            return (f"{len(served & carriers)} of the {len(served)} sites that load files from {server} carry it: "
                    f"probably that provider's")
    return None


def _site_names(conn: sqlite3.Connection) -> dict[str, str]:
    """The name each site gives itself most often (site name, structured data, else copyright line)."""
    names: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for kind, weight in (("site_name", 3), ("structured_name", 2), ("copyright", 1)):
        for r in conn.execute("SELECT site, value, count(*) AS n FROM fingerprint WHERE kind = ? GROUP BY site, value",
                              (kind,)):
            names[r["site"]][r["value"]] += r["n"] * weight
    return {site: max(counts, key=counts.get) for site, counts in names.items()}


def sites_to_queue(found: list[dict], *, skip: set[str] = frozenset()) -> list[dict]:
    """The home page of every site in a cluster, for the source queue. Left out: hosting platforms' own sites,
    identifiers seen on a platform's own site or on very many sites, and emails (directories list many
    groups). Sites sharing a provider's identifier are kept: the identifier says little, the sites may
    still matter. A site known only from Wayback copies also gets its home page as captured then."""
    sites: dict[str, dict] = {}
    for c in found:
        reason = c.get("platform_reason") or ""
        if c["kind"] == "email" or reason.startswith(("on more than", "seen on the hosting platform's own site")):
            continue
        for e in c["sites"]:
            if e["site"] in skip or is_platform(e["site"]):
                continue
            entry = sites.setdefault(e["site"], {"site": e["site"], "example": e["example"],
                                                 "wayback_only": e["wayback_only"], "why": set(), "capture": ""})
            entry["wayback_only"] = entry["wayback_only"] and e["wayback_only"]
            entry["capture"] = max(entry["capture"], e.get("last_capture") or "")
            entry["why"].add(f"{c['label']} {c['value']}")
    out = []
    for site, e in sorted(sites.items()):
        ref = parse_wayback(e["example"])
        original = ref.original_url if ref else e["example"]
        parts = urlsplit(original)
        home = f"{parts.scheme or 'https'}://{parts.hostname}/"
        urls = [home]
        stamp = (ref.timestamp if ref and ref.timestamp else re.sub(r"\D", "", e["capture"])[:14])
        if e["wayback_only"] and stamp:      # the site may be gone: its home page as captured then
            urls.append(f"https://web.archive.org/web/{stamp}/{home}")
        out.append({"site": site, "urls": urls, "why": sorted(e["why"])})
    return out


# --------------------------------------------------------------------------- report

def write_report(conn: sqlite3.Connection, folder: Path | None = None) -> Path:
    found = clusters(conn)
    folder = folder or paths.reports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"fingerprints-{time.strftime('%Y%m%d-%H%M%S')}.md"
    scanned = conn.execute("SELECT count(*), sum(found) FROM fingerprint_scan WHERE scanner = ?", (SCANNER,)).fetchone()
    by_kind = dict(conn.execute("SELECT kind, count(DISTINCT value) FROM fingerprint GROUP BY kind").fetchall())
    lines = [
        "# Shared fingerprints",
        "",
        f"Generated by `python -m archive fingerprints` ({SCANNER}). Identifiers in the preserved page code",
        "of two or more sites. Each is a finding about page code, not a relation between organisations: it",
        "says the page code of these sites carries the same identifier, when the copy was taken, and where in",
        "the page it sits. Strength is that of the kind of identifier (`docs/FINGERPRINTS.md`). Nothing here",
        "is published.",
        "",
        f"Pages scanned: {scanned[0] or 0}; fingerprints: {scanned[1] or 0}.",
        "",
        "| Kind | Distinct values | Shared by 2+ sites |",
        "|---|---:|---:|",
    ]
    shared = defaultdict(int)
    for c in found:
        if not c["platform"]:
            shared[c["kind"]] += 1
    for kind in KINDS:
        if kind in by_kind:
            lines.append(f"| {KINDS[kind].label} | {by_kind[kind]} | {shared.get(kind, '–') if kind in CLUSTERED else '–'} |")
    places = {"script": "site's own code", "data": "structured data", "meta": "meta tag", "link": "link or form",
              "comment": "switched-off code", "text": "visible text"}

    def site_line(e: dict) -> str:
        name = f" ({e['name']})" if e["name"] else ""
        flag = " — **shown as text: this page may be quoting it**" if e["shown_only"] else ""
        return (f"  - {e['site']}{name}: {e['pages']} page(s), {e['years'] or 'undated'}, "
                f"{', '.join(places[p] for p in e['places'])}; `{'`, `'.join(s[:60] for s in e['snippets'])}`"
                f"{flag}; e.g. {e['example']}")

    for strength in STRENGTH_ORDER:
        group = [c for c in found if c["strength"] == strength and not c["platform"]]
        title = {"strong": "Strong: one legal or financial owner",
                 "medium": "Medium: one web manager (possibly an agency)",
                 "weak": "Weak: contact emails (personal data: for matching, never published)"}[strength]
        lines += ["", f"## {title} ({len(group)})", ""]
        if not group:
            lines.append("None.")
        for c in group:
            if strength == "weak":
                tag = " (webmail: probably a person)" if c["webmail"] else ""
                lines.append(f"- `{c['value']}`{tag}: {', '.join(e['site'] for e in c['sites'])}")
                continue
            lines.append(f"- **{c['label']} `{c['value']}`** on {len(c['sites'])} sites")
            lines += [site_line(e) for e in c["sites"]]
            if c["shared_servers"]:
                lines.append("  - all of them load files from: " + "; ".join(
                    f"{h} ({k} of the {n} sites in the archive that use it carry this)" for h, k, n in c["shared_servers"][:4]))
    platform = [c for c in found if c["platform"]]
    lines += ["", f"## Platforms and providers: probably not the organisations' own ({len(platform)})", "",
              "An identifier a hosting platform or web provider puts on every site it serves says who serves",
              "the sites, not who runs them.", ""]
    lines += [f"- {c['label']} `{c['value']}`: {len(c['sites'])} sites "
              f"({', '.join(e['site'] for e in c['sites'][:8])}{', …' if len(c['sites']) > 8 else ''}) — "
              f"{c['platform_reason']}" for c in platform]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def queue_sites(conn: sqlite3.Connection, *, skip: set[str] = frozenset(), origin: str | None = None) -> dict:
    """Add the home pages of the sites in clusters to the source queue (existing sources are left as they are)."""
    from .queue_import import add_urls
    wanted = sites_to_queue(clusters(conn), skip=skip)
    urls = [u for w in wanted for u in w["urls"]]
    report = add_urls(conn, urls, origin=origin or f"fingerprints-{time.strftime('%Y-%m-%d')}")
    report["sites"] = {w["site"]: {"urls": w["urls"], "why": w["why"]} for w in wanted}
    return report


def to_json(found: list[dict]) -> str:
    return json.dumps(found, ensure_ascii=False, indent=1)
