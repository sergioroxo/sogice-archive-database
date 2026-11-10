"""Which review packets touch a test-set expectation (so a ruling can be checked against it).

    python scripts/review_testset.py --db data/scratch/copy.db --folder data/scratch/review-trial [--split dev]

Prints, per expectation, the open issues whose records sit near its anchor
(usage and same_speaker checks, within the check's window) or on its pages
(no_relation checks), and writes ``touching.json`` in the queue folder: the
issue ids, to pick a trial batch whose rulings the test set can score.
Reads the keys, so it is for the operator, never for the reviewer.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archive import db, paths, testset  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=paths.database_path())
    ap.add_argument("--folder", type=Path, default=paths.data_dir() / "review")
    ap.add_argument("--split", default="dev")
    args = ap.parse_args()
    conn = db.connect(args.db)
    data = testset.load_testset(paths.REPO_ROOT / "evaluation" / "testset_2026-11-10.json")
    keys = [json.loads(p.read_text()) for p in (args.folder / "keys").glob("*.json")]
    spans = {}   # issue -> [(passage_id, start, end, page)]
    for key in keys:
        for info in key["labels"].values():
            if key["table"] == "usage":
                rows = conn.execute("SELECT u.passage_id, u.char_start, u.char_end, p.page_start, p.text FROM usage u"
                                    " JOIN passage p ON p.id = u.passage_id WHERE u.id = ?", (info["id"],)).fetchall()
            else:
                rows = conn.execute("""SELECT u.passage_id, u.char_start, u.char_end, p.page_start, p.text
                                       FROM relation_usage ru JOIN usage u ON u.id = ru.usage_id
                                       JOIN passage p ON p.id = u.passage_id WHERE ru.relation_id = ?""",
                                    (info["id"],)).fetchall()
            for pid, s, e, page_start, text in rows:
                page = page_start + text.count("\f", 0, s) if page_start is not None else None
                spans.setdefault(key["issue"], []).append((pid, s, e, page, key["table"]))
    touching: dict[str, list[str]] = {}
    for entry in data["entries"]:
        if entry["split"] != args.split:
            continue
        view = testset.entry_view(conn, entry)
        for exp in entry.get("expect", []):
            hits = set()
            if exp["check"] in ("usage", "same_speaker"):
                window = exp.get("window", testset.DEFAULT_WINDOW if exp["check"] == "usage" else 0)
                anchors = [exp["anchor"]] if exp["check"] == "usage" else exp["anchors"]
                for loc in (l for a in anchors for l in view.find(a)):
                    for iid, rows in spans.items():
                        if any(pid == loc.passage_id and s < loc.end + window and e > loc.start - window
                               for pid, s, e, _page, table in rows if table == "usage"):
                            hits.add(iid)
            elif exp["check"] == "no_relation":
                lo, hi = exp["pages"]
                for iid, rows in spans.items():
                    if any(table == "relation" and page is not None and lo <= page <= hi
                           and any(pid == p["id"] for p in view.passages) for pid, _s, _e, page, table in rows):
                        hits.add(iid)
            if hits:
                touching[exp["id"]] = sorted(hits)
    for exp_id, ids in touching.items():
        print(f"{exp_id}: {len(ids)}")
    all_ids = sorted({i for ids in touching.values() for i in ids})
    (args.folder / "touching.json").write_text(json.dumps({"by_expectation": touching, "issues": all_ids}, indent=1))
    print(f"{len(all_ids)} issues touch {len(touching)} expectations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
