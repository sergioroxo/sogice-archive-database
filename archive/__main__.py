"""Command line: python -m archive <command>.

    python -m archive import-queue [--queue PATH] [--db PATH] [--dry-run]
    python -m archive preserve [--limit N] [--kind K ...] [--target ID ...] [--retry-failed]
                               [--workers N] [--no-youtube-captions] [--captions-backlog]
                               [--no-index]
    python -m archive index [--limit N] [--retry-failed]
    python -m archive report
    python -m archive attach-pdfs FOLDER [--dry-run]
    python -m archive seed
    python -m archive match
    python -m archive read [--source ID ...] [--testset [--split dev] [--entry KEY ...]]
                           [--prompt read-v3] [--chunk-chars N] [--again] [--dry-run]
    python -m archive read --status
    python -m archive read --testset --both     # two readers, then reconcile
    python -m archive reconcile [--testset|--all|--source ID] [--report-only]
    python -m archive review prepare|apply|status|undo   # the review queue (data/review/)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from . import attach, db, dedupe, match, paths, queue_import, read, report, seeds, testset
from .depot import Depot
from .index_text import TextIndexer, pending_versions
from .acquire import pending_targets, recheck_stored
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


def cmd_seed(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    db.init_db(conn)
    result = seeds.load_all(conn)
    conn.close()
    path = queue_import.write_report(result, paths.reports_dir(), f"seed-{time.strftime('%Y%m%d-%H%M%S')}.json")
    print(json.dumps(result["stats"], indent=2))
    print(f"placeholder concepts (cross-references without an entry): {result['placeholders']}")
    print(f"report: {path}")
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    db.init_db(conn)
    data = match.run_match(conn, log=lambda line: print(line, flush=True))
    conn.close()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = paths.reports_dir()
    queue_import.write_report(data, folder, f"match-{stamp}.json")
    markdown = match.to_markdown(data)
    (folder / f"match-{stamp}.md").write_text(markdown, encoding="utf-8")
    print(markdown[:3000])
    print(f"report: {folder / f'match-{stamp}.md'}")
    return 0


def cmd_recheck(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    db.init_db(conn)
    conn.execute("PRAGMA busy_timeout = 30000")
    found = recheck_stored(conn, Depot(paths.data_dir()), dry_run=args.dry_run)
    for item in found:
        url = conn.execute("SELECT url FROM source WHERE id = ?", (item["source_id"],)).fetchone()[0]
        print(f"{'would flag' if args.dry_run else 'flagged'} {item['version_id']} {item['challenge']} {url}")
    targets = sorted({r[0] for r in conn.execute(
        "SELECT t.id FROM source_target t JOIN fetch_attempt a ON a.target_id = t.id"
        f" WHERE a.version_id IN ({','.join('?' for _ in found)})", [f["version_id"] for f in found])}) if found else []
    conn.close()
    print(f"{len(found)} stored pages are bot checks; their {len(targets)} targets are pending again.")
    if targets and not args.dry_run:
        print("fetch again: python -m archive preserve " + " ".join(f"--target {t}" for t in targets))
    return 0


def cmd_dedupe(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    db.init_db(conn)
    conn.execute("PRAGMA busy_timeout = 30000")
    if args.undo:
        with conn:
            row = dedupe.undo_merge(conn, args.undo, reason=args.reason or "undone by researcher",
                                    actor_id=args.actor)
        print(f"undone: {args.undo} -> {row['id']}")
        return 0
    data = dedupe.run_dedupe(conn, dry_run=args.dry_run)
    conn.close()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = paths.reports_dir()
    queue_import.write_report(data, folder, f"dedupe-{stamp}.json")
    markdown = dedupe.to_markdown(data)
    (folder / f"dedupe-{stamp}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"report: {folder / f'dedupe-{stamp}.md'}")
    return 0


def cmd_testset(args: argparse.Namespace) -> int:
    data = testset.load_testset(args.file)
    conn = db.connect(args.db)
    db.init_db(conn)
    conn.execute("PRAGMA busy_timeout = 30000")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = paths.reports_dir()
    if args.action == "prepare":
        depot = Depot(paths.data_dir())
        result = testset.prepare(conn, depot, data, package_root=args.package)
        sources = [s for e in data["entries"] for s in testset.entry_sources(e)]
        pending = {t["id"] for t in pending_targets(conn, target_ids=[
            v["target_id"] for v in result["targets"].values()])}
        conn.close()
        print(json.dumps({k: v for k, v in result.items() if k != "targets"}, indent=2))
        if pending and not args.no_fetch:
            run_preserve(args.db, paths.data_dir(), target_ids=sorted(pending), workers=4, index=False,
                         log=lambda line: print(line, flush=True))
        conn = db.connect(args.db)
        conn.execute("PRAGMA busy_timeout = 30000")
        indexer = TextIndexer(conn, Depot(paths.data_dir()))
        for version in testset.unindexed_versions(conn, sources):
            outcome = indexer.index(version)
            print(f"indexed {version['source_id']} {outcome.status} {outcome.passages} passages")
        conn.close()
        return 1 if result["problems"] else 0
    if args.action == "show":
        entry = next((e for e in data["entries"] if e["key"] == args.key), None)
        if entry is None:
            print(f"no entry {args.key!r}", file=sys.stderr)
            return 2
        print(testset.show(conn, entry, grep=args.grep, width=args.width))
        return 0
    judgements = json.loads(args.judgements.read_text(encoding="utf-8")) if args.judgements else {}
    result = testset.score(conn, data, judgements, split=args.split)
    conn.close()
    queue_import.write_report(result, folder, f"testset-{stamp}.json")
    markdown = testset.to_markdown(result)
    (folder / f"testset-{stamp}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"report: {folder / f'testset-{stamp}.md'}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    from .config import load_models_config
    from .ollama import OllamaClient
    conn = db.connect(args.db)
    db.init_db(conn)
    conn.execute("PRAGMA busy_timeout = 30000")
    if args.status:
        rows = read.run_status(conn)
        for r in rows:
            p = r["progress"] or {}
            print(f"{r['id']} {r['source_id']}: section {p.get('sections_done', 0)}/{p.get('sections', '?')}, "
                  f"last progress {r['minutes_since_heartbeat']} min ago" + ("  STALLED" if r["stalled"] else ""))
        print(f"{len(rows)} reading run(s) in progress")
        return 1 if any(r["stalled"] for r in rows) else 0
    sources = list(args.source or [])
    if args.testset:
        data = testset.load_testset(args.file)
        for entry in data["entries"]:
            if args.split and entry["split"] != args.split:
                continue
            if args.entry and entry["key"] not in args.entry:
                continue
            sources += testset.entry_sources(entry)
    if not sources:
        print("nothing to read: give --source or --testset", file=sys.stderr)
        return 2
    prompt = read.load_prompt(args.prompt)
    if args.dry_run:
        for requested in sources:
            sid = read.keeper(conn, requested)
            units, skipped = read.source_units(conn, sid)
            chunks = read.make_chunks(units, args.chunk_chars)
            print(f"{sid}: {len(units)} passages, {len(chunks)} sections, "
                  f"{sum(len(u['text']) for u in units)} chars; skipped {skipped}")
        if chunks:
            messages, _ = read.build_messages(prompt, read.source_header(conn, sid), chunks[0], None,
                                              (1, len(chunks)))
            print("\n--- first section of the last source ---\n" + messages[1]["content"][:3000])
        return 0
    cfg = load_models_config()
    role = cfg.role("reading")
    if args.model_role != "reading":  # another role's model with the reading settings (comparisons)
        import dataclasses
        if args.model_role == "embedding":
            print("the embedding model cannot read", file=sys.stderr)
            return 2
        if args.model_role.startswith("trial_") and Path(args.db).resolve() == paths.database_path().resolve():
            print("trial models read into a copy of the database only (--db data/scratch/…)", file=sys.stderr)
            return 2
        role = dataclasses.replace(role, model=cfg.model_for(args.model_role))
    client = OllamaClient(cfg.base_url, cfg.timeout_seconds)
    log = lambda line: print(line, flush=True)
    if args.both:  # two independent readings, batch by batch (one resident model at a time), then compare
        import dataclasses
        from . import reconcile
        second = dataclasses.replace(role, model=cfg.model_for("reading_second"))
        results = []
        for start in range(0, len(sources), args.batch):
            batch = sources[start:start + args.batch]
            for layer in (role, second):
                log(f"=== batch {start // args.batch + 1}: {layer.model} reads {len(batch)} source(s)")
                results += read.read_sources(conn, client, layer, batch, prompt=prompt, chunk_chars=args.chunk_chars,
                                             again=args.again, log=log)
            keepers = sorted({read.keeper(conn, s) for s in batch})
            log(f"=== batch {start // args.batch + 1}: reconcile {json.dumps(reconcile.reconcile(conn, keepers, primary_model=role.model, models=[role.model, second.model])['counts'])}")
    else:
        results = read.read_sources(conn, client, role, sources, prompt=prompt, chunk_chars=args.chunk_chars,
                                    again=args.again, log=log)
    conn.close()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    queue_import.write_report({"prompt": prompt.version, "model": role.model, "results": results},
                              paths.reports_dir(), f"read-{stamp}.json")
    outcomes = Counter(r["outcome"] for r in results)
    print(json.dumps(dict(outcomes)))
    return 1 if outcomes.get("failed") else 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    from . import reconcile
    from .config import load_models_config
    conn = db.connect(args.db)
    db.init_db(conn)
    conn.execute("PRAGMA busy_timeout = 30000")
    sources = list(args.source or [])
    if args.testset:
        data = testset.load_testset(args.file)
        sources += [s for e in data["entries"] if not args.split or e["split"] == args.split
                    for s in testset.entry_sources(e)]
    if args.all:
        sources += [r[0] for r in conn.execute(
            "SELECT DISTINCT json_extract(inputs, '$.source_id') FROM run WHERE role = 'reading' AND outcome = 'succeeded'")]
    from .read import keeper
    sources = sorted({keeper(conn, s) for s in sources})
    if sources and not args.report_only:
        cfg = load_models_config()
        primary, second = cfg.model_for("reading"), cfg.model_for("reading_second")
        print(json.dumps(reconcile.reconcile(conn, sources, primary_model=primary, models=[primary, second]), indent=1))
    report = reconcile.difficulties(conn, sources or None)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = paths.reports_dir()
    queue_import.write_report(report, folder, f"difficulties-{stamp}.json")
    markdown = reconcile.difficulties_markdown(report)
    (folder / f"difficulties-{stamp}.md").write_text(markdown, encoding="utf-8")
    print(markdown[:4000])
    print(f"report: {folder / f'difficulties-{stamp}.md'}")
    conn.close()
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from . import review
    from .config import load_models_config
    base = args.folder
    if args.action == "status":
        print(json.dumps(review.status(base), indent=1))
        return 0
    conn = db.connect(args.db)
    db.init_db(conn)
    conn.execute("PRAGMA busy_timeout = 30000")
    if args.action == "prepare":
        sources = list(args.source or [])
        if args.testset:
            data = testset.load_testset(args.file)
            sources += [s for e in data["entries"] if not args.split or e["split"] == args.split
                        for s in testset.entry_sources(e)]
        from .read import keeper
        sources = sorted({keeper(conn, s) for s in sources}) or None
        print(json.dumps(review.prepare(conn, sources, base=base), indent=1))
    elif args.action == "apply":
        cfg = load_models_config()
        reviewer = args.reviewer or cfg.review_model
        if not reviewer:
            print("no reviewer: set review.model in config/models.yaml or pass --reviewer", file=sys.stderr)
            return 2
        result = review.apply(conn, reviewer=reviewer, base=base, prompt_version=cfg.review_prompt or review.PROMPT)
        print(json.dumps(result, indent=1, ensure_ascii=False))
    elif args.action == "undo":
        if not args.run:
            print("undo needs --run RUN_ID (the review run to reverse)", file=sys.stderr)
            return 2
        n = review.undo(conn, args.run, reason=args.reason or "review run undone by researcher", actor_id=args.actor)
        print(f"undone: {n} decision(s) of {args.run}")
    conn.close()
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

    p = sub.add_parser("seed", help="load seed vocabularies, entities and merge decisions")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("match", help="exact matching of seed labels across all passages")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("recheck", help="flag stored pages that today's detection calls bot checks")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_recheck)

    p = sub.add_parser("dedupe", help="merge sources that are the same work (YouTube id, DOI) as recorded decisions")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--dry-run", action="store_true", help="list the merges, change nothing")
    p.add_argument("--undo", metavar="DECISION_ID", help="reverse one merge decision")
    p.add_argument("--reason", help="with --undo: why")
    p.add_argument("--actor", default="sergio", help="with --undo: researcher id")
    p.set_defaults(func=cmd_dedupe)

    p = sub.add_parser("testset", help="Phase 3 test set: prepare sources, show text, score results")
    p.add_argument("action", choices=["prepare", "show", "score"])
    p.add_argument("key", nargs="?", help="entry key (for show)")
    p.add_argument("--file", type=Path, default=paths.REPO_ROOT / "evaluation" / "testset_2026-11-10.json")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--package", type=Path, default=testset.PACKAGE_ROOT, help="Transfer package folder")
    p.add_argument("--no-fetch", action="store_true", help="prepare: queue and attach only")
    p.add_argument("--grep", help="show: only passages containing this text")
    p.add_argument("--width", type=int, help="show: cut each passage to this many characters")
    p.add_argument("--split", choices=["dev", "holdout"], help="score: one split only")
    p.add_argument("--judgements", type=Path, help="score: verdicts for judged expectations (JSON)")
    p.set_defaults(func=cmd_testset)

    p = sub.add_parser("read", help="Phase 3: local model reads sources; software locates the quotes")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--source", action="append", help="source id (repeatable)")
    p.add_argument("--testset", action="store_true", help="read the test-set sources")
    p.add_argument("--file", type=Path, default=paths.REPO_ROOT / "evaluation" / "testset_2026-11-10.json")
    p.add_argument("--split", choices=["dev", "holdout"], help="with --testset: one split only")
    p.add_argument("--entry", action="append", help="with --testset: only this entry key (repeatable)")
    p.add_argument("--prompt", default=read.DEFAULT_PROMPT, help="prompt version (file in prompts/)")
    p.add_argument("--chunk-chars", type=int, default=read.DEFAULT_CHUNK_CHARS)
    p.add_argument("--again", action="store_true", help="read even if already read with this prompt")
    p.add_argument("--dry-run", action="store_true", help="show sections and the first prompt; no model call")
    p.add_argument("--status", action="store_true", help="show runs in progress; exit 1 if one has stalled")
    p.add_argument("--both", action="store_true",
                   help="read with the reading and reading_second models, then reconcile (batch by batch)")
    p.add_argument("--batch", type=int, default=25, help="with --both: sources per batch")
    p.add_argument("--model-role", default="reading",
                   help="use this role's model (config/models.yaml: audit, trial_…) with the reading settings")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("reconcile", help="compare the two models' readings; write the difficulties report")
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--source", action="append")
    p.add_argument("--testset", action="store_true")
    p.add_argument("--split", choices=["dev", "holdout"])
    p.add_argument("--file", type=Path, default=paths.REPO_ROOT / "evaluation" / "testset_2026-11-10.json")
    p.add_argument("--all", action="store_true", help="every source with a reading")
    p.add_argument("--report-only", action="store_true", help="write the difficulties report, change nothing")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("review", help="the review queue: packets for disputed and held records, verdicts, undo")
    p.add_argument("action", choices=["prepare", "apply", "status", "undo"])
    p.add_argument("--db", type=Path, default=paths.database_path())
    p.add_argument("--folder", type=Path, help="queue folder (default data/review)")
    p.add_argument("--source", action="append", help="with prepare: only this source")
    p.add_argument("--testset", action="store_true", help="with prepare: the test-set sources")
    p.add_argument("--split", choices=["dev", "holdout"])
    p.add_argument("--file", type=Path, default=paths.REPO_ROOT / "evaluation" / "testset_2026-11-10.json")
    p.add_argument("--reviewer", help="with apply: the reviewing model's name (default: review.model in the config)")
    p.add_argument("--run", help="with undo: the review run to reverse")
    p.add_argument("--reason", help="with undo: why")
    p.add_argument("--actor", default="sergio", help="with undo: researcher id")
    p.set_defaults(func=cmd_review)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
