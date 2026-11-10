"""Compare reader variants on the dev test set without writing anything.

    python scripts/probe_reader.py --prompt read-v3 --chunk-chars 1600 [--entry KEY ...] [--drop TEXT]

Reads each dev entry's sources with the given prompt and section size, locates
the quotes in memory, and reports *anchor coverage*: the share of the test
set's usage anchors (expectations that want a usage) with a located quote
within 200 characters. Also: statements, located share, seconds per 1,000
characters. Holdout entries are never read. ``--drop`` removes a sentence from
the prompt for the probe only (to test a wording change before writing a new
prompt file).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archive import db, paths, read, testset  # noqa: E402
from archive.config import load_models_config  # noqa: E402
from archive.ollama import OllamaClient  # noqa: E402
from archive.quote_locator import locate_quote  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default=read.DEFAULT_PROMPT)
    ap.add_argument("--chunk-chars", type=int, default=read.DEFAULT_CHUNK_CHARS)
    ap.add_argument("--entry", action="append")
    ap.add_argument("--skip", action="append", default=[], help="entry keys to leave out")
    ap.add_argument("--drop", action="append", default=[], help="text to remove from the prompt")
    args = ap.parse_args()

    prompt = read.load_prompt(args.prompt)
    text = prompt.text
    for piece in args.drop:
        if piece not in text:
            raise SystemExit(f"--drop text not in prompt: {piece!r}")
        text = text.replace(piece, "")
    prompt = read.Prompt(args.prompt + ("+drop" if args.drop else ""), text)

    cfg = load_models_config()
    role = cfg.role("reading")
    client = OllamaClient(cfg.base_url, cfg.timeout_seconds)
    conn = db.connect(paths.database_path())
    data = testset.load_testset(paths.REPO_ROOT / "evaluation" / "testset_2026-11-10.json")
    reader = read.Reader(conn, client, role, prompt=prompt, chunk_chars=args.chunk_chars)

    totals = {"anchors": 0, "covered": 0, "statements": 0, "located": 0, "seconds": 0.0, "chars": 0}
    try:
        for entry in data["entries"]:
            if entry["split"] != "dev" or entry["key"] in args.skip or (args.entry and entry["key"] not in args.entry):
                continue
            sources = {read.keeper(conn, s) for s in testset.entry_sources(entry)}
            spans: list[tuple[str, int, int]] = []
            started = time.monotonic()
            chars = 0
            for sid in sorted(sources):
                units, _ = read.source_units(conn, sid, reader.furniture)
                chars += sum(len(u["text"]) for u in units)
                reading = reader.read_source(sid)
                for chunk in reading.chunks:
                    for s in read.statements_of(chunk.data):
                        totals["statements"] += 1
                        found = locate_quote(s.get("quote") or "", chunk.records, preferred_unit_id=s.get("unit"))
                        if found:
                            totals["located"] += 1
                            unit = next(r for r in chunk.records if r["unit_id"] == found["unit_id"])
                            spans.append((unit["passage_id"], found["start"], found["end"]))
            seconds = time.monotonic() - started
            view = testset.entry_view(conn, entry)
            anchors = [e for e in entry.get("expect", []) if e["check"] == "usage" and e.get("must", "some") != "none"]
            hit = 0
            for exp in anchors:
                window = exp.get("window", testset.DEFAULT_WINDOW)
                if any(pid == loc.passage_id and start < loc.end + window and end > loc.start - window
                       for loc in view.find(exp["anchor"]) for pid, start, end in spans):
                    hit += 1
            totals["anchors"] += len(anchors)
            totals["covered"] += hit
            totals["seconds"] += seconds
            totals["chars"] += chars
            print(f"{entry['key']:28s} anchors {hit}/{len(anchors)}  quotes {len(spans):4d}  "
                  f"{seconds:5.0f} s  {1000 * seconds / max(chars, 1):4.1f} s/1k", flush=True)
    finally:
        client.unload(role.model)
    print(json.dumps({"prompt": prompt.version, "chunk_chars": args.chunk_chars,
                      "anchor_coverage": round(totals["covered"] / max(totals["anchors"], 1), 3),
                      "located_share": round(totals["located"] / max(totals["statements"], 1), 3),
                      "seconds_per_1000_chars": round(1000 * totals["seconds"] / max(totals["chars"], 1), 2),
                      **totals}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
