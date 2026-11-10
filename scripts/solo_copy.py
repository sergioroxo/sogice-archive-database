"""A copy of the database with one reader's latest readings alone, for scoring a model by itself.

    python scripts/solo_copy.py --db data/scratch/read-v5.db --model MODEL --prompt read-v5 --rules \
        --out data/scratch/solo-v5-first.db      # MODEL: a model tag from config/models.yaml

In the copy, the named model's latest reading runs with this prompt keep all their records as proposed
(what the reader alone would say), the other readers' runs with this prompt are marked rejected, and no
assessment is current (nothing is held or compared). With --rules, the rules' earlier decisions are
undone in the copy and the rules are applied to this reader's records alone. Only for scoring: never
point the archive at it.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rules", action="store_true", help="apply the relation rules to this reader alone")
    args = ap.parse_args()
    if args.out.resolve() == args.db.resolve() or args.out.name == "archive.db":
        print("write the copy somewhere else (data/scratch/…)", file=sys.stderr)
        return 2
    src, dst = sqlite3.connect(args.db), sqlite3.connect(args.out)
    src.backup(dst)
    src.close()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.rules:
        from archive import review
        dst.row_factory = sqlite3.Row
        for (rule_run,) in dst.execute("SELECT id FROM run WHERE json_extract(inputs, '$.job') = 'rules' "
                                       "ORDER BY started_at DESC").fetchall():
            review.undo(dst, rule_run, reason="solo copy for scoring: the rules are applied again to one reader",
                        actor_kind="model", actor_id="script:solo_copy")
        dst.row_factory = None
    runs = dst.execute("""SELECT id, model FROM run r WHERE role = 'reading' AND outcome = 'succeeded'
                          AND prompt_version = ? AND NOT EXISTS (SELECT 1 FROM run later WHERE later.role = 'reading'
                          AND later.outcome = 'succeeded' AND later.model = r.model AND later.started_at > r.started_at
                          AND json_extract(later.inputs, '$.source_id') = json_extract(r.inputs, '$.source_id'))""",
                       (args.prompt,)).fetchall()
    with dst:
        for run_id, model in runs:
            state = "proposed" if model == args.model else "rejected"
            usages = "SELECT id FROM usage WHERE run_id = ?"
            dst.execute(f"UPDATE claim SET review_state = ? WHERE id IN (SELECT claim_id FROM claim_usage "
                        f"WHERE usage_id IN ({usages}))", (state, run_id))
            dst.execute(f"UPDATE relation SET review_state = ? WHERE id IN (SELECT relation_id FROM relation_usage "
                        f"WHERE usage_id IN ({usages}))", (state, run_id))
            dst.execute("UPDATE usage SET review_state = ? WHERE run_id = ?", (state, run_id))
        dst.execute("UPDATE assessment SET current = 0")
    if args.rules:
        from archive import rules
        sources = [r[0] for r in dst.execute(
            "SELECT DISTINCT json_extract(inputs, '$.source_id') FROM run WHERE role = 'reading' AND model = ? "
            "AND prompt_version = ?", (args.model, args.prompt))]
        print("rules:", rules.apply_rules(dst, sources))
    print(f"{args.out}: {sum(m == args.model for _, m in runs)} runs by {args.model} kept, "
          f"{sum(m != args.model for _, m in runs)} other runs hidden")
    dst.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
