"""YouTube targets: metadata, description and (optionally) captions via yt-dlp.

No video or audio is downloaded here.

* ``yt-dlp -J`` reads the watch page: title, channel, upload date, duration,
  description, language and the list of caption tracks. The trimmed metadata
  is stored as a JSON object; the description becomes a text version
  (flag ``video_description``); empty source fields are filled (upload date
  with certainty 'exact').
* Captions: manual tracks in the video's own language, else the automatic
  track for the spoken language (``<lang>-orig``); never machine
  translations. Stored as a .vtt version (flags ``captions_manual`` or
  ``captions_auto``); the indexer keeps their timecodes.

YouTube's robots.txt allows /watch but disallows /api/, where caption files
live. Captions are therefore fetched only when ``captions=True`` (decision
for the researcher); otherwise a ``robots_disallowed`` attempt records that
they were skipped. Channels and playlists are not expanded.

One video at a time on youtube.com (``Fetcher.paced``); a transient failure
is retried once.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import tempfile
import uuid
from pathlib import Path

from .acquire import TargetResult
from .db import canonical_json, utc_now
from .depot import Depot
from .fetch import FetchResult, Fetcher
from .urls import short_hash

YTDLP = "yt-dlp"
TIMEOUT = 180
PACE_HOST = "youtube.com"
KEEP_INFO = ("id", "title", "fulltitle", "uploader", "uploader_id", "uploader_url", "channel",
             "channel_id", "channel_url", "upload_date", "release_date", "timestamp", "duration",
             "description", "language", "tags", "categories", "webpage_url", "availability",
             "live_status", "was_live", "view_count", "like_count", "comment_count", "chapters",
             "_type", "extractor")
GONE = re.compile(r"video unavailable|has been removed|private video|account .* terminated|"
                  r"no longer available|this video is not available|does not exist", re.I)
BLOCKED = re.compile(r"sign in to confirm|not a bot|members-only|age-restricted|"
                     r"confirm your age|http error 403", re.I)
THROTTLED = re.compile(r"http error 429|too many requests|rate.?limit", re.I)


def base_language(code: str | None) -> str | None:
    return code.split("-")[0].lower() if code else None


def choose_track(info: dict) -> tuple[str, str] | None:
    """(track key, 'manual' | 'auto') to fetch, or None. Never a translation."""
    lang = base_language(info.get("language"))
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}
    if lang:
        same = sorted(k for k in manual if base_language(k) == lang)
        if same:
            return same[0], "manual"
    origs = sorted(k for k in auto if k.endswith("-orig"))
    if lang:
        matching = [k for k in origs if base_language(k[:-5]) == lang]
        if matching:
            return matching[0], "auto"
    elif len(origs) == 1:
        return origs[0], "auto"
    if manual and not lang and len(manual) == 1:
        return next(iter(manual)), "manual"
    return None


def upload_date_iso(value: str | None) -> str | None:
    if value and re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return None


class VideoAcquirer:
    def __init__(self, conn: sqlite3.Connection, fetcher: Fetcher, depot: Depot, *,
                 run_id: str | None = None, captions: bool = False, runner=subprocess.run):
        self.conn, self.fetcher, self.depot = conn, fetcher, depot
        self.run_id, self.captions, self.runner = run_id, captions, runner

    # -- yt-dlp -----------------------------------------------------------------

    def _ytdlp(self, args: list[str]) -> tuple[int, str, str]:
        try:
            done = self.runner([YTDLP, "--ignore-config", "--no-warnings", *args],
                               capture_output=True, timeout=TIMEOUT, check=False)
        except FileNotFoundError:
            return 127, "", "yt-dlp is not installed"
        except subprocess.TimeoutExpired:
            return 124, "", f"yt-dlp took longer than {TIMEOUT} s"
        return (done.returncode, done.stdout.decode("utf-8", "replace"),
                done.stderr.decode("utf-8", "replace"))

    @staticmethod
    def _failure(url: str, code: int, stderr: str) -> FetchResult:
        message = (stderr.strip().splitlines() or [f"exit {code}"])[-1][:400]
        if GONE.search(stderr):
            return FetchResult(url, "gone", error=message)
        if THROTTLED.search(stderr):
            return FetchResult(url, "blocked", error=message, transient=True, retry_after=60)
        if BLOCKED.search(stderr):
            return FetchResult(url, "blocked", error=message)
        return FetchResult(url, "network_error", error=message, transient=True)

    def _info(self, url: str) -> tuple[dict | None, FetchResult]:
        result = None
        for attempt in (1, 2):
            if attempt == 2:
                self.fetcher.sleep(min(result.retry_after or 15, 60))
            with self.fetcher.paced(PACE_HOST):
                code, out, err = self._ytdlp(["-J", "--skip-download", "--no-playlist",
                                              "--flat-playlist", "--playlist-end", "1", url])
            if code == 0:
                try:
                    return json.loads(out), FetchResult(url, "ok", http_status=200)
                except ValueError:
                    result = FetchResult(url, "network_error", error="yt-dlp output is not JSON")
            else:
                result = self._failure(url, code, err)
            self._record(result.url, attempt, result)
            if not result.transient:
                break
        return None, result

    # -- recording -------------------------------------------------------------------

    def _record(self, url: str, attempt: int, result: FetchResult, version_id: str | None = None,
                extra: dict | None = None) -> None:
        detail = result.detail()
        detail.update(extra or {})
        outcome = "stored" if version_id else result.outcome
        with self.conn:
            self.conn.execute(
                "INSERT INTO fetch_attempt(id, target_id, run_id, attempt, route, requested_url,"
                " final_url, http_status, content_type, outcome, detail, version_id, seconds,"
                " created_at) VALUES (?,?,?,?, 'live_fetch', ?,?,?,?,?,?,?,?,?)",
                (f"fat_{uuid.uuid4().hex}", self._target_id, self.run_id, attempt, url,
                 result.final_url or url, result.http_status, result.content_type, outcome,
                 canonical_json(detail), version_id, result.seconds, utc_now()))

    def _version(self, source_id: str, data: bytes, extension: str, mime: str,
                 flags: list[str]) -> tuple[str, bool]:
        stored = self.depot.put(data, extension)
        version_id = "ver_" + short_hash(f"{source_id}|{stored.sha256}")
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO source_version(id, source_id, sha256, path, byte_size,"
                " mime_type, acquisition_route, quality_flags, captured_at, created_at)"
                " VALUES (?,?,?,?,?,?, 'live_fetch', ?,?,?)",
                (version_id, source_id, stored.sha256, stored.relative_path, stored.size, mime,
                 canonical_json(sorted(flags)), utc_now(), utc_now()))
        return version_id, stored.created

    def _fill_source(self, source_id: str, info: dict) -> None:
        source = self.conn.execute("SELECT * FROM source WHERE id = ?", (source_id,)).fetchone()
        updates = {}
        if not source["title"] and info.get("title"):
            updates["title"] = str(info["title"])[:500]
        publisher = info.get("channel") or info.get("uploader")
        if not source["publisher"] and publisher:
            updates["publisher"] = str(publisher)[:300]
        date = upload_date_iso(info.get("upload_date"))
        if not source["published_date"] and date:
            updates["published_date"], updates["published_date_certainty"] = date, "exact"
        if updates:
            with self.conn:
                self.conn.execute(
                    f"UPDATE source SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                    (*updates.values(), source_id))

    # -- main ---------------------------------------------------------------------

    def acquire(self, target: sqlite3.Row) -> TargetResult:
        self._target_id = target["id"]
        url, source_id = target["url"], target["source_id"]
        outcomes: list = []
        if not self.fetcher.robots_allowed(url):
            self._record(url, 1, FetchResult(url, "robots_disallowed", error="robots.txt disallows this URL"))
            return TargetResult(target["id"], "failed", outcomes=["robots_disallowed"])

        info, result = self._info(url)
        if info is None:
            return TargetResult(target["id"], "failed", outcomes=[result.outcome])
        if info.get("_type") in ("playlist", "multi_video"):
            self._record(url, 1, FetchResult(url, "unsupported_type",
                                             error="channel or playlist: list its videos as separate sources"))
            return TargetResult(target["id"], "failed", outcomes=["unsupported_type"])

        trimmed = {k: info[k] for k in KEEP_INFO if k in info}
        trimmed["caption_tracks"] = {"manual": sorted(info.get("subtitles") or {}),
                                     "auto_orig": sorted(k for k in (info.get("automatic_captions") or {})
                                                         if k.endswith("-orig"))}
        info_object = self.depot.put(canonical_json(trimmed).encode("utf-8"), "json")
        self._fill_source(source_id, info)
        extra = {"info_path": info_object.relative_path, "duration": info.get("duration")}

        stored_any, new_file, first_version = False, False, None
        description = (info.get("description") or "").strip()
        if description:
            version_id, created = self._version(source_id, description.encode("utf-8"), "txt",
                                                "text/plain", ["video_description"])
            self._record(url, 1, FetchResult(url, "ok", http_status=200, content_type="text/plain"),
                         version_id, {**extra, "part": "description"})
            outcomes.append("stored")
            stored_any, new_file, first_version = True, created, version_id

        track = choose_track(info)
        if track is None:
            self._record(url, 1, FetchResult(url, "empty", error="no caption track in the video's language"),
                         None, {**extra, "part": "captions", "language": info.get("language")})
            outcomes.append("no_captions")
        elif not self.captions:
            self._record(url, 1, FetchResult(url, "robots_disallowed",
                                             error="captions are served from /api/, disallowed by "
                                                   "YouTube's robots.txt; not fetched"),
                         None, {**extra, "part": "captions", "track": track[0]})
            outcomes.append("captions_skipped")
        else:
            version_id = self._fetch_captions(url, info, track, source_id, extra, outcomes)
            if version_id:
                stored_any, first_version = True, first_version or version_id

        return TargetResult(target["id"], "stored" if stored_any else "failed", first_version,
                            "live_fetch" if stored_any else None, new_file, outcomes)

    def _fetch_captions(self, url, info, track, source_id, extra, outcomes) -> str | None:
        key, kind = track
        with tempfile.TemporaryDirectory(prefix="sogice-yt-") as tmp:
            info_path = Path(tmp) / "info.json"
            info_path.write_text(json.dumps(info), encoding="utf-8")
            flag = "--write-subs" if kind == "manual" else "--write-auto-subs"
            with self.fetcher.paced(PACE_HOST):
                code, _, err = self._ytdlp(["--load-info-json", str(info_path), "--skip-download",
                                            flag, "--sub-langs", key, "--sub-format", "vtt",
                                            "-o", str(Path(tmp) / "cap.%(ext)s")])
            files = sorted(Path(tmp).glob("cap*.vtt"))
            if code != 0 or not files:
                failure = self._failure(url, code, err or "no caption file written")
                self._record(url, 1, failure, None, {**extra, "part": "captions", "track": key})
                outcomes.append(failure.outcome)
                return None
            data = files[0].read_bytes()
        version_id, _ = self._version(source_id, data, "vtt", "text/vtt",
                                      [f"captions_{kind}"])
        self._record(url, 1, FetchResult(url, "ok", http_status=200, content_type="text/vtt"),
                     version_id, {**extra, "part": "captions", "track": key})
        outcomes.append("stored")
        return version_id
