"""Blind packets for judging the test set's `judged` expectations across two or more readings.

    python scripts/judge_packets.py --a data/archive.db --b data/scratch/other.db [more.db …] --out data/scratch/judging

For each judged expectation in the dev split, writes ``<id>.md`` with the
criterion, the source text it concerns (pages for paged entries) and each
reading's records (statements, claims, relations), labelled A, B, C… in a
random order per packet. Disputed and superseded records are not shown: a
reading is judged on what it would publish. The key (which database is A) goes to ``key.json``,
which the judge must not read. Verdicts go to ``verdicts.json`` as
``{id: {"A": {"verdict": "pass"|"fail", "reason": …}, "B": {…}, …}}``; then
``--split-verdicts`` turns them into one judgements file per database for
``testset score --judgements``. Packets hold source text: they stay under data/.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archive import db, paths, testset  # noqa: E402

PAGES = {"fr-p48-49-senses": (48, 49), "fr-p45-46-no-verdict": (45, 46), "fr-p63-64-judged": (63, 64)}


def _page(p, offset: int) -> int | None:
    return None if p["page_start"] is None else p["page_start"] + p["text"].count("\f", 0, offset)


def _in_pages(p, offset: int, pages) -> bool:
    page = _page(p, offset)
    return pages is None or (page is not None and pages[0] <= page <= pages[1])


def records(conn: sqlite3.Connection, entry: dict, pages) -> str:
    view = testset.entry_view(conn, entry)
    passages = {p["id"]: p for p in view.passages}
    lines = []
    statements = [u for u in view.usages if u["entity_id"] is None]
    statements.sort(key=lambda u: (u["passage_id"] or "~", u["char_start"] or 0))
    lines.append(f"Runs: {len([r for r in view.runs if r['role'] == 'reading'])} current reading run(s); "
                 f"{len(statements)} statements in the whole entry.")
    run_notes = [json.loads(r["inputs"]).get("skipped") for r in view.runs]
    if any(run_notes):
        lines.append(f"Left out by the reader: {run_notes}")
    lines += ["", "Statements (page | speaker | quoted party | intermediary | attribution | stance | quote):"]
    shown = 0
    for u in statements:
        p = passages.get(u["passage_id"])
        if p is None:
            if pages is None:
                lines.append(f"- UNLOCATED | {u['speaker']} | {u['stance']} | {u['quote'][:200]}")
            continue
        if not _in_pages(p, u["char_start"], pages):
            continue
        shown += 1
        lines.append(f"- p.{_page(p, u['char_start'])} | {u['speaker']} | {u['quoted_party'] or ''} | "
                     f"{u['intermediary'] or ''} | {u['attribution_mode']} | {u['stance']} | {u['quote']}")
    ids = {u["id"] for u in statements}
    lines += ["", "Claims (claimant | factual status | text):"]
    for c in view.claims:
        if pages is None or any(pages[0] <= x <= pages[1] for x in c["pages"]):
            lines.append(f"- {c['claimant_label']} | {c['factual_status']} | {c['text']}")
    lines += ["", "Relations (subject | type | object | stated by | layer):"]
    for r in view.relations:
        if pages is None or any(pages[0] <= x <= pages[1] for x in r["pages"]):
            lines.append(f"- {r['subject_label']} | {r['type']} | {r['object_label']} | {r['attribution']} | {r['layer']}")
    return "\n".join(lines)


def source_text(conn: sqlite3.Connection, entry: dict, pages) -> str:
    view = testset.entry_view(conn, entry)
    seen, out = set(), []
    for p in view.passages:
        if p["text_sha256"] in seen:
            continue
        seen.add(p["text_sha256"])
        if pages is not None:
            first, last = _page(p, 0), _page(p, len(p["text"]))
            if first is None or last < pages[0] or first > pages[1]:
                continue
        out.append(p["text"].replace("\f", "\n[page break]\n"))
    return "\n\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=Path, default=paths.database_path())
    ap.add_argument("--b", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, default=paths.data_dir() / "scratch" / "judging")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--split-verdicts", action="store_true", help="verdicts.json -> one judgements file per database")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.split_verdicts:
        key = json.loads((args.out / "key.json").read_text())
        verdicts = json.loads((args.out / "verdicts.json").read_text())
        per_db: dict[str, dict] = {}
        for exp_id, by_label in verdicts.items():
            for label, verdict in by_label.items():
                per_db.setdefault(key[exp_id][label], {})[exp_id] = verdict
        for name, judgements in per_db.items():
            path = args.out / f"judgements-{Path(name).stem}.json"
            path.write_text(json.dumps(judgements, indent=1, ensure_ascii=False), encoding="utf-8")
            print(path)
        return 0
    data = testset.load_testset(paths.REPO_ROOT / "evaluation" / "testset_2026-11-10.json")
    conns = {str(path): db.connect(path) for path in [args.a, *args.b]}
    rng = random.Random(args.seed)
    key = {}
    for entry in data["entries"]:
        if entry["split"] != "dev":
            continue
        for exp in entry.get("expect", []):
            if exp["check"] != "judged":
                continue
            pages = PAGES.get(exp["id"])
            order = list(conns)
            rng.shuffle(order)
            key[exp["id"]] = dict(zip("ABCDEFG", order))
            first = conns[order[0]]
            parts = [f"# {exp['id']} ({entry['key']})", "", "## Criterion", "", exp["criterion"], "",
                     f"## Source text{' (pages %d–%d)' % pages if pages else ''}", "",
                     source_text(first, entry, pages), ""]
            for label, name in zip("ABCDEFG", order):
                parts += [f"## Reading {label}", "", records(conns[name], entry, pages), ""]
            (args.out / f"{exp['id']}.md").write_text("\n".join(parts), encoding="utf-8")
    (args.out / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(f"{len(key)} packets in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
