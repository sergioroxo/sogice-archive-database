"""Polite, bounded HTTP fetching of public sources (network side only).

Rules
* Only public http(s) URLs on ports 80/443, without credentials or secret
  query parameters. Every connection resolves the host once and refuses
  private, loopback and link-local addresses (no DNS-rebinding route into the
  local network). Ported from the old ``project_acquisition.py``.
* Honest User-Agent; no cookies, no browser imitation.
* One request at a time per host, at least ``delay`` seconds apart.
* robots.txt is obeyed for live pages (RFC 9309: a 4xx robots file allows
  all, a 5xx or unreachable one disallows all). Wayback captures and the CDX
  lookup are requests to the Internet Archive, not to the source site.
* Size and time caps on every response; media types (video, audio, image)
  are refused from their headers without downloading the body.
* Challenge pages (Cloudflare, "verify you are human"...) count as blocked,
  using the old ``acquire.classify_challenge`` rules.

``Fetcher.get`` never raises for network or HTTP problems: it returns a
``FetchResult`` whose ``outcome`` names what happened and whose
``transient`` flag says whether one retry makes sense.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import ssl
import threading
import time
import urllib.robotparser
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import certifi
import httpcore
import httpx

from .urls import UrlError, check_http_url, parse_wayback

USER_AGENT = ("SOGICE-Archive/0.1 (research archive; "
              "+https://github.com/sergioroxo/sogice-archive-database)")
ROBOTS_AGENT = "SOGICE-Archive"
ACCEPT = ("text/html,application/xhtml+xml,application/pdf,text/plain,"
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
          "application/msword;q=0.9,*/*;q=0.5")
MAX_BYTES = 64 * 1024 * 1024
REQUEST_TIMEOUT = 30.0
TOTAL_SECONDS = 180.0
MAX_REDIRECTS = 8
DEFAULT_DELAY = 3.0
HOST_DELAYS = {"web.archive.org": 4.0, "archive.org": 4.0, "youtube.com": 8.0}
CDX_URL = "https://web.archive.org/cdx/search/cdx"

SECRET_PARAMS = {"token", "access_token", "api_key", "apikey", "key", "password", "secret",
                 "signature", "sig", "authorization", "auth"}
REFUSED_TYPE_PREFIXES = ("video/", "audio/", "image/", "font/")
CHALLENGE_STATUSES = {403, 429, 503}
CHALLENGE_TEXT = re.compile(
    r"just a moment\.\.\.|checking your browser|verify you are human|"
    r"enable javascript and cookies to continue|cf-browser-verification|cf_chl_opt|"
    r"cdn-cgi/challenge-platform|please complete the security check|"
    r"attention required.{0,40}cloudflare|ddos protection by|"
    r"preparing to download \.\.\.|pow_challenge|proof.of.work|"
    r"verifying your browser|__mw_turnstile|checking if the site connection is secure|"
    r"needs to review the security of your connection",
    re.IGNORECASE)
CHALLENGE_MAX_BODY = 60_000   # real challenge pages are small; long pages are content
CHALLENGE_LARGE_BODY = 2_000_000
CHALLENGE_VISIBLE_CHARS = 2_000
_INVISIBLE_TEXT = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>|<!--.*?-->|<[^>]+>",
                             re.IGNORECASE | re.DOTALL)
KEEP_HEADERS = ("content-type", "content-length", "content-encoding", "last-modified", "etag",
                "server", "cf-ray", "cf-mitigated", "retry-after", "location",
                "x-archive-src", "memento-datetime", "link")


class NotPublic(ValueError):
    """The URL or its address is not a public internet source."""


# ---------------------------------------------------------------------------
# Public-network guard
# ---------------------------------------------------------------------------

def check_public_url(url: str) -> str:
    """Cheap URL-level checks (no DNS). Raises NotPublic."""
    try:
        url = check_http_url(url)
    except UrlError as exc:
        raise NotPublic(str(exc)) from None
    parts = urlsplit(url)
    if parts.port not in (None, 80, 443):
        raise NotPublic(f"custom port {parts.port} is not fetched")
    host = (parts.hostname or "").rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
        raise NotPublic(f"local host name {host!r}")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None  # a name: its addresses are checked at connect time
    if literal is not None and not literal.is_global:
        raise NotPublic(f"non-public address {host}")
    for key, _ in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in SECRET_PARAMS or lowered.startswith("x-amz-"):
            raise NotPublic(f"URL carries a secret-looking parameter {key!r}")
    return url


class PublicNetworkBackend(httpcore.SyncBackend):
    """Resolve once, refuse non-public addresses, connect to the checked IP."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise httpcore.ConnectError(f"cannot resolve {host}: {exc}") from None
        addresses = list(dict.fromkeys(row[4][0] for row in rows))
        if not addresses or any(not ipaddress.ip_address(a.split("%")[0]).is_global
                                for a in addresses):
            raise NotPublic(f"{host} resolves to a non-public address")
        deadline = time.monotonic() + (timeout or REQUEST_TIMEOUT)
        last_error: Exception | None = None
        for address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                return super().connect_tcp(address, port, remaining, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise httpcore.ConnectTimeout(f"connecting to {host} timed out")


_HTTPCORE_TO_HTTPX = (
    (httpcore.ConnectTimeout, httpx.ConnectTimeout), (httpcore.ReadTimeout, httpx.ReadTimeout),
    (httpcore.WriteTimeout, httpx.WriteTimeout), (httpcore.PoolTimeout, httpx.PoolTimeout),
    (httpcore.ConnectError, httpx.ConnectError), (httpcore.ReadError, httpx.ReadError),
    (httpcore.WriteError, httpx.WriteError), (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
    (httpcore.LocalProtocolError, httpx.LocalProtocolError), (httpcore.ProxyError, httpx.ProxyError),
    (httpcore.UnsupportedProtocol, httpx.UnsupportedProtocol),
)


@contextmanager
def _httpx_errors():
    """Raise httpcore's errors as httpx's, as httpx's own transport does.

    Without this a read timeout (httpcore.ReadTimeout) is not an
    httpx.TimeoutException, escapes Fetcher._one, and the target is skipped
    unrecorded (43 targets in the 9 Nov full run).
    """
    try:
        yield
    except tuple(core for core, _ in _HTTPCORE_TO_HTTPX) as exc:
        mapped = next(ours for core, ours in _HTTPCORE_TO_HTTPX if isinstance(exc, core))
        raise mapped(str(exc) or type(exc).__name__) from exc


class PublicTransport(httpx.BaseTransport):
    """httpx transport whose every connection goes through PublicNetworkBackend."""

    def __init__(self, max_connections: int = 10):
        context = ssl.create_default_context(cafile=certifi.where())
        self.pool = httpcore.ConnectionPool(ssl_context=context, max_connections=max_connections,
                                            network_backend=PublicNetworkBackend())

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        with _httpx_errors():
            response = self.pool.handle_request(httpcore.Request(
                method=request.method,
                url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host,
                                 port=request.url.port, target=request.url.raw_path),
                headers=request.headers.raw, content=request.stream, extensions=request.extensions))

        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                with _httpx_errors():
                    yield from response.iter_stream()

            def close(self):
                with _httpx_errors():
                    response.close()

        return httpx.Response(response.status, headers=response.headers, stream=Stream(),
                              extensions=response.extensions)

    def close(self) -> None:
        self.pool.close()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    url: str
    outcome: str                   # stored-able 'ok' or a fetch_attempt outcome
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    body: bytes | None = field(default=None, repr=False)
    headers: dict = field(default_factory=dict)
    redirects: list = field(default_factory=list)
    error: str | None = None
    challenge: str | None = None
    transient: bool = False
    retry_after: float | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"

    def detail(self) -> dict:
        out: dict = {"headers": self.headers}
        if self.redirects:
            out["redirects"] = self.redirects
        if self.error:
            out["error"] = self.error[:500]
        if self.challenge:
            out["challenge"] = self.challenge
        return out


@dataclass(frozen=True)
class Capture:
    timestamp: str
    original: str
    mimetype: str
    status: str

    @property
    def raw_url(self) -> str:
        return f"https://web.archive.org/web/{self.timestamp}id_/{self.original}"


def classify_challenge(status: int | None, headers: dict, body: bytes | None) -> str | None:
    if "challenge" in str(headers.get("cf-mitigated", "")).lower():
        return "cf-mitigated:challenge"
    is_cloudflare = "cloudflare" in str(headers.get("server", "")).lower() or "cf-ray" in headers
    if status in CHALLENGE_STATUSES and is_cloudflare:
        return f"cloudflare_http:{status}"
    if body and len(body) <= CHALLENGE_MAX_BODY:
        text = body.decode("utf-8", "replace")   # the message can sit after long inline scripts
        if CHALLENGE_TEXT.search(text):
            return "blocker_text"
    elif body and len(body) <= CHALLENGE_LARGE_BODY:
        # Big pages can still be challenges padded with inline scripts: judge the visible text.
        visible = re.sub(r"\s+", " ", _INVISIBLE_TEXT.sub(" ", body.decode("utf-8", "replace"))).strip()
        if len(visible) < CHALLENGE_VISIBLE_CHARS and CHALLENGE_TEXT.search(visible):
            return "blocker_text"
    return None


_STUB_SCAN_BYTES = 30_000
_INVISIBLE = re.compile(rb"<(script|style|noscript|template)\b.*?</\1\s*>|<!--.*?-->|<[^>]+>",
                        re.IGNORECASE | re.DOTALL)
PARKED_TEXT = re.compile(
    rb"this domain (name )?(is|may be) for sale|buy this domain|domain (is )?parked|"
    rb"parked free|sedoparking|hugedomains|dan\.com/|afternic|parkingcrew|bodis\.com|"
    rb"window\.location(\.href)?\s*=\s*[\"']/lander",
    re.IGNORECASE)


def classify_stub(body: bytes, content_type: str) -> str | None:
    """'parked' for domain-parking pages, 'empty' for HTML with no visible text.

    Only small pages are checked: real articles are long. A JavaScript-only
    shell is 'empty' for our purposes (there is no text to cite), so the
    caller can fall back to a Wayback capture.
    """
    ctype = (content_type or "").lower()
    head = body[:2048].lstrip().lower()
    is_html = "html" in ctype or head.startswith((b"<!doctype html", b"<html"))
    if not is_html or len(body) > _STUB_SCAN_BYTES * 4:
        return None
    if PARKED_TEXT.search(body[:_STUB_SCAN_BYTES * 4]):
        return "parked"
    if len(body) <= _STUB_SCAN_BYTES:
        visible = re.sub(rb"(&nbsp;|\s)+", b" ", _INVISIBLE.sub(b" ", body)).strip()
        if len(visible) < 40:
            return "empty"
    return None


def _retry_after(value: str | None) -> float | None:
    if value and value.strip().isdigit():
        return float(value.strip())
    return None


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class Fetcher:
    def __init__(self, *, transport: httpx.BaseTransport | None = None,
                 delay: float = DEFAULT_DELAY, host_delays: dict | None = None,
                 max_bytes: int = MAX_BYTES, total_seconds: float = TOTAL_SECONDS,
                 respect_robots: bool = True, sleep=time.sleep, clock=time.monotonic):
        self.client = httpx.Client(
            transport=transport or PublicTransport(), follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
            headers={"User-Agent": USER_AGENT, "Accept": ACCEPT})
        self.delay = delay
        self.host_delays = dict(HOST_DELAYS if host_delays is None else host_delays)
        self.max_bytes = max_bytes
        self.total_seconds = total_seconds
        self.respect_robots = respect_robots
        self.sleep, self.clock = sleep, clock
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._robots: dict[str, urllib.robotparser.RobotFileParser | bool] = {}

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- pacing ------------------------------------------------------------

    def _host_lock(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def _pace(self, host: str) -> None:
        """Wait until this host may receive another request (caller holds the host lock)."""
        gap = self.host_delays.get(host, self.delay)
        last = self._last.get(host)
        if last is not None:
            wait = last + gap - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    @contextmanager
    def paced(self, host: str):
        """Hold a host's turn for work done outside this client (e.g. yt-dlp)."""
        with self._host_lock(host):
            self._pace(host)
            yield

    # -- one request -------------------------------------------------------

    def _one(self, url: str, deadline: float) -> tuple[httpx.Response | None, bytes | None, FetchResult | None]:
        """Send one GET; return (response, body, None) or (None, None, failure)."""
        host = (urlsplit(url).hostname or "").lower()
        with self._host_lock(host):
            self._pace(host)
            try:
                with self.client.stream("GET", url) as response:
                    ctype = response.headers.get("content-type", "")
                    if response.status_code != 200:
                        body = bytearray()  # error pages: keep the start, for challenge checks
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > CHALLENGE_MAX_BODY:
                                break
                        return response, bytes(body), None
                    if ctype.lower().startswith(REFUSED_TYPE_PREFIXES):
                        return response, None, None
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > self.max_bytes:
                        return None, None, FetchResult(url, "too_large", http_status=200,
                                                       error=f"declared {declared} bytes")
                    chunks, total = [], 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.max_bytes:
                            return None, None, FetchResult(url, "too_large", http_status=200,
                                                           error=f"over {self.max_bytes} bytes")
                        if self.clock() > deadline:
                            return None, None, FetchResult(url, "network_error", http_status=200,
                                                           error="download took too long",
                                                           transient=True)
                        chunks.append(chunk)
                    return response, b"".join(chunks), None
            except NotPublic as exc:
                return None, None, FetchResult(url, "network_error", error=f"refused: {exc}")
            except httpx.TimeoutException as exc:
                return None, None, FetchResult(url, "network_error", error=f"timeout: {exc}",
                                               transient=True)
            except (httpx.HTTPError, httpcore.ConnectError, OSError, ssl.SSLError) as exc:
                return None, None, FetchResult(url, "network_error",
                                               error=f"{type(exc).__name__}: {exc}", transient=True)

    def get(self, url: str, *, route: str = "live_fetch") -> FetchResult:
        """Fetch a page or file, following redirects (each one re-checked).

        ``route='wayback'`` keeps every hop on web.archive.org and skips
        robots.txt; ``route='live_fetch'`` checks robots.txt for each host.
        """
        started = self.clock()
        deadline = started + self.total_seconds
        redirects: list[dict] = []
        current = url

        def done(result: FetchResult) -> FetchResult:
            result.url, result.redirects = url, redirects
            result.final_url = result.final_url or current
            result.seconds = round(self.clock() - started, 3)
            return result

        for _ in range(MAX_REDIRECTS + 1):
            try:
                check_public_url(current)
            except NotPublic as exc:
                return done(FetchResult(url, "network_error", error=f"refused: {exc}"))
            if route == "wayback" and (urlsplit(current).hostname or "").lower() != "web.archive.org":
                return done(FetchResult(url, "http_error", error=f"Wayback redirected off the archive to {current}"))
            if route == "live_fetch" and self.respect_robots and not self.robots_allowed(current):
                return done(FetchResult(url, "robots_disallowed", error="robots.txt disallows this URL"))

            response, body, failure = self._one(current, deadline)
            if failure is not None:
                return done(failure)
            status = response.status_code
            headers = {k: response.headers[k] for k in KEEP_HEADERS if k in response.headers}
            ctype = response.headers.get("content-type", "")
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    return done(FetchResult(url, "http_error", http_status=status, headers=headers,
                                            error="redirect without Location"))
                target = urljoin(current, location)
                redirects.append({"from": current, "to": target, "status": status})
                current = target
                continue

            result = FetchResult(url, "ok", http_status=status, content_type=ctype or None,
                                 headers=headers)
            challenge = classify_challenge(status, response.headers, body)
            if challenge:
                result.outcome, result.challenge = "blocked", challenge
                result.error = "access check / challenge page"
            elif status == 404:
                result.outcome = "not_found"
            elif status == 410:
                result.outcome = "gone"
            elif status in (401, 403, 451):
                result.outcome = "blocked"
            elif status == 429:
                result.outcome, result.transient = "blocked", True
                result.retry_after = _retry_after(response.headers.get("retry-after"))
            elif status >= 500:
                result.outcome, result.transient = "http_error", True
                result.retry_after = _retry_after(response.headers.get("retry-after"))
            elif status != 200:
                result.outcome = "http_error"
            elif ctype.lower().startswith(REFUSED_TYPE_PREFIXES):
                result.outcome = "unsupported_type"
                result.error = f"media type {ctype.split(';')[0]} is not fetched here"
            elif not body or not body.strip():
                result.outcome = "empty"
            elif (stub := classify_stub(body, ctype)) is not None:
                result.outcome = "gone" if stub == "parked" else "empty"
                result.error = ("domain-parking page" if stub == "parked"
                                else "HTML with no visible text (script or redirect stub)")
            else:
                result.body = body
            if result.outcome != "ok" and status not in (200,):
                result.error = result.error or f"HTTP {status}"
            return done(result)
        return done(FetchResult(url, "http_error", error=f"more than {MAX_REDIRECTS} redirects"))

    # -- robots.txt --------------------------------------------------------

    def robots_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}".lower()
        rules = self._robots.get(key)
        if rules is None:
            rules, cacheable = self._load_robots(key)
            if cacheable:
                self._robots[key] = rules
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(ROBOTS_AGENT, url)

    def _load_robots(self, base: str) -> tuple[urllib.robotparser.RobotFileParser | bool, bool]:
        """(rules or allow-all/disallow-all, cacheable). RFC 9309 status rules."""
        deadline = self.clock() + 60
        current = base + "/robots.txt"
        for _ in range(5):
            response, body, failure = self._one(current, deadline)
            if failure is not None:
                # Unreachable -> disallow; do not cache a transient failure.
                return False, not failure.transient
            status = response.status_code
            if status in (301, 302, 303, 307, 308) and response.headers.get("location"):
                current = urljoin(current, response.headers["location"])
                try:
                    check_public_url(current)
                except NotPublic:
                    return True, True
                continue
            if 400 <= status < 500:
                return True, True      # unavailable -> allow all
            if status >= 500:
                return False, False    # unreachable -> disallow all (for now)
            parser = urllib.robotparser.RobotFileParser()
            parser.parse((body or b"").decode("utf-8", "replace").splitlines())
            return parser, True
        return True, True

    # -- Wayback -----------------------------------------------------------

    def wayback_captures(self, url: str, *, limit: int = 3) -> tuple[list[Capture], FetchResult]:
        """Most recent successful captures of ``url`` (newest first) from the CDX API.

        The CDX index matches URLs regardless of scheme and ``www.``.
        """
        query = urlencode({"url": url.split("#", 1)[0], "output": "json",
                           "fl": "timestamp,original,mimetype,statuscode",
                           "filter": "statuscode:200", "collapse": "digest",
                           "limit": str(-abs(limit))})
        result = self.get(f"{CDX_URL}?{query}", route="wayback")
        if not result.ok:
            return [], result
        try:
            rows = json.loads(result.body or b"[]")
        except ValueError:
            result.outcome, result.error = "http_error", "CDX reply is not JSON"
            return [], result
        captures = [Capture(*row[:4]) for row in rows[1:] if len(row) >= 4]
        captures.sort(key=lambda c: c.timestamp, reverse=True)
        return captures, result


def capture_time(final_url: str | None) -> tuple[str | None, str | None]:
    """(ISO capture time, certainty) from a Wayback URL, else (None, None)."""
    try:
        ref = parse_wayback(final_url or "")
    except UrlError:
        return None, None
    if not ref or not ref.captured_date:
        return None, None
    return ref.captured_date, ref.captured_certainty
