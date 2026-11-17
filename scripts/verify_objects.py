"""Check every stored object against the database (docs/ORCHESTRATION.md S0). Read-only.

    python scripts/verify_objects.py                       # data/archive.db, objects under data/
    python scripts/verify_objects.py --db DEST/archive-20261117-0900.db   # check a backup made by backup.sh
    python scripts/verify_objects.py --json data/reports/objects-$(date +%F).json

For every ``source_version`` row it checks the preserved file (``path``, against ``sha256``) and,
when there is one, the extracted text (``text_path``, against ``text_sha256``): the file exists
and the SHA-256 of its bytes matches. Several rows may share one object; each object is hashed
once. It prints counts and every missing or mismatched object with its source ids.

It never fetches, deletes, moves or restores anything, and opens the database read-only.
Missing objects are usually Wayback HTML that Microsoft Defender quarantined: never restore
from quarantine and do not fetch again by default (the same capture is quarantined again).
Copy the object from a backup, or list the source for Sérgio.

Exit status: 0 when every object is present and matches, 1 when some are not, 2 when the
database cannot be opened.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from archive import paths  # noqa: E402
from archive.depot import Depot  # noqa: E402

CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def open_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"no database at {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def expected_objects(conn: sqlite3.Connection) -> dict[str, dict]:
    """Every object the database names: relative path -> expected sha256, kind, source ids."""
    objects: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT source_id, id, path, sha256, text_path, text_sha256 FROM source_version ORDER BY path")
    for row in rows:
        for kind, rel, sha in (("file", row["path"], row["sha256"]),
                               ("text", row["text_path"], row["text_sha256"])):
            if not rel:
                continue
            entry = objects.setdefault(rel, {"sha256": sha, "kind": kind,
                                             "source_ids": set(), "version_ids": set(),
                                             "conflicting_sha256": set()})
            if sha and entry["sha256"] and sha != entry["sha256"]:
                entry["conflicting_sha256"].add(sha)
            entry["source_ids"].add(row["source_id"])
            entry["version_ids"].add(row["id"])
    return objects


def verify(db_path: Path, data_dir: Path, *, progress=None) -> dict:
    conn = open_read_only(db_path)
    try:
        versions = conn.execute("SELECT count(*) FROM source_version").fetchone()[0]
        objects = expected_objects(conn)
    finally:
        conn.close()
    depot = Depot(data_dir)
    counts = defaultdict(int)
    problems: list[dict] = []
    for n, (rel, entry) in enumerate(sorted(objects.items()), 1):
        status = "ok"
        actual = None
        try:
            path = depot.absolute(rel)
        except ValueError:
            status = "bad_path"
        else:
            if not path.is_file():
                status = "missing"
            else:
                try:
                    actual = sha256_file(path)
                except OSError as exc:
                    status = f"unreadable: {exc.strerror or exc}"
                else:
                    expected = entry["sha256"] or path.name.split(".", 1)[0]
                    if actual != expected or entry["conflicting_sha256"]:
                        status = "mismatch"
        key = status.split(":", 1)[0]
        counts[f"{entry['kind']}_{key}"] += 1
        if status != "ok":
            problems.append({"status": status, "kind": entry["kind"], "path": rel,
                             "expected_sha256": entry["sha256"], "actual_sha256": actual,
                             "source_ids": sorted(entry["source_ids"]),
                             "version_ids": sorted(entry["version_ids"])})
        if progress and n % 1000 == 0:
            progress(n, len(objects))
    return {"database": str(db_path), "data_dir": str(data_dir),
            "source_versions": versions, "objects": len(objects),
            "counts": dict(sorted(counts.items())), "problems": problems}


def render(report: dict) -> str:
    c = report["counts"]
    lines = [f"database:        {report['database']}",
             f"data folder:     {report['data_dir']}",
             f"source versions: {report['source_versions']}",
             f"objects named:   {report['objects']}"]
    for kind in ("file", "text"):
        total = sum(v for k, v in c.items() if k.startswith(kind + "_"))
        others = {k.split("_", 1)[1]: v for k, v in c.items()
                  if k.startswith(kind + "_") and not k.endswith("_ok")}
        detail = ", ".join(f"{k} {v}" for k, v in sorted(others.items())) or "no problems"
        lines.append(f"  {kind:4} objects: {total}, ok {c.get(kind + '_ok', 0)}; {detail}")
    problems = report["problems"]
    sources = sorted({s for p in problems for s in p["source_ids"]})
    lines.append(f"problems: {len(problems)} objects, {len(sources)} sources")
    for p in problems:
        lines.append(f"  {p['status']:9} {p['kind']:4} {p['path']}  sources: {', '.join(p['source_ids'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=None,
                        help="database to check (default: data/archive.db, or $SOGICE_DATA_DIR)")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="folder that holds objects/ (default: the database's folder)")
    parser.add_argument("--json", type=Path, default=None, help="also write the full report as JSON here")
    parser.add_argument("--quiet", action="store_true", help="no progress lines on stderr")
    args = parser.parse_args(argv)
    db_path = args.db or paths.database_path()
    data_dir = args.data_dir or db_path.resolve().parent
    progress = None if args.quiet else (
        lambda n, total: print(f"  checked {n}/{total}", file=sys.stderr, flush=True))
    try:
        report = verify(db_path, data_dir, progress=progress)
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"verify_objects: {exc}", file=sys.stderr)
        return 2
    print(render(report))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
