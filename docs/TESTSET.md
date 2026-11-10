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
| apa-consumers-report | Academic abstract only, via the DOI route (holdout) |
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
passes; no source takes more than 5 minutes.

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
