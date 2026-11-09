"""Command line: python -m archive <command>.

    python -m archive import-queue [--queue PATH] [--db PATH] [--dry-run]
    python -m archive preserve [--limit N] [--kind K ...] [--target ID ...] [--retry-failed]
                               [--workers N] [--no-youtube-captions] [--captions-backlog]
                               [--no-index]
    python -m archive index [--limit N] [--retry-failed]
    python -m archive report
    python -m archive attach-pdfs FOLDER [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from . import attach, db, paths, queue_import, report
from .depot import Depot
from .index_text import TextIndexer, pending_versions
from .acquire import pending_targets
from .preserve import pilot_sample, run_preserve


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


def cmd_preserve(args: argparse.Namespace) -> int:
    targets = None
    if args.pilot:
        conn = db.connect(args.db)
        db.init_db(conn)
        targets = pilot_sample(pending_targets(conn, kinds=args.kind), args.pilot, args.seed)
        conn.close()
    summary = run_preserve(args.db, paths.data_dir(), targets=targets, limit=args.limit, kinds=args.kind,
                           target_ids=args.target, retry_failed=args.retry_failed,
                           workers=args.workers, youtube_captions=not args.no_youtube_captions,
                           captions_backlog=args.captions_backlog,
                           index=not args.no_index, log=lambda line: print(line, flush=True))
    print(json.dumps(summary, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    db.init_db(conn)
    data = report.build_report(conn)
    conn.close()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = paths.reports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"preserve-{stamp}.json").write_text(report.report_json(data), encoding="utf-8")
    markdown = report.to_markdown(data)
    (folder / f"preserve-{stamp}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"report: {folder / f'preserve-{stamp}.md'}")
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


def cmd_attach_pdfs(args: argparse.Namespace) -> int:
    folder = args.folder.expanduser()
    if not folder.is_dir():
        print(f"not a folder: {folder}")
        return 2
    conn = db.connect(args.db)
    db.init_db(conn)
    depot = Depot(paths.data_dir())
    summary = attach.run_attach(conn, depot, folder, dry_run=args.dry_run,
                                log=lambda line: print(line, flush=True))
    if not args.dry_run:
        indexer = TextIndexer(conn, depot)
        versions = pending_versions(conn)
        summary["indexed"] = dict(Counter(indexer.index(v).status for v in versions))
    conn.close()
    name = f"attach-{time.strftime('%Y%m%d-%H%M%S')}{'-dry' if args.dry_run else ''}.json"
    path = queue_import.write_report(summary, paths.reports_dir(), name)
    print(json.dumps({k: summary[k] for k in ("files", "counts")} | {"indexed": summary.get("indexed")}, indent=2))
    print(f"{len(summary['review'])} to review, {len(summary['unmatched'])} unmatched; report: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m archive")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import-queue", help="copy the old source queue into source/source_target")
    p.add_argument("--queue", type=Path, default=paths.OLD_QUEUE, help="old source_queue.db (opened read-only)")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--dry-run", action="store_true", help="import into memory only and report")
    p.set_defaults(func=cmd_import_queue)

    p = sub.add_parser("preserve", help="fetch pending targets, then index their text")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--limit", type=int)
    p.add_argument("--kind", action="append", help="only this kind_hint (repeatable)")
    p.add_argument("--target", action="append", help="only this target id (repeatable)")
    p.add_argument("--retry-failed", action="store_true", help="also retry targets that failed before")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--no-youtube-captions", action="store_true",
                   help="skip YouTube captions (served from /api/, which robots.txt disallows)")
    p.add_argument("--captions-backlog", action="store_true",
                   help="only fetch YouTube captions that earlier runs skipped")
    p.add_argument("--no-index", action="store_true", help="fetch only")
    p.add_argument("--pilot", type=int, metavar="N", help="a stratified sample of N pending targets")
    p.add_argument("--seed", type=int, default=1, help="random seed for --pilot")
    p.set_defaults(func=cmd_preserve)

    p = sub.add_parser("index", help="extract text, cut passages, detect language for stored versions")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--limit", type=int)
    p.add_argument("--retry-failed", action="store_true")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("report", help="gate figure, targets by kind, failures by domain")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("attach-pdfs", help="attach the researcher's own PDFs to their sources")
    p.add_argument("folder", type=Path, help="folder of PDFs (searched recursively, only read)")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--dry-run", action="store_true", help="show the matches, attach nothing")
    p.set_defaults(func=cmd_attach_pdfs)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
