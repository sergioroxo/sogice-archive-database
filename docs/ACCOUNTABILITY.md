# Accountability: what the AI did, how sure we are, what it found hard

This archive is about people's testimony, including survivors'. A quote
credited to the wrong person, or a stance read the wrong way, can misrepresent
someone. There is no human gate before publication, so the archive has to say
plainly, for every record, how it came about and how far it can be trusted,
and it has to keep a public account of what the models got wrong or found
hard. This page explains how.

## What every record carries

| Question | Where it is answered |
|---|---|
| Which words is this based on? | the usage: a verbatim quote located by software in a preserved copy (passage, offsets, page or time) |
| Who made the record? | a `propose` decision: actor (model tag or researcher), run, prompt version, quote, reason |
| Which models read the source, and when? | the assessment: `readers` (model, run, prompt, time) |
| Which of them proposed this record? | the assessment: `proposed_by` |
| Did they agree? | the assessment: `confidence`, with the fields they differed on |
| What happened since? | the decision chain (hash-linked, append-only): merges, disputes, audits, rulings by rule (`rule:relations/v1`, with the rule's name) or by the reviewer, researcher corrections, undos |
| Can it be undone? | yes: every change is a decision with the previous state; `undo` reverses it |

## Confidence levels

| Level | Meaning | Shown publicly as |
|---|---|---|
| `two_readers_agree` | two different models read the source independently and recorded the same statement with the same speaker and no opposite stance | "Two independent readers agree" |
| `one_reader` | both models read the passage; only one recorded this | "Found by one reader" |
| `readers_disagree` | both recorded it, with different speakers or opposite stances; both records are *disputed* and wait for review | not published as a finding; listed as "under review" |
| `single_reading` | only one model has read the source so far | "Read by one model" |
| `confirmed` (review state) | a reviewer (Claude, `/review-queue`) ruled on a disputed or held record, on a quote from the source | "Reviewed" (with the reviewer's reason) |
| `confirmed` by rule | a software rule read a stated role or an amendment's author from the words of the source (`archive/rules.py`) | "Stated role" / "Stated authorship" (with the line it rests on) |
| `researcher_verified` (evidence state) | Sérgio checked it | "Checked by the researcher" |

Agreement is not proof. The test set showed both models making the *same*
mistakes in the same places (see below), so two readers agreeing raises
confidence but never turns a record into a verified fact. Every record stays
an attributed statement ("Source X states…"), never a plain fact.

## The difficulties register

`python -m archive reconcile --report` writes `data/reports/difficulties-*.md`:
how many statements fall in each confidence level, where the readers
disagreed (by field, with examples), what only one reader found, and per
model the unlocated quotes, cut-off sections and gap passes. Each release
will carry this register with the dataset, so readers of the archive can see
the limits of the method next to its results.

## What the failures have taught us about working with the models

From the Phase 3 test set (10 November 2026; numbers in `docs/MODELS.md`):

1. **Models leave things out more than they invent.** Quotes are located by
   software, so a model cannot put words in a source. What goes wrong is
   omission (a statement not recorded) and misattribution (the right words
   credited to the wrong voice, or the wrong stance).
2. **Given the chance, a model stops early.** With one open list per section
   the models sometimes returned one or two items for a whole page. Making
   the answer structural (one required list per passage) fixed it; telling
   them in words did not.
3. **More rules can make a model worse.** Prompt read-v2 added sensible
   attribution rules and the 12B then quoted less. Every prompt change is
   tested against the test set before use, and old prompts are kept so runs
   stay comparable.
4. **Size is not the same as quality.** A 35B mixture-of-experts model did
   about as badly as the 12B; the dense 27B and 31B models did far better.
5. **Two models share blind spots.** Of 16 failing expectations, 9 were
   failed by both models in the same way. A second reader catches what one
   model missed (5 of the 7 model-specific failures were omissions the other
   model found) but not what both get wrong.
6. **The hard cases are about voices, not words.** Both models struggled
   with: an interviewer's question credited to the interviewee; a quotation
   inside a quotation; captions without speaker labels; a figure relayed by
   a news outlet; a list of people heard turned into affiliations; and
   stance toward a ban confused with stance toward the practice. These are
   now explicit rules (prompt read-v4) and test cases, and they are where
   Sérgio's weekly sample should look first.
7. **Judging must be blind.** Model results were compared with the A/B labels
   shuffled, judged by an agent that did not know which model was which.
8. **Measure, don't trust.** The located share, the passages with no quote,
   the gap passes, cut-off sections and disagreements are all counted by
   software, not reported by the model about itself.

From the first two-reader run (read-v4, 10 November evening):

9. **A second reader cuts both ways.** Where the readers clash, a wrong
   reading is held back, but so is the right one (the Stop the Ban stance:
   nothing is published, which is the safe failure). Where only one reader
   records something, its misses are filled, and its errors come in too.
10. **Relations are the weak point.** The readers agree on few relations
    (81 of 280; most of the agreed ones are stated roles), and every failing
    relation in the combined reading came from one reader alone. A relation
    links two people or bodies, so a wrong one says more than a wrong quote.
11. **A rule written in words is followed by one model and not the other.**
    read-v4 says stance is toward the practice, never toward a ban; the 31B
    mostly follows it, Qwen often marks support for a ban as "endorses".
    Lesson 2 again: what matters has to be built into the form of the answer,
    not only the wording.
12. **The software that checks the models needs checking too.** The first
    reconciliation compared the 31B with an old 12B reading that had been
    replaced, and changed records it should have left alone. It was caught
    because every statement's confidence is counted (47% showed as never
    reconciled) and every change is a decision: the fix undid each wrong
    change with a recorded `undo`, and the register now includes it.

From the first review-queue trial (61 packets, 10 November; `docs/REVIEW.md`):

13. **A disagreement is often two mistakes.** In 9 of 26 disputed statements
    both readers were wrong, so the queue must allow "neither" and a
    correction, not only a choice between them.
14. **One reader's relation is a weak claim.** Of 28 held relations the
    reviewer rejected 15. Holding them was right.
15. **No reader is the reference.** The reviewer, blind to the models,
    chose each one about equally often.
16. **Review finds rules.** Most rulings fell into a few patterns (role lines,
    relations to things that are not entities, stance toward a ban,
    unlabelled captions). Turning those into rules that software applies is
    what makes the method scale; the queue is for what needs judgement.

From turning the trial's patterns into rules (10 November, night):

17. **A rule is checked against the rulings it came from.** The relation
    rules were run on the same readings the reviewer had seen: they never
    contradicted a ruling, reached the same result in 32 of 42 records, and
    left 4 to the queue on purpose (they need judgement). The queue's
    consequential relations fell from 98 to 39.
18. **A test set can disagree with a rule.** The test set forbids any
    authorship relation on pp. 42–43 of the French report; the adopted
    amendment rule makes "Amendement CL27 de M. Gérard" authorship. Neither
    is changed to fit the other: the conflict is written down and Sérgio
    decides.
19. **Re-running must change nothing.** The first version of the rules
    passed every test, but a second `reconcile` brought back copies a rule
    had folded away. It was found by running everything twice and checking
    that the second run decided nothing new; reconcile now leaves records
    ruled on by a rule, a reviewer or Sérgio as they are, with the
    assessment they had.

From testing read-v5 (11 November):

20. **A fix can work and still lose.** Asking for the position on a ban
    corrected about a hundred of Qwen's stances and halved the stance
    disputes, yet read-v5 failed more checks than read-v4: the new wording
    also moved things it was not aimed at. A prompt is judged on the whole
    dev set, not on the failure it was written for.
21. **Software that overrides a model needs limits.** Setting the stance
    from the ban position was right for ban supporters and opponents, and
    wrong for a quote that only describes a bill and for a law that is not
    a ban. The rule now replaces only the stances it was made for, and each
    run records which version of the rule wrote its stances.
22. **Bad metadata becomes bad readings.** A catalogue title ("r4501")
    shown in the header was taken as the report's name and became a
    speaker 116 times. What the readers are told about a source is part of
    the method and needs the same care as the prompt.

## The review queue

Disputed statements and relations, and relations only one reader proposed
(held from publication since 10 November), go to a review queue worked by a
Claude Code session with a saved prompt (`/review-queue`). The reviewer sees
the source text and both readings with the model names hidden, rules on the
words of the source, and may say it cannot tell. Every ruling is a decision
under the reviewer's name that can be undone; low-confidence rulings change
nothing and go to Sérgio. See `docs/REVIEW.md`.

## What stays with the researcher

Sérgio samples 30–50 AI decisions a week, including the reviewer's rulings,
and has the final say on method changes. He works the packets the reviewer
sends him, and the hard cases above are where his sample looks first. A researcher's decision is never undone by a
later automatic step.
