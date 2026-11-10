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
| What happened since? | the decision chain (hash-linked, append-only): merges, disputes, audits, researcher corrections, undos |
| Can it be undone? | yes: every change is a decision with the previous state; `undo` reverses it |

## Confidence levels

| Level | Meaning | Shown publicly as |
|---|---|---|
| `two_readers_agree` | two different models read the source independently and recorded the same statement with the same speaker and no opposite stance | "Two independent readers agree" |
| `one_reader` | both models read the passage; only one recorded this | "Found by one reader" |
| `readers_disagree` | both recorded it, with different speakers or opposite stances; both records are *disputed* and wait for review | not published as a finding; listed as "under review" |
| `single_reading` | only one model has read the source so far | "Read by one model" |
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

## What stays with the researcher

Sérgio samples 30–50 AI decisions a week and has the final say on method
changes. Disputed records, single-reader records of consequential relations
(affiliation, funding, employment, training, co-signing) and the hard cases
above are the review queue. A researcher's decision is never undone by a
later automatic step.
