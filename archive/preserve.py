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

import random
import threading
import uuid
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from . import db
from .acquire import VIDEO_KINDS, Acquirer, pending_targets
from .captions import VideoAcquirer, caption_backlog
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


# Pilot sample: every kind represented, roughly in proportion to the queue,
# with Wayback captures among the web pages. Sums to 50.
PILOT_QUOTAS = {("webpage", "live_fetch"): 16, ("webpage", "wayback"): 6, ("pdf", None): 8,
                ("docx", None): 3, ("social", None): 3, ("video", None): 6, ("youtube", None): 5,
                ("audio", None): 2, ("unknown", None): 1}


def pilot_sample(targets: list, size: int = 50, seed: int = 1) -> list:
    """Stratified random sample: kind (and route for web pages) quotas, one per domain.

    Quotas are scaled to ``size``; places a kind cannot fill go to live web
    pages. Deterministic for a given seed and queue.
    """
    rng = random.Random(seed)
    total = sum(PILOT_QUOTAS.values())
    quotas = {key: max(1, round(n * size / total)) for key, n in PILOT_QUOTAS.items()}
    pools: dict[tuple, list] = defaultdict(list)
    for target in sorted(targets, key=lambda row: row["id"]):
        route = target["route"] if target["kind_hint"] == "webpage" else None
        pools[(target["kind_hint"], route)].append(target)
    for pool in pools.values():
        rng.shuffle(pool)
    chosen: list = []
    chosen_ids: set = set()
    domains: set = set()

    def take(pool: list, want: int) -> None:
        for target in pool:
            if want <= 0 or len(chosen) >= size:
                return
            url = target["url"]
            ref = parse_wayback(url) if target["route"] == "wayback" else None
            domain = host_of(ref.original_url) if ref else target_host(url)
            if target["id"] in chosen_ids or domain in domains:
                continue
            chosen.append(target)
            chosen_ids.add(target["id"])
            domains.add(domain)
            want -= 1

    for key, want in quotas.items():
        take(pools.get(key, []), want)
    take(pools.get(("webpage", "live_fetch"), []), size - len(chosen))
    return chosen


def run_preserve(db_path: str | Path, data_dir: str | Path, *, limit: int | None = None,
                 kinds: list[str] | None = None, target_ids: list[str] | None = None,
                 retry_failed: bool = False, workers: int = 6, youtube_captions: bool = True,
                 captions_backlog: bool = False, targets: list | None = None,
                 index: bool = True, fetcher: Fetcher | None = None, video_runner=None,
                 log: Callable[[str], None] = print) -> dict:
    conn = db.connect(db_path)
    db.init_db(conn)
    if targets is None:
        targets = (caption_backlog(conn, limit) if captions_backlog else
                   pending_targets(conn, limit=limit, kinds=kinds, target_ids=target_ids,
                                   retry_failed=retry_failed))
    targets = interleave_by_host(targets)
    run_id = f"run_{uuid.uuid4().hex}"
    with conn:
        conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                     (run_id, db.canonical_json({
                         "job": "preserve", "targets": len(targets), "kinds": kinds, "limit": limit,
                         "retry_failed": retry_failed, "workers": workers,
                         "youtube_captions": youtube_captions,
                         "captions_backlog": captions_backlog}), db.utc_now()))
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
                                     captions=youtube_captions, captions_only=captions_backlog,
                                     **extra)
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
