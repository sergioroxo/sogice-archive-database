---
name: review-queue
description: Work through the archive's review queue (data/review/open) — rule on statements the two readers disagreed on and relations only one reader proposed, write verdicts, apply them as recorded decisions, and note recurring patterns. Use when Sérgio asks to review the queue.
argument-hint: "[number of packets, default 20]"
disable-model-invocation: true
---

# Review queue

You are the reviewer described in `prompts/review-v2.md`. Read that file in
full first, then the reading prompt it names (`prompts/read-v4.md`). Follow
them exactly.

Number of packets this time: $ARGUMENTS (if empty, 20).

1. Check the queue:
   `.venv/bin/python -m archive review status`
   If nothing is open, run `.venv/bin/python -m archive review prepare` (all
   reconciled sources) and check again. If still nothing, say so and stop.
2. Take the first N packets in `data/review/open/`, sorted by file name (the
   leading number is the priority). Skip any that already have a file in
   `data/review/verdicts/`.
3. For each packet: read it, decide on the source text alone, and write
   `data/review/verdicts/<issue>.json` in the format of `prompts/review-v2.md`.
   One packet at a time; do not let one packet's answer decide the next.
4. Apply them:
   `.venv/bin/python -m archive review apply`
   If a verdict is reported as not applied (for example, the quote was not
   found in the source text), fix that verdict file if the fix is clear, and
   run apply again. Otherwise leave it and report it.
5. Write `data/review/patterns-<today>.md` as `prompts/review-v2.md` says.
6. Report to Sérgio: how many packets, the verdict counts, the packets sent
   to him (`data/review/researcher/`), the main patterns and any proposed
   rule changes, and the review run id (so the run can be undone with
   `python -m archive review undo --run <id>`).

Never open `data/review/keys/`, never query the database about the records
under review, never edit packets, prompts or code during a review. Nothing
under `data/` is committed (public repository).
