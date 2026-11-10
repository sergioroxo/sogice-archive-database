# Local reading (Phase 3)

`python -m archive read` has the local reading model (see
`config/models.yaml`, role `reading`) read sources and store what it finds
as usages, claims, entities and relations. The model copies short quotes;
software finds them in the stored text. Code: `archive/read.py`. Prompts:
`prompts/read-v*.md` (a new wording gets a new file, so runs stay comparable;
the current one is `read-v4`).

## What the model is given

- Every passage of every readable copy of the source, in order, in sections
  of about 6,000 characters. A section never splits a passage.
- A header: URL, site, title, publisher, publication date if known, and the
  Wayback capture date marked as *not* a publication date. For a recording
  without speaker labels, also the speakers Sérgio has named
  (`python -m archive speakers SOURCE --set "Name=host"`, stored in
  `data/speakers/`; the map's hash goes into the run's inputs).
- The end of the previous section and its last speaker, as context only, so a
  speaker's turn carries across sections.
- Left out on purpose: merged duplicates (their keeper is read), copies
  flagged `challenge_page` or with a missing/unreadable file, passages
  repeated on 3+ sources (site furniture), and word-for-word repeats.

## What it returns

| List | Fields | Becomes |
|---|---|---|
| statements | unit, quote, speaker, quoted_party, intermediary, attribution, stance, ban_position (read-v5 on), claim | a usage; with `claim: true` also a claim (claimant = speaker, never ruled true or false) |
| entities | name, kind, unit, quote | a usage naming the entity; exact label or variant attaches to a known entity, otherwise a new proposed entity |
| relations | subject, type, object, stated_by, unit, quote | a relation from the closed vocabulary, only with a located quote |

From `read-v5` each statement also carries **ban_position**: the speaker's
position on a ban or law against change efforts (supports, opposes, none).
Software sets the stance from it: supporting a ban is `criticizes`, opposing
one is `endorses`. It replaces only a stance that reads as a stance toward
the ban, or none: endorses (for a supporter), criticizes (for an opponent),
not_applicable and unclear. A finer stance (mixed, minimizes, denies,
acknowledges) and the neutral describes and reports stay as the reader gave
them: describing a bill or reporting that bans exist is not criticizing the
practice. This is rule `ban/v2`, recorded in each run as `stance_rule`; the
first read-v5 runs used `ban/v1`, which also replaced describes and reports.
The decision records both the reader's stance and the rule. "unknown" is no
longer an allowed attribution, and "host" or "guest" as a speaker becomes
"interviewer" or "unknown".

## What read-v5 adds

From the first review trial (`docs/REVIEW.md`), adopted by Sérgio on
10 November 2026:

- the ban-position field above (the stance rule, written in words in
  read-v4, was followed by one reader and not the other);
- "criticizes" means criticizing change efforts: a speaker who defends them
  while criticizing LGBT groups, "ideology" or affirming therapists endorses;
- captions: "host" and "guest" are not names; a question the host reads out
  belongs to whoever sent it, with the host or channel as intermediary; the
  researcher's speaker list is used when there is one;
- reference lists, footnotes and "See …" citations are not statements: the
  document `cites` the publication, and the publication is `authored_by`
  its authors;
- "Amendement CL34 de M. X", "proposition de loi de Mme Y": the text is
  `authored_by` the person, never `co_signatory_of`;
- relation ends are names written in the unit, never a profession, title,
  office, description or speaker label; a profession on its own states no
  relation, even under an organisation's heading.

read-v5 did not test better than read-v4 on the dev set (`docs/MODELS.md`),
so read-v4 stays the default. `read-v6` keeps read-v5 and adds four
clarifications for what went wrong with it: a quote belongs to the speaker
introduced at the end of the unit before; a report's own text belongs to
the body or person it names as its author, never a file name or code; a list
of people heard gives no statements; and a law that orders, funds or
protects change efforts (such as a law ordering research into a "cure") is
not a ban.

## Rules after reading

After `reconcile`, software rules (`archive/rules.py`) decide the relations
that need no judgement: ends that are not names or not in the text are
rejected; role lines ("Name, role, Organisation", or "Name, role" under a
list heading) are confirmed as `employed_by`; a bare profession or a
transcript's speaker label is rejected; amendments are turned into
authorship. Each ruling is a decision under `rule:relations/v1` and can be
undone. What is left goes to the review queue. See `docs/REVIEW.md`.

## Checks software makes

- Each quote is located in the passages sent (`quote_locator`). A quote it
  cannot find is stored as a usage with `evidence_state = 'unlocated'` and
  no passage; it counts against the located share.
- A failed call is retried once (cut-off replies: see below). If a section
  still fails, nothing from that source is written, the run is marked failed
  and the batch continues.
- **One list per passage.** The reply holds a required list of statements for
  each passage (P1, P2, …), so the model must answer for every passage
  before it can close. With one list for the whole section (prompts read-v1,
  read-v2) it sometimes stopped after one or two items.
- **Gap pass:** a passage of 400+ characters with no located quote is sent
  once more on its own.
- **Cut-off replies:** a reply cut off at the token cap is first read again
  as two halves; a single passage that still runs over (a model stuck
  repeating itself) keeps the complete items before the cut. Repeated items
  are dropped.
- Each run records how many passages were sent and lists those with no
  quote. Such a passage may hold nothing relevant; long gaps are for review.

## Safeguards and time

| Risk | Guard |
|---|---|
| A call hangs | 10-minute timeout per call (`ollama.timeout_seconds`); retried once |
| A reply runs on | token cap per call (`num_predict`); see cut-off replies above |
| One document holds the batch | stopped past 3× its budget (budget: `seconds_per_1000_chars`, 15; at least 10 minutes), marked failed, batch continues |
| The process dies or hangs | each run records `progress` and `heartbeat_at` after every section; `read --status` lists runs in progress and exits 1 if one has had no progress for 20 minutes (usable as a scheduled alarm); the next batch marks such a run failed |

The test-set gate for time is seconds per 1,000 characters read, not a fixed
time per source.

## Two readers

`read --both` reads each batch with the `reading` model, then with the
`reading_second` model (one resident model at a time), then runs
`reconcile` on the batch. The two readings are layers: a model reading a
source again replaces only its own earlier reading. `reconcile` compares
only the two configured models' latest, not superseded readings (an old
reading by another model stays in the database for the record but takes no
part; anything an earlier reconciliation changed on it is undone with a
recorded `undo`). It pairs
statements whose quotes overlap, compares speaker and stance, and writes an
assessment per record (confidence, readers, proposed by, differences); see
`docs/ACCOUNTABILITY.md`. Agreeing copies are folded into one record (the
`reading` model's), disagreeing ones are both disputed. Since reconcile v3, a
role-only label ("Mme la rapporteure") counts as the one speaker the source
gives that role, two names of one registry entity are one speaker, a shared
role word alone ("X, rapporteure" and "Y, rapporteure") is not, and an
intermediary that is only the hosting site is not a difference. A record a
reviewer, the researcher or a rule has ruled on is left as it is, with the
assessment it had.

## Records and undo

Every stored record has a `propose` decision: the model tag, the run, the
prompt version and the quote. The run stores `source_id`, the prompt's
SHA-256, the sections, calls, gap calls, tokens and seconds. Reading a
source again (`--again`, or a new prompt) supersedes the earlier run's
usages, claims and relations: they stay in the database with
`review_state = 'superseded'` and one `supersede` decision per run.

## Commands

```bash
python -m archive read --testset --split dev           # the dev entries of the test set
python -m archive read --testset --entry fr-4501 --again
python -m archive read --source src_… --prompt read-v3
python -m archive read --testset --dry-run             # sections and the first prompt, no model call
python -m archive testset score --split dev
python -m archive read --status                        # runs in progress, stalled runs
python -m archive read --testset --both                # two readers, then reconcile and the rules
python -m archive reconcile --testset --split dev      # compare again (reconcile v3), then the relation rules
python -m archive reconcile --all --report-only        # the difficulties register
python -m archive speakers src_… --set "Jane Doe=host" --note "the host tells her own story at 12:00"
python -m archive review prepare                       # the review queue (docs/REVIEW.md)
python -m archive read --db data/scratch/copy.db --testset --model-role audit --again   # try the 31B on a copy
python scripts/probe_reader.py --chunk-chars 1600      # compare variants; writes nothing
```

`scripts/probe_reader.py` reads the dev entries with a prompt or section
size and reports anchor coverage (share of expected sentences with a located
quote nearby), located share and speed, without writing to the database.
For a full score of a variant, read into a copy of the database and score
that copy.

The model stays loaded for the length of a batch (`keep_alive` in the
config) and is unloaded when the batch ends: one resident model at a time.
