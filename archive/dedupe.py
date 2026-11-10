"""One work, one source: duplicates that URL identity misses.

``urls.identity_key`` treats ``youtu.be/<id>?si=…`` and ``youtube.com/watch?v=<id>``
as different sources, and so with ``doi.org/<doi>``, ``…/doi/abs/<doi>``,
``…/doi/full/<doi>`` and ``…/doi/pdf/<doi>``. Source ids are not changed
(re-imports must keep finding their sources); a second key, the *work key*,
names the work behind a URL: ``youtube:<video id>`` or ``doi:<doi>``.

- New URLs whose work key a stored source already has attach to that source
  (an identifier match, like an exact repeat): see ``queue_import.apply_import``.
- Sources already stored twice are merged by ``run_dedupe``: the duplicate gets
  ``source.merged_into = <keeper>`` and a recorded 'merge' decision (rule,
  key, keeper, reason). Nothing is moved or deleted; its targets and versions
  stay as further copies of the work, and ``undo_merge`` reverses it.

Readers and reports count a merged source under its keeper. Titles and text
similarity never merge sources.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from urllib.parse import parse_qs, urlsplit

from .db import canonical_json, record_decision, utc_now
from .doi import doi_candidates
from .urls import UrlError, parse_wayback

RULE = "work_key/v1"
ACTOR_ID = f"rule:{RULE}"
YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
                 "youtube-nocookie.com"}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_NOT_THE_WORK = re.compile(r"/(suppl|suppl_file|figure|figures|citedby|references|toc)(/|$)", re.I)


def _original(url: str) -> str:
    try:
        ref = parse_wayback(url)
    except UrlError:
        return url
    return ref.original_url if ref else url


def youtube_id(url: str) -> str | None:
    parts = urlsplit(_original(url))
    host = (parts.hostname or "").lower().removeprefix("www.")
    if host not in YOUTUBE_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if host == "youtu.be":
        candidate = segments[0] if segments else None
    elif segments[:1] == ["watch"]:
        candidate = (parse_qs(parts.query).get("v") or [None])[0]
    elif len(segments) >= 2 and segments[0] in ("shorts", "embed", "live", "v"):
        candidate = segments[1]
    else:
        candidate = None
    return candidate if candidate and _VIDEO_ID.match(candidate) else None


def work_key(url: str | None) -> str | None:
    """'youtube:<id>' or 'doi:<doi>' for a URL naming that work, else None."""
    if not url:
        return None
    video = youtube_id(url)
    if video:
        return f"youtube:{video}"
    original = _original(url)
    if _NOT_THE_WORK.search(urlsplit(original).path):
        return None
    dois = doi_candidates(original)
    return f"doi:{dois[0]}" if dois else None


def _has_merged_column(conn: sqlite3.Connection) -> bool:
    return any(row[1] == "merged_into" for row in conn.execute("PRAGMA table_info(source)"))


def source_keys(conn: sqlite3.Connection) -> dict[str, set]:
    """source id -> work keys of its URL and its targets' URLs."""
    keys: dict[str, set] = {}
    rows = conn.execute("SELECT id, url FROM source").fetchall()
    rows += conn.execute("SELECT source_id, url FROM source_target").fetchall()
    for source_id, url in rows:
        key = work_key(url)
        if key:
            keys.setdefault(source_id, set()).add(key)
    return keys


def keeper_of(conn: sqlite3.Connection) -> dict[str, str]:
    """source id -> the source it is merged into (only merged sources)."""
    if not _has_merged_column(conn):
        return {}
    return dict(conn.execute("SELECT id, merged_into FROM source WHERE merged_into IS NOT NULL").fetchall())


def key_index(conn: sqlite3.Connection) -> dict[str, str]:
    """work key -> the source that holds it (a keeper, never a merged duplicate)."""
    merged = keeper_of(conn)
    index: dict[str, str] = {}
    for source_id, keys in sorted(source_keys(conn).items()):
        holder = merged.get(source_id, source_id)
        for key in keys:
            index.setdefault(key, holder)
    return index


def find_groups(conn: sqlite3.Connection) -> list[dict]:
    """Unmerged sources sharing a work key, joined transitively."""
    merged = keeper_of(conn)
    parent: dict[str, str] = {}

    def root(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    by_key: dict[str, list] = {}
    for source_id, keys in source_keys(conn).items():
        holder = merged.get(source_id, source_id)
        for key in keys:
            by_key.setdefault(key, []).append(holder)
    for holders in by_key.values():
        for other in holders[1:]:
            a, b = root(holders[0]), root(other)
            if a != b:
                parent[max(a, b)] = min(a, b)
    groups: dict[str, set] = {}
    for holders in by_key.values():
        for holder in holders:
            groups.setdefault(root(holder), set()).add(holder)
    keys_of = {}
    for key, holders in by_key.items():
        for holder in holders:
            keys_of.setdefault(root(holder), set()).add(key)
    return [{"sources": sorted(members), "keys": sorted(keys_of[r])}
            for r, members in sorted(groups.items()) if len(members) > 1]


def _text_chars(conn: sqlite3.Connection, source_id: str) -> int:
    row = conn.execute("""
        SELECT MAX(chars) FROM (SELECT SUM(length(p.text)) AS chars FROM passage p
        JOIN source_version v ON v.id = p.version_id WHERE v.source_id = ? GROUP BY v.id)""",
                       (source_id,)).fetchone()
    return row[0] or 0


def choose_keeper(conn: sqlite3.Connection, sources: list[str]) -> tuple[str, dict]:
    """The copy with the most text; then a stored version; then the shortest URL."""
    facts = {}
    for sid in sources:
        url = conn.execute("SELECT url FROM source WHERE id = ?", (sid,)).fetchone()[0] or ""
        versions = conn.execute("SELECT COUNT(*) FROM source_version WHERE source_id = ?", (sid,)).fetchone()[0]
        facts[sid] = {"url": url, "text_chars": _text_chars(conn, sid), "versions": versions}
    keeper = min(sources, key=lambda s: (-facts[s]["text_chars"], -facts[s]["versions"],
                                         len(facts[s]["url"]), s))
    return keeper, facts


def merge_source(conn: sqlite3.Connection, duplicate: str, into: str, *, reason: str, actor_kind: str,
                 actor_id: str, run_id: str | None = None, work_keys: list | None = None) -> dict:
    """Mark ``duplicate`` as the same work as ``into``, as one recorded decision."""
    if duplicate == into:
        raise ValueError("a source cannot be merged into itself")
    rows = dict(conn.execute("SELECT id, merged_into FROM source WHERE id IN (?, ?)", (duplicate, into)).fetchall())
    if set(rows) != {duplicate, into}:
        raise ValueError(f"unknown source: {sorted({duplicate, into} - set(rows))}")
    if rows[duplicate] or rows[into]:
        raise ValueError("both sources must be unmerged")
    conn.execute("UPDATE source SET merged_into = ? WHERE id = ?", (into, duplicate))
    return record_decision(conn, actor_kind=actor_kind, actor_id=actor_id, run_id=run_id, action="merge",
                           target_table="source", target_id=duplicate, reason=reason,
                           previous_state={"merged_into": None},
                           new_state={"merged_into": into, "work_keys": work_keys or []})


def undo_merge(conn: sqlite3.Connection, decision_id: str, *, reason: str, actor_kind: str = "researcher",
               actor_id: str, run_id: str | None = None) -> dict:
    row = conn.execute("SELECT * FROM decision WHERE id = ?", (decision_id,)).fetchone()
    if row is None or row["action"] != "merge" or row["target_table"] != "source":
        raise ValueError(f"{decision_id} is not a source merge")
    if conn.execute("SELECT 1 FROM decision WHERE reverts_decision_id = ?", (decision_id,)).fetchone():
        raise ValueError(f"{decision_id} was already undone")
    conn.execute("UPDATE source SET merged_into = NULL WHERE id = ?", (row["target_id"],))
    return record_decision(conn, actor_kind=actor_kind, actor_id=actor_id, run_id=run_id, action="undo",
                           target_table="source", target_id=row["target_id"], reason=reason,
                           reverts_decision_id=decision_id, previous_state=row["new_state"],
                           new_state={"merged_into": None})


def run_dedupe(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    groups = find_groups(conn)
    run_id = f"run_{uuid.uuid4().hex}"
    plan = []
    for group in groups:
        keeper, facts = choose_keeper(conn, group["sources"])
        plan.append({"keys": group["keys"], "keeper": keeper,
                     "copies": [{"source_id": s, **facts[s], "role": "keeper" if s == keeper else "merged"}
                                for s in group["sources"]]})
    decisions = 0
    if not dry_run and plan:
        with conn:
            conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                         (run_id, canonical_json({"job": "dedupe", "rule": RULE, "groups": len(plan)}),
                          utc_now()))
            for item in plan:
                for copy in item["copies"]:
                    if copy["role"] != "merged":
                        continue
                    merge_source(conn, copy["source_id"], item["keeper"], actor_kind="model", actor_id=ACTOR_ID,
                                 run_id=run_id, work_keys=item["keys"],
                                 reason=(f"Same work identifier ({', '.join(item['keys'])}) as {item['keeper']};"
                                         f" the keeper is the copy with the most stored text."))
                    decisions += 1
            conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ? WHERE id = ?", (utc_now(), run_id))
    return {"rule": RULE, "dry_run": dry_run, "run_id": None if dry_run or not plan else run_id,
            "groups": len(plan), "merges": decisions if not dry_run else sum(len(i["copies"]) - 1 for i in plan),
            "plan": plan}


def to_markdown(report: dict) -> str:
    lines = [f"# Source dedupe ({report['rule']}{', dry run' if report['dry_run'] else ''})", "",
             f"{report['groups']} groups; {report['merges']} merges.", "",
             "| Work key | Keeper | Merged copy | Text chars (keeper / copy) |", "|---|---|---|---|"]
    for item in report["plan"]:
        keeper = next(c for c in item["copies"] if c["role"] == "keeper")
        for copy in item["copies"]:
            if copy["role"] == "merged":
                lines.append(f"| {', '.join(item['keys'])} | {keeper['url'][:70]} | {copy['url'][:70]} | "
                             f"{keeper['text_chars']} / {copy['text_chars']} |")
    return "\n".join(lines) + "\n"
