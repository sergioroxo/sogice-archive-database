# Phase 3 test set

The test set is how we know whether local reading is good enough. It is the
file `evaluation/testset_2026-11-10.json`: 20 entries, each a source (one
entry holds two copies of the same video) with the behaviour we expect from
the reader: 112 expectations in all. It holds short anchor phrases only (at
most 12 words, enforced by a test), never source texts.

The Transfer-package cases were written from the package's section 11. The
other expectations were drafted by Claude agents reading the stored text,
then reviewed; every anchor is checked against the text by `score`. Sérgio
can change any of them: edit the file, run `score`, commit.

## The 20 entries

| Entry | Why it is there |
|---|---|
| fr-4501 | French report 4501: Transfer cases pp. 42, 42–43, 45–46, 48–49, 63–64; non-English; speaker turns; page locators |
| exodus-closure | Exodus closure press release (package PDF of the Wayback capture): "transforming" targets churches; umbrella closure vs affiliates |
| harvest-covenant | Harvest USA sample covenants: two samples kept apart; template wording, not testimony |
| harvest-women-curriculum | Curriculum title attests wording only; repeated site furniture |
| harvest-support-groups | Guidance with privacy exceptions; repeated site furniture |
| latimes-exodus | News relaying Exodus voices (holdout) |
| eternity-exodus | News with ministries on both sides of the closure |
| france24-torrents | English news about Torrents de Vie (links to fr-4501) |
| cohen-cnn | Duplicate: one video queued under two URLs; captions with timecodes |
| publico-pt | Portuguese reportage (holdout) |
| mirrorweb-challenge | Deliberate failure: a bot-check page stored instead of a briefing |
| bacp-mou | UK Memorandum of Understanding (PDF): joint statement, signatories, exemptions |
| christianconcern-exgay | Ban opponent quoting an ex-gay man; scare quotes |
| beyondexgay-rick | Survivor narrative (holdout) |
| exodus-moving-on | Exodus ministry page, 2008 Wayback capture |
| apa-consumers-report | Academic article: APA abstract page merged by DOI with a full-text PDF copy (holdout) |
| davidson-therapeutic-choice | Long interview with a practitioner; manual captions |
| genspect-irish-mou | Critic of the Irish MoU; stance nuance (holdout) |
| stoptheban-testimonies | Testimonies relayed by a campaign site |
| trevor-still-happening | Survey report (PDF): numbers stay attributed |

Five entries are **holdout**: they are scored, but whoever tunes prompts must
not look at their expectations or results (`score --split dev` for tuning).

## Kinds of expectation

| Check | Passes when |
|---|---|
| `usage` | a model usage near the anchor fits `where` (speaker, quoted party, intermediary, stance, attribution, concept, date); `where` may be a list of acceptable alternatives; with `"must": "none"` no usage may fit |
| `same_speaker` | usages at two anchors share a speaker (a turn across a page break) |
| `claims_attributed` | claims on those pages have a claimant and are not ruled corroborated/refuted |
| `no_relation` | no asserted relation of those types draws on those pages |
| `separate` | no claim or relation draws on two page groups (the two covenant samples) |
| `furniture` | usages in repeated page blocks are not counted as body text |
| `no_model_usages` | the reader found nothing (the bot-check page) |
| `same_text` | two copies share their text and only one was read |
| `language`, `pages`, `timecodes` | the preserved text has the right language, page or time locators |
| `judged` | a Claude agent reads the results and records a verdict |

Each expectation ends as pass, fail, pending (not read yet), judge (verdict
needed) or error (the test set itself is wrong: fix the anchor, not the model).

## The gate (from the plan)

90% of model quotes located; speaker and stance filled on 90% of located
usages (the plan says "filled"; 90% is our working figure); every expectation
passes; reading takes at most 15 seconds per 1,000 characters. (Until 10 Nov
the limit was 5 minutes per source; a 150-page report cannot meet that, and
what matters is whether the corpus fits the schedule: about 109 million
English characters at ~13 s per 1,000 (the 31B) is roughly 390 model hours. Hangs are
caught separately: see `docs/READING.md`, Safeguards.)

## Holdout result (11 November 2026)

The five holdout entries (24 checks, about 104,000 characters) had never
been read or looked at. They were read once with the method chosen on the
dev set: the 31B and Qwen 3.8 27B, prompt read-v4, then reconcile and the
relation rules, in the archive itself. The one judged check went to a blind
judge, with the three readings shuffled.

| Reading | Holdout failed (24) | Dev failed (88) |
|---|---:|---:|
| Both, reconciled (what would be published) | 9 | 5 |
| 31B alone, with the rules | 7 | 8 |
| Qwen 3.8 27B alone, with the rules | 6 | 10 |

**Gate not passed.**

- Quotes located: 99.7%. Speaker filled: 95%. Stance filled: 100%.
- Reading time over the five sources: 12.3 s per 1,000 characters for the
  31B and 11.1 s for Qwen.
- Three sources each took more than 15 s per 1,000 characters. The
  shortest page (1,479 characters) took the 31B 24 s per 1,000: each call
  repeats the prompt, so short pages cost the most.

What the nine failures are:

- **Copies of a merged work are not read (2 checks).** The APA article's
  abstract page holds both anchor sentences. It was merged into the
  full-text copy, and reading takes only the keeper's own copies, although
  the plan says every readable copy is read. Across the archive, 22 sources
  are merged into others; most only repeat their keeper's text, but 8 hold
  text the keeper lacks (11 passages, about 9,400 characters). Fixed the
  same day: their copies are read with the keeper (see below).
- **The readers are not told who wrote a text (1 automatic check and the
  judged one).** The Genspect essay is by Jaco van Zyl. Both readers
  credited its critique of the MoU to Genspect, the publisher. One reader
  took the author's name from the page for the first half of the essay;
  neither did for its conclusion. The source header gives title and
  publisher, never the author. The same gap causes most of the 254 disputed
  statements:
  - about 100: the APA journal's name against "authors";
  - about 40: van Zyl against Genspect.
- **Disputed, waiting for review (2).** In the Portuguese report, each
  reader alone passes both checks (Pedro recounting the priest; Maria
  relaying her psychologist). But they named different speakers, so the
  combined reading holds those statements for review.
- **The test set and the prompt disagree (1).** Rick quotes his counselor.
  Both readers made the counselor the speaker, with Rick as intermediary,
  as the prompt's rule for quotations inside quotations says. The test set
  forbids it. This is the same conflict as the French p. 42 Torrents de Vie
  check, and it waits for Sérgio.
- **Reader mistakes (2).**
  - The news agency Lusa recorded as the speaker of the mother's denial,
    with the mother as quoted party (31B; Qwen recorded nothing there).
  - Rick's "sub-standard if not bogus" marked "mixed" (31B). Qwen said
    "criticizes", but reconcile does not count mixed against criticizes as
    a disagreement, so the first reader's stance stood.

At this rate, disputes alone would swamp the review queue. The holdout
produced 254 disputed statements and 60 held relations, and the English
corpus is about 1,000 times larger. Most of these disputes come from the
missing author, so fixing that matters for the queue too.

This holdout has now been seen. Fixes made because of it must be tested on
the dev set, and the next gate needs new holdout entries.

## The two fixes, tested on dev (11 November 2026)

- **Merged copies are read with their keeper.** No dev source has merged
  copies, so dev cannot show this fix; a unit test does.
- **Readers are told the author** (`docs/AUTHORS.md`).

The test was run on a copy of the archive with authors set. That copy also
holds the titles run's repaired titles, so the two cannot be told apart
here. Five dev sources' headers changed:

- two gained an author: France 24 and Christian Concern;
- three gained a repaired title and publisher: the French report, the
  Exodus closure and the Harvest covenants.

Both readers re-read these five, with read-v4, reconcile and the rules.

| Dev, 88 checks, automatic | Before (read-v4 + rules) | After |
|---|---:|---:|
| Both, reconciled | 3 | 3 |
| 31B alone | 4 | 3 |
| Qwen 3.8 27B alone | 7 | 7 |

The three combined failures are the same as before. The solo changes are
all on the French report, re-read with its new title.

**Two expectations were widened.** With the author in the header, both
readers credit each article's own voice to its byline. Two expectations
had named only the outlet or "author" as the speaker:

- France 24's 700,000 estimate now also accepts David Rich (the page prints
  "By: David RICH");
- Christian Concern's introduction now also accepts Sean Redfearn ("Sean
  Redfearn talks to Jack Sterling").

Before the change, these two checks failed on the new readings.

**Judged checks.** The eight judged checks on the re-read sources went to a
blind judge, old and new readings shuffled. Seven ruled the same for both:
5 pass, 2 fail. One regressed, Harvest covenants' exceptions: the new
reading files Sample 2's steps under Sample 1.

The cause is the titles run, not these fixes. Besides the title, it set the
publisher to "Men's Discipleship Group - Sample #1", the first sample's
heading. The titles run set 344 publishers across the archive. In a sample
of 40, 17 named people (the text's authors, not its publisher) and three
only a country. Those need sorting before the corpus is read.

## For the reading stage

To be scored, a reading or audit run stores `source_id` in `run.inputs`, and
a quote that software cannot locate is kept as a usage with
`evidence_state = 'unlocated'`.

## Problems in the stored texts, found while writing expectations

- A MirrorWeb bot-check page was stored for a Scottish Parliament briefing
  (fetch detection missed it); kept here as the deliberate failure. Fixed
  10 Nov: detection catches it, `recheck` flags such copies `challenge_page`
  (7 found: 4 MirrorWeb, 3 Vimeo) and their targets are fetched again.
- transformingcongregations.org: the page builder keeps its text in a script
  blob; only a contact-form blurb is visible HTML. One of 25 such pages (of
  4,816), now listed in the preservation report for review.
- Harvest's women's curriculum page is title-only on the live site (as the
  package said).
- BACP MoU PDF: two-column layout scrambles reading order; title not in text.
- Trevor report PDF: footnotes mixed into body text; chart data lost.
- Davidson video: captions marked manual are speech-recognition quality, with
  no speaker labels.
- Exodus "Moving On" is a 1993 Regeneration article; 2008 is the capture date.
- Público piece is a Lusa agency text; the Stop the Ban page is cut off.
- Duplicate sources the URL rules missed: 5 YouTube videos and 17 DOIs
  queued under two URLs each. Fixed 10 Nov: work keys and `dedupe`.

## Commands

```bash
python -m archive testset prepare          # queue URLs, attach package PDFs, fetch, index
python -m archive testset show fr-4501 --grep "Torrents"
python -m archive testset score            # all entries
python -m archive testset score --split dev --judgements data/reports/judgements.json
```

Transfer package files are read from
`~/surviving-sogice-ingest/Transfer/SurvivingSOGICE_Complete_Package`
(`--package` to change) and checked against the SHA-256 in the test set.
