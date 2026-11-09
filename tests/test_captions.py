import json
import subprocess
from pathlib import Path

import httpx
import pytest

from archive import db
from archive.captions import VideoAcquirer, choose_track, upload_date_iso
from archive.depot import Depot
from archive.index_text import TextIndexer, pending_versions
from tests.test_fetch import make_fetcher

NOW = "2026-11-09T12:00:00Z"
URL = "https://www.youtube.com/watch?v=abc123"
VTT = ("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nI was told prayer would change me\n\n"
       "00:00:04.000 --> 00:00:08.000\nI was told prayer would change me\nand it never did\n")
INFO = {"id": "abc123", "title": "My story", "channel": "Survivor Voices", "upload_date": "20190314",
        "duration": 600, "language": "en-US", "description": "A survivor talks about a ministry.",
        "subtitles": {}, "automatic_captions": {"en-orig": [{}], "en": [{}], "fr": [{}], "de-DE-orig": [{}]},
        "formats": [{"url": "https://secret.googlevideo.com/x"}]}


class FakeYtdlp:
    def __init__(self, info=None, info_error=None, caption_error=None):
        self.calls, self.info, self.info_error, self.caption_error = [], info, info_error, caption_error

    def __call__(self, cmd, capture_output, timeout, check):
        self.calls.append(cmd)
        if "-J" in cmd:
            if self.info_error:
                return subprocess.CompletedProcess(cmd, 1, b"", self.info_error.encode())
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.info).encode(), b"")
        if self.caption_error:
            return subprocess.CompletedProcess(cmd, 1, b"", self.caption_error.encode())
        out = Path(cmd[cmd.index("-o") + 1]).parent
        key = cmd[cmd.index("--sub-langs") + 1]
        (out / f"cap.{key}.vtt").write_text(VTT)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")


@pytest.fixture
def setup(tmp_path):
    conn = db.connect(tmp_path / "archive.db")
    db.init_db(conn)
    with conn:
        conn.execute("INSERT INTO source(id, url, created_at) VALUES ('s1', ?, ?)", (URL, NOW))
        conn.execute("INSERT INTO source_target(id, source_id, route, url, kind_hint, created_at)"
                     " VALUES ('t1', 's1', 'live_fetch', ?, 'youtube', ?)", (URL, NOW))
    fetcher, _, fake_clock = make_fetcher({})
    yield conn, Depot(tmp_path), fetcher, fake_clock
    conn.close()


def target(conn):
    return conn.execute("SELECT * FROM source_target WHERE id='t1'").fetchone()


def attempts(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM fetch_attempt ORDER BY created_at")]


@pytest.mark.parametrize("info, expected", [
    (INFO, ("en-orig", "auto")),
    ({**INFO, "subtitles": {"en-GB": [{}], "fr": [{}], "live_chat": [{}]}}, ("en-GB", "manual")),
    ({**INFO, "subtitles": {"fr": [{}]}}, ("en-orig", "auto")),          # manual only in another language
    ({**INFO, "language": None}, None),                                   # two -orig tracks, language unknown
    ({**INFO, "language": None, "automatic_captions": {"pt-BR-orig": [{}], "en": [{}]}}, ("pt-BR-orig", "auto")),
    ({**INFO, "language": "es", "automatic_captions": {"en-orig": [{}]}}, None),
])
def test_choose_track_never_translations(info, expected):
    assert choose_track(info) == expected


def test_upload_date():
    assert upload_date_iso("20190314") == "2019-03-14" and upload_date_iso("2019") is None


def test_captions_off_stores_description_and_records_skip(setup):
    conn, depot, fetcher, _ = setup
    fake = FakeYtdlp(INFO)
    result = VideoAcquirer(conn, fetcher, depot, runner=fake, captions=False).acquire(target(conn))
    assert result.status == "stored" and result.outcomes == ["stored", "captions_skipped"]
    assert len(fake.calls) == 1 and "-J" in fake.calls[0]                   # no caption download
    rows = attempts(conn)
    assert [r["outcome"] for r in rows] == ["stored", "robots_disallowed"]
    assert json.loads(rows[1]["detail"])["track"] == "en-orig"
    info_path = json.loads(rows[0]["detail"])["info_path"]
    stored_info = json.loads(depot.read(info_path))
    assert "formats" not in stored_info and stored_info["caption_tracks"]["auto_orig"] == ["de-DE-orig", "en-orig"]
    source = conn.execute("SELECT * FROM source WHERE id='s1'").fetchone()
    assert (source["title"], source["publisher"]) == ("My story", "Survivor Voices")
    assert (source["published_date"], source["published_date_certainty"]) == ("2019-03-14", "exact")
    version = conn.execute("SELECT * FROM source_version").fetchone()
    assert json.loads(version["quality_flags"]) == ["video_description"]
    from archive.captions import caption_backlog
    assert [t["id"] for t in caption_backlog(conn)] == ["t1"]
    # the backlog run fetches only the captions
    backlog = VideoAcquirer(conn, fetcher, depot, runner=fake, captions_only=True).acquire(target(conn))
    assert backlog.outcomes == ["stored"]
    assert [json.loads(r["detail"]).get("part") for r in attempts(conn)][-1] == "captions"
    assert conn.execute("SELECT COUNT(*) FROM source_version").fetchone()[0] == 2
    assert caption_backlog(conn) == []


def test_captions_when_enabled_are_indexed_with_times(setup):
    conn, depot, fetcher, _ = setup
    fake = FakeYtdlp(INFO)
    result = VideoAcquirer(conn, fetcher, depot, runner=fake).acquire(target(conn))  # default: on
    assert result.outcomes == ["stored", "stored"]
    assert "--write-auto-subs" in fake.calls[1] and "en-orig" in fake.calls[1]
    vtt = conn.execute("SELECT * FROM source_version WHERE mime_type='text/vtt'").fetchone()
    assert json.loads(vtt["quality_flags"]) == ["captions_auto"]
    indexer = TextIndexer(conn, depot)
    for version in pending_versions(conn):
        indexer.index(version)
    passage = conn.execute("SELECT * FROM passage WHERE version_id = ?", (vtt["id"],)).fetchone()
    assert passage["text"] == "I was told prayer would change me\nand it never did"
    assert (passage["time_start"], passage["time_end"]) == (1.0, 8.0)


@pytest.mark.parametrize("stderr, outcome, calls", [
    ("ERROR: [youtube] abc123: Video unavailable. This video has been removed", "gone", 1),
    ("ERROR: Sign in to confirm you're not a bot", "blocked", 1),
    ("ERROR: unable to download: HTTP Error 429: Too Many Requests", "blocked", 2),
    ("ERROR: Connection reset by peer", "network_error", 2),
])
def test_failures_and_retry(setup, stderr, outcome, calls):
    conn, depot, fetcher, clock = setup
    fake = FakeYtdlp(info_error=stderr)
    result = VideoAcquirer(conn, fetcher, depot, runner=fake).acquire(target(conn))
    assert result.status == "failed" and result.outcomes == [outcome]
    assert len(fake.calls) == calls and [r["attempt"] for r in attempts(conn)] == list(range(1, calls + 1))


def test_playlists_are_not_expanded(setup):
    conn, depot, fetcher, _ = setup
    result = VideoAcquirer(conn, fetcher, depot, runner=FakeYtdlp({"_type": "playlist", "entries": []})
                           ).acquire(target(conn))
    assert result.outcomes == ["unsupported_type"]
    assert conn.execute("SELECT COUNT(*) FROM source_version").fetchone()[0] == 0


def test_robots_disallowed_video(setup, tmp_path):
    conn, depot, _, _ = setup
    fetcher, _, _ = make_fetcher({}, robots={"www.youtube.com": httpx.Response(200, text="User-agent: *\nDisallow: /watch\n")})
    fake = FakeYtdlp(INFO)
    result = VideoAcquirer(conn, fetcher, depot, runner=fake).acquire(target(conn))
    assert result.outcomes == ["robots_disallowed"] and fake.calls == []
