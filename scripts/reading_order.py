"""The reading order of one language: sources still to be read by both readers (docs/ORCHESTRATION.md, S3).

    python scripts/reading_order.py --db data/archive.db --language en --out data/reports/order-en.txt
    python scripts/reading_order.py --db data/archive.db --language en --first demo-set.txt --out …
    .venv/bin/python -m archive read --both --sources-file data/reports/order-en.txt --limit 500

No model; the database is opened read-only. A source is listed when

- it is not merged into another (merged copies are read with their keeper);
- it has text to read: the passages ``read`` would send (``read.source_units``: flagged versions, page
  furniture and repeated passages left out), and more than half of their characters are in ``--language``;
- the two readers in ``config/models.yaml`` (roles ``reading`` and ``reading_second``) have not both read
  it with ``--prompt`` as it is now (``read.already_read``: same version and same text). A source only
  one reader has read is listed; ``read --both`` skips the reader that is done;
- no reader that still has to read it has failed on it twice with this prompt version (retried once,
  then marked failed: such sources are counted under "failed twice" and wait for a person).

Order: the ids in ``--first`` (the demo set), in that file's order, then by exact term and entity matches
(``usage.found_by = 'exact_match'``, not rejected or superseded, in the passages read) per 1,000
characters, densest first, then the sources without matches; ties by source id. The output is one id
per line with a comment (characters, matches per 1,000), which ``--sources-file`` ignores.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from archive import read  # noqa: E402
from archive.__main__ import SelectionError, sources_from_file  # noqa: E402
from archive.config import load_models_config  # noqa: E402
from archive.match import furniture_hashes  # noqa: E402

READERS = ("reading", "reading_second")   # the roles whose models read every source (CLAUDE.md)
MAX_ATTEMPTS = 2                          # a failed source is retried once, then left failed (CLAUDE.md)


def connect_read_only(path: Path) -> sqlite3.Connection:
    path = Path(path).resolve()
    if not path.exists():
        raise SystemExit(f"no database at {path}")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def match_counts(conn: sqlite3.Connection) -> Counter:
    """Exact matches per passage (seed vocabulary and entity labels found by software)."""
    return Counter({row[0]: row[1] for row in conn.execute(
        "SELECT passage_id, COUNT(*) FROM usage WHERE found_by = 'exact_match' AND passage_id IS NOT NULL"
        " AND review_state NOT IN ('rejected', 'superseded') GROUP BY passage_id")})


def failed_readings(conn: sqlite3.Connection, prompt: read.Prompt) -> Counter:
    """Failed reading runs per (source, model) with this prompt version (stalled runs closed as failed count)."""
    return Counter({(row[0], row[1]): row[2] for row in conn.execute(
        "SELECT json_extract(inputs, '$.source_id'), model, COUNT(*) FROM run WHERE role = 'reading'"
        " AND outcome = 'failed' AND prompt_version = ? GROUP BY 1, 2", (prompt.version,))})


def candidates(conn: sqlite3.Connection, language: str, prompt: read.Prompt, models: list[str],
               log=lambda _: None) -> tuple[dict[str, dict], Counter]:
    """Every source that passes the filters, with its characters and matches; and why the others did not."""
    furniture = furniture_hashes(conn)
    matches = match_counts(conn)
    failures = failed_readings(conn, prompt)
    found, left_out = {}, Counter()
    keepers = [row[0] for row in conn.execute("SELECT id FROM source WHERE merged_into IS NULL ORDER BY id")]
    left_out["merged"] = conn.execute("SELECT COUNT(*) FROM source WHERE merged_into IS NOT NULL").fetchone()[0]
    for number, sid in enumerate(keepers, 1):
        if number % 1000 == 0:
            log(f"  {number}/{len(keepers)} sources looked at")
        units, _ = read.source_units(conn, sid, furniture)
        chars = sum(len(u["text"]) for u in units)
        if not chars:
            left_out["no text to read"] += 1
            continue
        in_language = sum(len(u["text"]) for u in units if u["language"] == language)
        if in_language * 2 <= chars:
            left_out["mostly another language"] += 1
            continue
        done = [read.already_read(conn, sid, prompt, model) for model in models]
        if all(done):
            left_out["read by both readers"] += 1
            continue
        if all(ok or failures[(sid, model)] >= MAX_ATTEMPTS for ok, model in zip(done, models)):
            left_out["failed twice"] += 1   # retried once, then marked failed (CLAUDE.md): listed in the run's report
            continue
        hits = sum(matches[u["passage_id"]] for u in units)
        found[sid] = {"chars": chars, "matches": hits, "density": hits * 1000 / chars}
    return found, left_out


def order(found: dict[str, dict], first: list[str]) -> list[str]:
    head = [sid for sid in dict.fromkeys(first) if sid in found]
    taken = set(head)
    rest = sorted((sid for sid in found if sid not in taken),
                  key=lambda sid: (-found[sid]["density"], sid))
    return head + rest


def first_ids(conn: sqlite3.Connection, path: Path) -> list[str]:
    """The demo set, as keepers, in file order."""
    try:
        ids = sources_from_file(path)
    except SelectionError as exc:
        raise SystemExit(str(exc))
    kept, unknown = [], []
    for sid in ids:
        try:
            kept.append(read.keeper(conn, sid))
        except read.ReadError:
            unknown.append(sid)
    if unknown:
        raise SystemExit(f"--first: {len(unknown)} unknown source id(s): {', '.join(unknown[:5])}")
    return list(dict.fromkeys(kept))


def write_order(path: Path, ids: list[str], found: dict[str, dict], head: set, header: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {line}" for line in header]
    for sid in ids:
        info = found[sid]
        lines.append(f"{sid}  # {info['chars']} chars, {info['density']:.2f} matches per 1,000"
                     + (", first" if sid in head else ""))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, required=True, help="the archive database (opened read-only)")
    parser.add_argument("--language", required=True, help="passage language code, e.g. en, pt, es")
    parser.add_argument("--out", type=Path, required=True, help="the order file to write (one source id per line)")
    parser.add_argument("--first", type=Path, help="source ids to put first (the demo set), one per line")
    parser.add_argument("--prompt", default=read.DEFAULT_PROMPT, help="reading prompt version (default: %(default)s)")
    args = parser.parse_args(argv)

    cfg = load_models_config()
    models = list(dict.fromkeys(cfg.model_for(role) for role in READERS))
    prompt = read.load_prompt(args.prompt)
    conn = connect_read_only(args.db)
    try:
        first = first_ids(conn, args.first) if args.first else []
        started = time.monotonic()
        found, left_out = candidates(conn, args.language, prompt, models,
                                     log=lambda line: print(line, file=sys.stderr, flush=True))
    finally:
        conn.close()
    ids = order(found, first)
    head = set(ids) & set(first)
    chars = sum(found[sid]["chars"] for sid in ids)
    summary = {
        "language": args.language, "prompt": prompt.version, "readers": models,
        "sources": len(ids), "characters": chars,
        "first": {"given": len(first), "listed": len(head), "not listed": len(first) - len(head)},
        "with matches": sum(1 for sid in ids if found[sid]["matches"]),
        "left out": dict(sorted(left_out.items())),
        "seconds": round(time.monotonic() - started, 1),
    }
    header = [f"reading order: language {args.language}, prompt {prompt.version} ({prompt.sha256[:12]}), "
              f"readers {', '.join(models)}",
              f"written {time.strftime('%Y-%m-%d %H:%M:%S')}: {len(ids)} sources, {chars} characters"
              + (f"; {len(head)} from {args.first.name} first" if args.first else ""),
              "order: --first ids, then exact matches per 1,000 characters (densest first), then the rest"]
    write_order(args.out, ids, found, head, header)
    for key, value in summary.items():
        print(f"{key}: {value}")
    print(f"order: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
