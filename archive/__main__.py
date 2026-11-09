"""Command line: python -m archive <command>.

    python -m archive import-queue [--queue PATH] [--db PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import db, paths, queue_import


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m archive")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import-queue", help="copy the old source queue into source/source_target")
    p.add_argument("--queue", type=Path, default=paths.OLD_QUEUE, help="old source_queue.db (opened read-only)")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--dry-run", action="store_true", help="import into memory only and report")
    p.set_defaults(func=cmd_import_queue)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
