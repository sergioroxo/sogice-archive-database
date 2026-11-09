"""Preserve pending targets: fetch (or read YouTube metadata), then index text.

Resumable and idempotent: stored targets are never fetched again, failed
ones only with ``retry_failed``, and indexing only touches versions without
text. Stop it at any time and run it again.

Targets are interleaved by host so that parallel workers rarely wait for the
same site; the Fetcher still allows one request per host at a time (and one
yt-dlp call at a time for YouTube). Each worker thread has its own SQLite
connection (WAL mode).
"""
from __future__ import annotations

import threading
import uuid
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from . import db
from .acquire import VIDEO_KINDS, Acquirer, pending_targets
from .captions import VideoAcquirer
from .depot import Depot
from .fetch import Fetcher
from .index_text import TextIndexer, pending_versions
from .urls import UrlError, host_of, parse_wayback


def target_host(url: str) -> str:
    try:
        if parse_wayback(url):
            return "web.archive.org"
    except UrlError:
        pass
    return host_of(url)


def interleave_by_host(targets: list) -> list:
    """Round-robin over hosts, keeping each host's own order."""
    queues: dict[str, deque] = defaultdict(deque)
    for target in targets:
        queues[target_host(target["url"])].append(target)
    order, hosts = [], deque(sorted(queues, key=lambda h: -len(queues[h])))
    while hosts:
        host = hosts.popleft()
        order.append(queues[host].popleft())
        if queues[host]:
            hosts.append(host)
    return order


def run_preserve(db_path: str | Path, data_dir: str | Path, *, limit: int | None = None,
                 kinds: list[str] | None = None, target_ids: list[str] | None = None,
                 retry_failed: bool = False, workers: int = 6, youtube_captions: bool = False,
                 index: bool = True, fetcher: Fetcher | None = None, video_runner=None,
                 log: Callable[[str], None] = print) -> dict:
    conn = db.connect(db_path)
    db.init_db(conn)
    targets = interleave_by_host(pending_targets(conn, limit=limit, kinds=kinds,
                                                 target_ids=target_ids, retry_failed=retry_failed))
    run_id = f"run_{uuid.uuid4().hex}"
    with conn:
        conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                     (run_id, db.canonical_json({
                         "job": "preserve", "targets": len(targets), "kinds": kinds, "limit": limit,
                         "retry_failed": retry_failed, "workers": workers,
                         "youtube_captions": youtube_captions}), db.utc_now()))
    log(f"{len(targets)} targets, {workers} workers; run {run_id}")

    depot = Depot(data_dir)
    own_fetcher = fetcher is None
    fetcher = fetcher or Fetcher()
    local = threading.local()
    connections: list = []
    lock = threading.Lock()
    tally: Counter = Counter()
    done = 0

    def worker(target):
        if not hasattr(local, "conn"):
            local.conn = db.connect(db_path, check_same_thread=False)
            local.conn.execute("PRAGMA busy_timeout = 30000")
            with lock:
                connections.append(local.conn)
        if target["kind_hint"] in VIDEO_KINDS:
            extra = {"runner": video_runner} if video_runner else {}
            acquirer = VideoAcquirer(local.conn, fetcher, depot, run_id=run_id,
                                     captions=youtube_captions, **extra)
        else:
            acquirer = Acquirer(local.conn, fetcher, depot, run_id=run_id)
        return target, acquirer.acquire(target)

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(worker, target) for target in targets]
            for future in as_completed(futures):
                try:
                    target, result = future.result()
                except Exception as exc:  # noqa: BLE001 - one bad target must not stop the batch
                    tally["error"] += 1
                    log(f"ERROR {type(exc).__name__}: {exc}")
                    continue
                done += 1
                key = result.status if result.status == "failed" else f"stored_{result.route}"
                tally[key] += 1
                log(f"[{done}/{len(targets)}] {result.status:6} {target['kind_hint']:8} "
                    f"{' > '.join(result.outcomes)[:44]:44} {target['url'][:90]}")
    finally:
        for connection in connections:
            connection.close()
        if own_fetcher:
            fetcher.close()

    summary = {"run_id": run_id, "targets": len(targets), "fetch": dict(tally)}
    if index:
        indexer = TextIndexer(conn, depot)
        index_tally: Counter = Counter()
        versions = pending_versions(conn)
        for version in versions:
            index_tally[indexer.index(version).status] += 1
        summary["index"] = dict(index_tally)
        log(f"indexed {len(versions)} versions: {dict(index_tally)}")
    with conn:
        conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ?, inputs = json_set(inputs,"
                     " '$.summary', json(?)) WHERE id = ?",
                     (db.utc_now(), db.canonical_json(summary), run_id))
    conn.close()
    return summary
