# The review queue

After the two readers and `reconcile`, some records cannot be published on
the models' word alone. They wait in a review queue, which a Claude Code
session works through with a saved prompt, and Sérgio samples. Code:
`archive/review.py`. Prompt: `prompts/review-v2.md`. Command:
`/review-queue` (`.claude/skills/review-queue/SKILL.md`).

## What waits for review

| Kind | What it is | State while waiting |
|---|---|---|
| statement dispute | both readers recorded the same words with a different speaker or opposite stance | both copies `disputed`; neither published |
| relation dispute | the same two names with a different relation type | both copies `disputed` |
| relation held | a relation only one reader proposed (decided 10 Nov 2026) | `proposed` but held: not published until a reviewer confirms it |

Statements found by one reader only are published, labelled "found by one
reader"; they are not in the queue. Sérgio's weekly sample covers them.

## The loop

1. `python -m archive review prepare` writes one packet per open issue to
   `data/review/open/`. A packet holds the source text around the quotes
   (⟦ ⟧ marks them; pages and times kept), the readings labelled A and B in
   a random order with model names hidden, what differs, who speaks
   elsewhere in the source (statements both readers agreed on), and the
   names and terms the archive already knows in the passage (entity
   registry, lexicon definitions). File names start with a priority:
   1 consequential relations (employment, affiliation, funding, operation,
   co-signing, training), 2 statement disputes, 3 other relations.
   Which record and model is A or B is in `data/review/keys/`; the reviewer
   never opens it.
2. In Claude Code, `/review-queue 40` takes the next 40 packets. For each,
   the reviewer writes `data/review/verdicts/<issue>.json`: A, B, neither
   (optionally with the corrected field), or needs_researcher; the words in
   the source that decide it; a reason; a confidence; a pattern tag.
3. `python -m archive review apply` (the command runs it) checks each
   verdict and records it:
   - the deciding quote must be found in the source text, or nothing is
     applied;
   - the records must still be as the packet showed them (not re-read or
     ruled on since);
   - the chosen reading becomes `confirmed`, the other `rejected`; with a
     correction, reading A is edited and confirmed; low confidence or
     needs_researcher changes nothing and the packet goes to
     `data/review/researcher/`.

   Every change is a decision under the reviewer's model name
   (`config/models.yaml`, `review.model`) with the review prompt's version
   and hash, the quote and the reason. A reviewer never sets
   `researcher_verified`.
4. The reviewer writes `data/review/patterns-<date>.md`: recurring mistakes
   with counts and examples, and proposed rule changes. Changes are tested on
   the test set before use; Sérgio decides.
5. Sérgio works `data/review/researcher/` and samples the reviewer's rulings
   with the rest of his weekly sample.

`reconcile` never changes a record a reviewer or the researcher has ruled
on. `python -m archive review undo --run RUN_ID` reverses a whole review run
(each change gets an `undo` decision); the issues come back at the next
`reconcile` and `prepare`. `review status` counts what is open, waiting,
done and sent to the researcher. The difficulties register lists the
rulings and the relations still held.

## Why a reviewer sees what it sees

- **Blind labels.** A and B are shuffled per packet so a ruling cannot lean
  on which model is thought to be better.
- **The text decides.** Context (speakers, known names, definitions) helps
  tell who is who; the ruling must rest on a quote from the source itself,
  and software checks that the quote is there.
- **The lexicon as context, not as evidence.** Definitions and names come
  from the seed vocabularies and the registry; they say what a word means
  and who someone is, never what this source says.
- **"I can't tell" is allowed.** Low confidence goes to Sérgio unapplied.

## Checking the reviewer

`scripts/review_testset.py` lists the packets that touch a test-set
expectation, so a review run can be scored: apply it to a copy of the
database and run `testset score` on the copy.

### First trial (10 November 2026, dev split, prompt review-v1)

The dev split gave 446 packets: 228 statement disputes, 19 relation
disputes, 199 held relations. A fresh Claude Code session, blind to the
test set, to the docs on these cases and to which model wrote which
reading, worked 61 of them with `/review-queue`: the 31 that touch a
test-set expectation and the first 30 of the rest in queue order. It took
11 minutes. Everything ran on a copy of the database.

| Kind | Packets | Ruling |
|---|---:|---|
| statement dispute | 26 | 31B's reading 10, Qwen's 7, both wrong and corrected 9 |
| relation dispute | 7 | 31B's reading 6, to the researcher 1 |
| relation held | 28 | confirmed 11, corrected 2, rejected 15 |

- **Test set:** automatic failures went from 3 to 2. The Stop the Ban stance
  (31B right, Qwen wrong, both disputed) is now settled the right way. No
  touched expectation got worse. The Davidson host check still fails: the
  reviewer set the speaker of the host's story to "unknown" (the captions
  have no labels), which is safe but not what the test set expects.
- **Holding single-reader relations was right:** more than half of those
  reviewed (15 of 28) were rejected.
- **A dispute is not always a choice between two:** in 9 of 26 statement
  disputes both readings were wrong.
- **Neither model is "the right one":** where the reviewer chose a reading,
  it chose each model about equally (17 and 17).
- **Confidence:** 43 high, 17 medium, 1 low; one packet went to Sérgio (a
  lawyer listed under an association in the list of people heard: the rules
  do not say whether a profession under a heading states a relation).
- **Fixed after the trial:** quotes from the context before or after the
  passage are now accepted (3 verdicts were first refused); the context is
  marked clearly; a relation can be reversed (`"reverse": true`; amendments
  "de M. X" were recorded the wrong way round); a confirmed relation
  identical to one already confirmed in the source is folded into it. The
  prompt is now review-v2.

The reviewer's proposals, for Sérgio to decide and the test set to check
(its patterns note is in the trial folder, not committed):

1. Role lines by rule, not by model: "Name, role, Organisation", or a
   heading naming an organisation followed by "Name, role", gives
   employed_by, stated by the publisher; a bare profession states nothing.
2. Software checks that both ends of a relation are names in the unit and
   entities (not a profession, title or a transcript's speaker label).
3. Reading rule: "Amendement CL34 de M. X" means the amendment is
   authored_by X, not co-signed.
4. Stance: a separate field for the speaker's position on the ban, mapped to
   stance by software; criticising LGBT groups or affirming therapists is
   not "criticizes".
5. Unlabelled captions: "host" and "guest" are not names; a question the
   host reads out belongs to whoever sent it; a speaker map per source from
   Sérgio for videos without labels.
6. Reconcile: two names for the same speaker (by the entity registry) are
   one speaker; an intermediary difference that means nothing (empty against
   the site that hosts the record) is not a disagreement.
7. Reference-list entries are `cites` relations, not statements.

Scale: the dev split is 0.36 M characters; the English corpus is 109 M. At
this rate the full queue would be far beyond what review sessions can work
through, so these proposals matter more than the queue itself: each one
turns a recurring mistake into a rule, and the queue is left with the cases
that need judgement.

```bash
python -m archive review prepare                       # all reconciled sources
python -m archive review prepare --testset --split dev
python -m archive review status
python -m archive review apply                         # after /review-queue wrote verdicts
python -m archive review undo --run run_…              # reverse one review run
```
