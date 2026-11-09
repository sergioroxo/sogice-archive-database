"""Command line: python -m archive <command>.

    python -m archive import-queue [--queue PATH] [--db PATH] [--dry-run]
    python -m archive fetch [--limit N] [--kind K ...] [--target ID ...] [--retry-failed]
    python -m archive index [--limit N] [--retry-failed]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

from . import db, paths, queue_import
from .acquire import Acquirer, pending_targets
from .depot import Depot
from .fetch import Fetcher
from .index_text import TextIndexer, pending_versions


def cmd_import_queue(args: argparse.Namespace) -> int:
    target = ":memory:" if args.dry_run else args.db
    conn = db.connect(target)
    db.init_db(conn)
    report = queue_import.run_import(args.queue, conn)
    conn.close()
    report["dry_run"] = args.dry_run
    report["database"] = str(target)
    name = f"queue-import-{time.strftime('%Y%m%d-%H%M%S')}{'-dry' if args.dry_run else ''}.json"
    path = queue_import.write_report(report, paths.reports_dir(), name)
    short = {k: v for k, v in report.items() if k not in ("top_domains", "rejected")}
    short["rejected"] = len(report["rejected"])
    print(json.dumps(short, indent=2))
    print(f"report: {path}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    db.init_db(conn)
    targets = pending_targets(conn, limit=args.limit, kinds=args.kind, target_ids=args.target,
                              retry_failed=args.retry_failed)
    run_id = f"run_{uuid.uuid4().hex}"
    with conn:
        conn.execute("INSERT INTO run(id, role, inputs, started_at) VALUES (?, 'other', ?, ?)",
                     (run_id, db.canonical_json({"job": "fetch", "targets": len(targets),
                                                 "kinds": args.kind, "limit": args.limit}),
                      db.utc_now()))
    print(f"{len(targets)} targets; run {run_id}", flush=True)
    tally: Counter = Counter()
    with Fetcher() as fetcher:
        acquirer = Acquirer(conn, fetcher, Depot(paths.data_dir()), run_id=run_id)
        for number, target in enumerate(targets, 1):
            result = acquirer.acquire(target)
            tally[result.status if result.status == "failed" else f"stored_{result.route}"] += 1
            print(f"[{number}/{len(targets)}] {result.status:6} {target['kind_hint']:8} "
                  f"{' > '.join(result.outcomes):40} {target['url'][:90]}", flush=True)
    with conn:
        conn.execute("UPDATE run SET outcome = 'succeeded', finished_at = ? WHERE id = ?",
                     (db.utc_now(), run_id))
    conn.close()
    print(json.dumps(dict(tally), indent=2))
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    db.init_db(conn)
    versions = pending_versions(conn, limit=args.limit, retry_failed=args.retry_failed)
    print(f"{len(versions)} versions to index", flush=True)
    indexer = TextIndexer(conn, Depot(paths.data_dir()))
    tally: Counter = Counter()
    for number, version in enumerate(versions, 1):
        result = indexer.index(version)
        tally[result.status] += 1
        langs = ",".join(f"{k}:{v}" for k, v in list(result.languages.items())[:3])
        print(f"[{number}/{len(versions)}] {result.status:7} {result.chars:>8} chars "
              f"{result.passages:>4} passages  {langs:24} {','.join(result.flags)} "
              f"{result.error or ''}", flush=True)
    conn.close()
    print(json.dumps(dict(tally)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m archive")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import-queue", help="copy the old source queue into source/source_target")
    p.add_argument("--queue", type=Path, default=paths.OLD_QUEUE, help="old source_queue.db (opened read-only)")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--dry-run", action="store_true", help="import into memory only and report")
    p.set_defaults(func=cmd_import_queue)

    p = sub.add_parser("fetch", help="fetch and store pending targets (no text extraction yet)")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--limit", type=int)
    p.add_argument("--kind", action="append", help="only this kind_hint (repeatable)")
    p.add_argument("--target", action="append", help="only this target id (repeatable)")
    p.add_argument("--retry-failed", action="store_true", help="also retry targets that failed before")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("index", help="extract text, cut passages, detect language for stored versions")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--limit", type=int)
    p.add_argument("--retry-failed", action="store_true")
    p.set_defaults(func=cmd_index)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
