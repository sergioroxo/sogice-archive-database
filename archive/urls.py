"""URL helpers: validation, identity keys and Wayback URLs (standard library).

Identity key
    Two URLs that differ only in scheme (http/https), a leading ``www.``,
    letter case of the host, a default port, the fragment, a trailing slash
    or tracking parameters (utm_*, fbclid, gclid...) name the same source.
    ``identity_key`` reduces a URL to that form. It is used to give each
    source a stable id and to attach exact repeats; it is never fetched.
    Path and query keep their case and order (servers may care).

Wayback
    ``parse_wayback`` splits ``https://web.archive.org/web/<timestamp><mod>/<url>``
    into the original URL and the capture timestamp. ``wayback_raw_url``
    builds the ``id_`` form that returns the archived bytes without the
    Wayback toolbar.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "yclid"}
_WAYBACK = re.compile(
    r"^https?://(?:www\.)?web\.archive\.org/web/(?P<stamp>[^/]*)/(?P<rest>.*)$", re.IGNORECASE)
_STAMP = re.compile(r"^(?P<digits>\d{1,14})(?P<mod>[a-z]{2}_|\*)?$")


class UrlError(ValueError):
    """Not a usable http(s) URL."""


def check_http_url(url: str) -> str:
    """Return the stripped URL if it is http(s) with a host and no credentials."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise UrlError(f"not an http(s) URL: {url[:200]!r}")
    if parts.username or parts.password:
        raise UrlError("URL must not contain credentials")
    try:
        parts.port  # raises on a malformed port
    except ValueError as exc:
        raise UrlError(f"bad port in URL: {exc}") from None
    return url


def host_of(url: str) -> str:
    """Lower-case host without a leading 'www.' (for per-domain politeness and reports)."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _is_tracking(name: str) -> bool:
    name = name.lower()
    return name.startswith("utm_") or name in TRACKING_PARAMS


def identity_key(url: str) -> str:
    """Scheme-free, normalised form of a URL for deciding 'same source'."""
    url = check_http_url(url)
    parts = urlsplit(url)
    host = host_of(url)
    port = parts.port
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not _is_tracking(k)]
    query = urlencode(query_pairs, doseq=True) if query_pairs else ""
    return urlunsplit(("", host, path, query, "")).lstrip("/")


def short_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def source_id_for(url: str) -> str:
    """Stable source id from the URL's identity key."""
    return "src_" + short_hash(identity_key(url))


@dataclass(frozen=True)
class WaybackRef:
    original_url: str        # the archived page's own URL
    timestamp: str | None    # digits as given ('20081218053759', '2'), or None
    modifier: str | None     # 'id_', 'if_', '*', ... as given
    kind: str                # 'capture' or 'sitemap'

    @property
    def captured_date(self) -> str | None:
        """ISO date/time from a full or partial timestamp ('2008-12-18T05:37:59Z', '2008-12')."""
        stamp = self.timestamp or ""
        if len(stamp) < 4:
            return None
        date = stamp[0:4]
        if len(stamp) >= 6:
            date += "-" + stamp[4:6]
        if len(stamp) >= 8:
            date += "-" + stamp[6:8]
        if len(stamp) >= 14:
            return f"{date}T{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}Z"
        return date

    @property
    def captured_certainty(self) -> str:
        # Wayback serves the nearest capture to a partial timestamp.
        return "exact" if self.timestamp and len(self.timestamp) == 14 else "approximate"


def parse_wayback(url: str) -> WaybackRef | None:
    """Split a Wayback URL; None if the URL is not on web.archive.org/web/."""
    match = _WAYBACK.match((url or "").strip())
    if not match:
        return None
    stamp, rest = match.group("stamp"), match.group("rest")
    if not re.match(r"^https?://", rest, re.IGNORECASE):
        # Wayback accepts 'http:/x' (one slash) and bare hosts.
        rest = re.sub(r"^(https?):/+", r"\1://", rest, flags=re.IGNORECASE)
        if not re.match(r"^https?://", rest, re.IGNORECASE):
            rest = "http://" + rest.lstrip("/")
    original = check_http_url(rest)
    if stamp.lower() == "sitemap":
        return WaybackRef(original, None, None, "sitemap")
    found = _STAMP.match(stamp)
    if not found:
        raise UrlError(f"unrecognised Wayback timestamp {stamp!r}")
    return WaybackRef(original, found.group("digits"), found.group("mod"), "capture")


def wayback_raw_url(ref: WaybackRef) -> str:
    """The id_ form: original archived bytes, no toolbar or rewritten links."""
    if ref.kind != "capture" or not ref.timestamp:
        raise UrlError("only captures with a timestamp have a raw form")
    return f"https://web.archive.org/web/{ref.timestamp}id_/{ref.original_url}"
