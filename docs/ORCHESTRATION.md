# Orchestration (16 November 2026)

This is the runbook for the Claude Opus orchestrator (Claude Code on the Mac
Studio) that takes the project from today to the demo of 31 March 2027.
`docs/PLAN.md` is the one plan: what and why. This document is how, who, in
what order and with what gate. It refers to the plan's steps and does not
repeat them.

Written by a Claude Code session in the cloud, from the repository only. It
could not see the Mac Studio, the database or any model. Every number about
the real archive comes from Sérgio's counts of 16 November or from the docs.
Every check that needs the database is written here as a command for you to
run.

Contents:

1. How to use this document
2. System map
3. Review (16 November)
4. Subprojects, each with a brief to paste
5. Critical path and schedule
6. Orchestration protocol
7. Decisions waiting for Sérgio
8. Risks

---

## 1. How to use this document

**Who you are.** A Claude Code session on the Mac Studio, on Sérgio's
Enterprise seat. You plan, start sessions, start local jobs, check results
and ask Sérgio for decisions. You do not tune against the holdout split. You
do not change a decision in `CLAUDE.md`.

**Read first, in this order:**

1. `CLAUDE.md`: the rules. They bind you and every session you start.
2. `docs/PLAN.md`: the plan.
3. This document: section 5 (critical path), then section 7 (decisions),
   then the subproject you are about to work on.
4. For a subproject: the doc it names (`docs/TAGGING.md`, `docs/REVIEW.md`,
   `docs/READING.md`, `docs/ACCOUNTABILITY.md`, `docs/TESTSET.md`,
   `docs/MODELS.md`).

`docs/HANDOFF.md` is from 9 November and out of date. Do not act on it.

**Where things stand: commands.** Run from the repository root.

```bash
git status -sb && git log --oneline | head -5
.venv/bin/python -m pytest -q                 # must pass before anything else
.venv/bin/python -m archive report            # preservation gate and failures
.venv/bin/python -m archive read --status     # reading runs in progress; exit 1 = stalled (other runs: the stuck-runs query below)
.venv/bin/python -m archive review status     # the review queue folder
ollama ps                                     # which model is loaded now
df -h . && du -sh data/scratch data/objects data/archive.db
```

**Where things stand: the database.** Read-only, always. These queries
were run read-only on 17 November against `data/archive.db` (a 12 November
state, schema 13) and all worked; fix a column name if SQLite complains
after a schema change, and correct this document.

```bash
DB=data/archive.db
q() { sqlite3 -readonly "$DB" "$1"; }

# Sources (keepers) and passages by language
q "SELECT count(*) FROM source WHERE merged_into IS NULL;"
q "SELECT language, count(*), sum(length(text)) FROM passage GROUP BY 1 ORDER BY 2 DESC LIMIT 12;"

# Sources with no readable text yet
q "SELECT count(*) FROM source s WHERE merged_into IS NULL AND NOT EXISTS
   (SELECT 1 FROM source_version v JOIN passage p ON p.version_id = v.id WHERE v.source_id = s.id);"

# Runs: what has been read and tagged, by model and prompt
q "SELECT role, json_extract(inputs,'\$.job') AS job, model, prompt_version, outcome, count(*),
          round(sum(seconds)/3600,1) AS hours
   FROM run GROUP BY 1,2,3,4,5 ORDER BY 1,2,3;"

# Sources read by both readers (current, succeeded runs)
q "SELECT count(*) FROM (SELECT json_extract(inputs,'\$.source_id') s FROM run
   WHERE role='reading' AND outcome='succeeded' GROUP BY s HAVING count(DISTINCT model) >= 2);"

# Statements and relations by state
q "SELECT found_by, review_state, count(*) FROM usage GROUP BY 1,2;"
q "SELECT review_state, count(*) FROM relation GROUP BY 1;"

# How sure: agreement between the two readers / taggers
q "SELECT target_table, confidence, count(*) FROM assessment WHERE current = 1 GROUP BY 1,2;"

# Tag lists seeded? (must show actor_type and cluster before the first tagging run)
q "SELECT scheme, count(*) FROM concept GROUP BY 1 ORDER BY 1;"

# Tags and excerpts
q "SELECT c.scheme, t.mode, t.review_state, count(*) FROM tag t JOIN concept c ON c.id = t.concept_id GROUP BY 1,2,3;"
q "SELECT kind, review_state, count(*) FROM asset GROUP BY 1,2;"

# Decisions by actor and action; the chain's head
q "SELECT actor_kind, actor_id, action, count(*) FROM decision GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 30;"
q "SELECT seq, event_hash FROM decision ORDER BY seq DESC LIMIT 1;"

# Runs that are stuck (any role: reading, tagging, check, preserve). On 17 November one
# preserve run had shown 'running' since 9 November: a leftover, not a live job.
q "SELECT id, model, started_at, heartbeat_at, progress FROM run WHERE outcome = 'running';"
```

**Then.** Find the first unfinished item on the critical path (section 5).
Open its subproject (section 4). Paste its brief into a new Claude Code
session, or run its commands yourself. Follow the protocol (section 6).

**Keep this document true.** When a step is done or a number is measured,
change the status here in the same commit. Do not write a second plan.

---

## 2. System map

### Stages

| # | Stage | Who | Command | Main tables | Status, 16 Nov |
|---|---|---|---|---|---|
| 1 | Queue | Python | `import-queue`, `queue-add` | `source`, `source_target` | 6,804 sources (6,826 with merged duplicates) |
| 1 | Preserve | Python | `preserve`, `recheck`, `dedupe`, `attach-pdfs` | `source_version`, `fetch_attempt`, files in `data/objects` | Done for the web corpus. Sérgio's own PDFs not attached yet |
| 1 | Extract, cut, detect language, index | Python | `index` | `section`, `passage`, `passage_fts` | 97,537 passages: 82,144 en, 5,274 es, 2,500 fr, 481 pt |
| 1 | Titles, authors, fingerprints | Python + a 12B model for first pages | `titles`, `authors`, `fingerprints` | `source`, `fingerprint` | Built and run (see their docs) |
| 2 | Seed vocabularies | Python | `seed` | `concept`, `concept_label`, `entity`, `entity_variant` | Seeded. Tag lists (actor types, clusters) not yet in the real archive |
| 2 | Exact matching | Python | `match` | `usage` (`found_by = 'exact_match'`) | Run: 151,271 term matches (TAGGING.md) |
| 3 | Read, two readers | Local models | `read --both` | `run`, `usage` (`found_by = 'model'`), `relation`, `claim` | Only the 24 test-set sources. Corpus run not started |
| 3 | Reconcile and relation rules | Python | `reconcile` (inside `read --both`) | `assessment`, `decision` | Built, measured on the test set. 974 relations, of which 576 superseded by later runs and 44 rejected |
| 3b | Tag, two taggers, compare | Local models + Python | `tag` | `tag`, `asset`, `assessment`, view `term_use` | Built. Four trials on dev copies. Nothing tagged in the real archive |
| 3b | Check | Local model (12B) | `check` | `decision` (action `audit`) | Built. Checker proposed, not confirmed |
| – | Review queue | Claude Code sessions | `review prepare / apply / status / undo`, `/review-queue` | packets in `data/review`, `decision` | Built. Trials on copies only |
| – | Evaluation | Python | `testset prepare / show / score` | files in `evaluation/` | Two test sets: readings (20 entries + 8 new) and tags |
| 4 | Embeddings and search | Local model + Python | none | none | **Not built.** FTS5 index exists; no vector table, no search command |
| – | Releases | Python | none | `release`, `release_record` (schema only) | **Not built** |
| – | Products | – | none | – | **Not built**: lexicon, catalogue/wiki, network, WebXR, documentary packets |
| – | Researcher interface | – | none | – | **Not built**; review lists are JSON reports |
| – | Backups | – | none | – | **Not built** (the plan's Phase 0 gate) |

### Data stores

| Store | What | Rule |
|---|---|---|
| `data/archive.db` | The archive. SQLite, schema in `archive/schema.sql` | Source of truth. Never run a trial on it. Never commit it |
| `data/objects/` | Preserved bytes by SHA-256 (2.3 GB) | Read-only files. Never commit |
| `data/scratch/` | Trial copies of the database (21 GB) | Trials only. Clean-up needs Sérgio's yes |
| `data/review/` | The review queue: `open`, `keys`, `verdicts`, `done`, `researcher` | Reviewers never open `keys/` |
| `data/reports/` | JSON reports of each command | Local |
| `seeds/` | Vocabularies, Entity Registry, tag lists | Committed. Public |
| `evaluation/` | The two test sets | Committed. Holdout rule in section 6 |
| `prompts/` | Every prompt version | A used prompt is never edited |
| `config/models.yaml` | Model names, the only place | |

### Records that matter most

- `usage`: a located quote. From matching (`exact_match`) or from a reader
  (`model`), with speaker, stance, states.
- `relation`: subject, type, object, with its evidence usages.
- `tag`: a label on one statement, with mode and cue. `asset`: an excerpt
  kept whole, or a new term.
- `assessment`: for each model record, whether both models gave it
  (`two_readers_agree`, `one_reader`, `readers_disagree`,
  `single_reading`). `current = 1` is the latest.
- `decision`: every proposal, comparison, check, ruling and undo, with
  actor, run, reason, quote and a hash chain. Append-only.
- `run`: one job of one model on one source, with prompt version, time and
  outcome.

### What "published" means today

Nothing is published yet, and no code selects what would be. The rules
exist only in the docs: agreed by both models and not flagged, or confirmed
in review; never a disputed or held record. The release step (subproject
S14) must write this selection once, in one place, and every product must
use it.

---

## 3. Review (16 November)

**How this review was made.** One session read the plan, the tagging and
review docs, and the code of `tag.py`, `review.py`, `check.py`, the schema,
and the commands `read`, `tag`, `review`. Four reviewers were started in
parallel; only the one on products, governance and schedule finished (the
other three were cut off by a usage limit). So: tagging, review, schema,
products and schedule are reviewed with evidence. Preservation, matching,
`reconcile.py` and `rules.py` were **not** reviewed line by line. Step R1
below repairs that.

Tests: 505 of 506 pass in the cloud. The one failure,
`tests/test_depot.py::test_put_is_content_addressed_and_idempotent`, is the
environment: the session ran as root, for whom every file is writable. It
passes for a normal user.

### Findings, by consequence

**1. The review workload, not the Mac Studio, decides the schedule.**
Machine time fits: 135 days to 31 March is about 2,750 hours at 85% uptime,
against about 1,250 hours of demand (reading 700, tagging 425, check 16,
Portuguese and Spanish about 80, embeddings a few). It fits once, not twice:
a second full reading does not fit comfortably, so the prompts must be
frozen before the bulk run. The review does not fit: about 130,000 tag
packets (400 session hours), and the reading side adds about as many. The
dev reading queue is 368 packets for about 0.36 million characters
(`docs/REVIEW.md`), which scales to roughly 100,000 packets for the corpus.
That projection is mine and unmeasured. Together: some 230,000 packets and
650 session hours, with 2% "for the researcher" already more than Sérgio
can work. See section 5 for the route that still makes the demo.

**2. Nothing downstream of tagging exists.** No embeddings table, no search
command, no release code, no export, no product, no interface, no backup.
Evidence: `archive/__main__.py` has 20 commands, none of these;
`pyproject.toml` has no `sqlite-vec`; tables `release` and `release_record`
exist (`schema.sql` 606–635) but no code writes them. Four and a half
months remain and nine things are unbuilt, with one researcher.

**3. No code decides what is public.** `visibility` defaults to `internal`
on every record and no Python code ever sets `public_eligible` (grep finds
none). The publication rules live in prose only. `release_record.table_name`
does not allow `tag`, `asset`, `assessment` or `decision` (`schema.sql`
618–621), so a release cannot freeze the lexicon's inputs or the
difficulties register. A schema version bump is needed before the first
release.

**4. The corpus reading run cannot be started as the code stands.** `read`
takes only `--source` (repeated) or `--testset` (`__main__.py` 260–271).
There is no "every unread source", no language filter, no order, no limit.
The same holds for `tag`. A small build is needed first (S3, step 1): read
source ids from a file, or select pending sources by language in a
priority order. What exists is sound: one run per source and model, written
only when the source is finished, skipped when already read with the same
prompt, heartbeat and stall detection, failed sources marked and the batch
going on (`read.py` 725–817).

**5. "Every AI run can be undone by run id" is only partly true.** `review
undo --run` (`review.py`, `undo`) reverses the decisions of any run that
changed records by decisions: review runs, and the rule, actor-rule and
titles runs (`docs/TITLES.md`). Dedupe merges can be undone. A reading or
tagging run cannot be undone by a command: it can only be superseded by a
new run; `review undo` on one would write undo decisions without changing
its records, so never point it at a reading or tagging run. A generic `archive undo --run RUN` is missing
(S16, step 4). The decision chain itself is solid: append-only, hash
chained, triggers refuse updates and deletes (`schema.sql` 558–600).

**6. Nothing protects the real archive from a trial, or the Mac Studio from
two jobs.** Only `read --model-role trial_*` and `check --model-role
trial_*` refuse the real database (`__main__.py` 292, 395). `tag --prompt tag-v4`, `check --held` and `review
apply` all default to `data/archive.db`. There is no lock: two terminals
can each load a model. Section 6 gives a wrapper; S0 builds it.

**7. No backup off this disk, which is nearly full, with an antivirus that
removes evidence files.** `data/backups` holds seven database snapshots
(1.5 GB, the last of 10 November), on the same disk, and none of
`data/objects`. The plan's Phase 0 gate ("backup working", 27 November) is
not met. Defender has quarantined a preserved capture before
(`docs/HANDOFF.md`). `Depot.read` checks an object's SHA-256 whenever it is
read, and `index` flags missing or corrupt objects, but no routine checks
all stored objects on a schedule. One disk fault loses the project. This is
the first job (S0).

**8. Governance is absent from the repository.** No data management plan,
no Sikt notification, no ethics note, no licence for code or data, no
takedown route, no flag for private individuals or minors on `entity`. The
archive links named people to sexual orientation and religion: special
categories under GDPR article 9. "Public testimony with attribution" is a
research decision, not yet a legal basis on paper. It may exist outside the
repository; if not, it gates every public release and the demo (S15).

**9. Tagging: the method is sound; the agreement is too low to run on the
corpus yet.** Measured on dev with tag-v3: clusters 30%, practices 43%,
functions 23%, actor types 60%; one-tagger tags right 57%. What is solid:
software locates every cue and excerpt; a tag is `(statement, concept)` so
comparison is exact; option B for terms is one SQL view, so a corrected
stance corrects the use at once; packets hide the model; verdicts need a
quote found in the shown text and refuse stale records. What is fragile:

- A statement only one reader recorded is tagged like any other
  (`tag.statements_for`), and its tags can be published by agreement of
  the taggers while the statement itself is held. The release rule must
  require both (S14).
- `compare` keeps the first tagger's copy and its cue; the second's cue is
  lost from view.
- Replies vary between runs at temperature 0 (TAGGING.md, third trial), so
  a re-run is not a reproduction. Record this in the method note.
- Agreed tags, the ones published without review, have no measured error
  rate. Item E (S5) builds the sample.
- `tag_sources` seeds the tag lists by itself (`tag.py`, `seed_lists`), so
  "seed before the first run" is automatic. But the lists are still
  waiting for Sérgio's review; seeding fixes ids, not content.

**10. Stance errors now reach the lexicon.** Under option B a wrong
"criticizes" turns a promoter's own word into "describes". Stance checks
found a wrong stance 8 times in 8, all read-v4 readings of defenders of
change efforts. The reading prompt must be chosen before the bulk run
(decision D1). This cannot be fixed in the tagger.

**11. The holdout is weak.** Both test sets are in a public repository.
`evaluation/tags_2026-11-12.json` has no split field, so any listing shows
holdout expectations; this already happened once (TAGGING.md, question 1).
`testset show` and `testset score` without `--split` show everything. A
tuner is kept honest by instruction only. Fix: a split field in the tags
file and a refusal to print holdout without an explicit flag (S16).

**12. Committed files to look at, since the repository is public.**
`seeds/sogice_glossary_2026-04-03.json` holds about 200 verbatim context
quotes from sources, many with URLs. `seeds/sanity_seed_2026-05-05.json`
and `seeds/Entity_Registry_v1.1.md` name people with roles, one as a
survivor. `seeds/quarantine/Term_More.md` is 99 KB copied from a wiki.
Short quotes are probably fine; Sérgio should decide knowingly (D12).

**13. Documentary clips have no path yet.** Passages carry `time_start`
from captions. Media files are counted, not downloaded; there is no
transcription code (`report.py` 139) and no table for clips. The gate "1
real clip" needs a decision on rights and a small manual route (S12).

**14. Docs out of date.** `README.md` was brought up to date on this
branch (17 November). `docs/PLAN.md` still
lists the 44 tactics, harm and target as passage facets, and gives Phases 1
and 2 future dates though both are done. `docs/HANDOFF.md` and
`docs/KICKOFF.md` are obsolete. `schema.sql` lets `usage.found_by` be
`embedding`, against "embeddings are never evidence". Fix in one doc
session (S16, step 5); the plan's content changes need Sérgio.

**15. What to cut or simplify.**

- Sanity for March: build the catalogue as static pages from the release;
  fill Sanity after the demo (D8).
- Image, audio and video embeddings: text only until after the demo.
- The checker on clusters (10 false alarms in 12): practices and excerpts
  only (built, S5 item C).
- The `audit` model role: nothing uses it.
- More verifier or small-model trials: measured twice, no gain.

### R1. Finish the code review (first week, a Claude Code session)

Three parts were not reviewed. Start one read-only session per part with
this brief; each returns findings with file and line, and adds them here.

> Review, read-only, one part of this repository as a senior engineer:
> (a) preservation and matching: `fetch.py`, `preserve.py`, `acquire.py`,
> `depot.py`, `extract.py`, `captions.py`, `passages.py`, `index_text.py`,
> `dedupe.py`, `attach.py`, `titles.py`, `authors.py`, `fingerprints.py`,
> `doi.py`, `seeds.py`, `match.py`, `quote_locator.py`; or (b) reading:
> `read.py`, `reconcile.py`, `rules.py`, `ollama.py`, `testset.py`,
> prompts read-v4 to v6. For each module: what it does, what is solid,
> real bugs (verified by reading), where docs and code disagree, what a
> 700-hour run would break. Never open the two files in `evaluation/`
> without keeping only entries whose split is "dev". Edit nothing. Output:
> findings by consequence, with file and line, and read-only `sqlite3`
> checks for anything that needs the database.

Checks for the Mac Studio that this review could not make:

```bash
# Language detection on short passages: how many are short, and what they were called
q "SELECT language, count(*) FROM passage WHERE length(text) < 200 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;"
# Sources whose passages are in several languages (which language is the source "in"?)
q "SELECT count(*) FROM (SELECT v.source_id FROM passage p JOIN source_version v ON v.id = p.version_id
   GROUP BY 1 HAVING count(DISTINCT p.language) > 1);"
# Preserved versions flagged as doubtful
q "SELECT j.value, count(*) FROM source_version v, json_each(v.quality_flags) j GROUP BY 1 ORDER BY 2 DESC;"
# Passages with timecodes (what documentary clips can rest on)
q "SELECT count(*), count(DISTINCT version_id) FROM passage WHERE time_start IS NOT NULL;"
# Reading runs that failed, by error
q "SELECT substr(error,1,60), count(*) FROM run WHERE outcome='failed' GROUP BY 1 ORDER BY 2 DESC LIMIT 15;"
# Relations by type and state (what the network would show)
q "SELECT type, review_state, count(*) FROM relation GROUP BY 1,2 ORDER BY 3 DESC;"
# Speakers recorded under many labels in one source (the webinar-host problem)
q "SELECT v.source_id, count(DISTINCT u.speaker) n FROM usage u JOIN passage p ON p.id = u.passage_id
   JOIN source_version v ON v.id = p.version_id WHERE u.found_by='model' GROUP BY 1 ORDER BY n DESC LIMIT 15;"
```

---

## 4. Subprojects

Each has: goal, state, needs, steps (with who), commands, gate, estimate,
risks, open decisions, and a brief to paste into a new Claude Code session.
"MS" is the Mac Studio. Hours are estimates unless a doc is cited. Every
brief assumes the session has read `CLAUDE.md` first and follows section 6.

| # | Subproject | Serves | Needs the Mac Studio |
|---|---|---|---|
| S0 | Operations: backup, disk, job lock | everything | yes (disk) |
| S1 | Corpus and preservation | all | fetching only |
| S2 | Seeds, vocabulary, lexicon curation | lexicon, tags | no |
| S3 | Reading: English, Portuguese, Spanish | all | yes, most of it |
| S4 | Reconcile and software rules | network, review load | no |
| S5 | Tagging (step 3b) | lexicon, search, exhibition | yes |
| S6 | Review queue and the weekly sample | all published records | no |
| S7 | Researcher interface | Sérgio's work | no |
| S8 | Entity Registry, relations, 2D network | network, WebXR | no |
| S9 | Embeddings and archive search | search, suggestions | a few hours |
| S10 | Lexicon product | lexicon | no |
| S11 | Catalogue/wiki via Sanity | catalogue | no |
| S12 | Documentary packets | film, teaching | no |
| S13 | WebXR exhibition scene | the demo | no (a Quest) |
| S14 | Releases | every product | no |
| S15 | Governance | permission to publish | no |
| S16 | Evaluation and accountability | trust in all of it | dev runs |

### S0. Operations: backup, disk, job lock

**Goal.** The archive survives a disk fault and an antivirus. One model job
at a time. Trials cannot touch the real database.

**State.** No backup off the disk; seven older database snapshots in
`data/backups` (1.5 GB, last 10 November). Disk 98% full on 16 November
(about 21 GB free; Sérgio freed some on 17 November); `data/scratch` holds
about 21 GB of old trial copies.

**Needs.** Sérgio: the backup destination (D10), yes to the clean-up (D11).

**Steps.**

1. Sérgio: answer D10 and D11.
2. Python: `scripts/backup.sh`: `sqlite3 data/archive.db ".backup
   'DEST/archive-DATE.db'"`, then `rsync -a data/objects/ DEST/objects/`,
   then a restore check (`PRAGMA integrity_check` on the copy, and a
   count of objects). Weekly, and before every bulk run.
3. Python: `scripts/verify_objects.py`: every `source_version.path` exists
   and its SHA-256 matches. Report missing files (Defender). Never restore
   from quarantine. Do not fetch the page again by default: `acquire`
   leaves missing objects alone on purpose (`acquire.py`, the note above
   `_CHALLENGE_STORED`), because the same capture is quarantined again.
   Copy from the backup, or list the source for Sérgio.
4. Python: `scripts/ms_job.sh`: the lock wrapper of section 6.
5. Claude session: keep the trial copies that later steps need
   (`tag-dev3.db`, `review-tags-dev2.db`, the newest reading trial), list
   the rest with sizes for Sérgio; delete only what he approves.
6. Python: make `tag`, `check`, `read --again` and `review apply` refuse
   `data/archive.db` when the prompt is not the default, unless `--real`
   is given.

**Gate.** A restore test passes from the backup. `verify_objects.py`
reports no missing object. Two `ms_job.sh` calls at once: the second waits.

**Estimate.** MS 2 h (copying). Session 3 h. Sérgio 30 min.

**Risks.** The backup disk is also the only copy: use two destinations if
possible (an external disk and UiB storage). Disk fills during the bulk
run: check `df` in the daily status.

**Brief.**

> Goal: backup, object verification and a job lock for this archive. Write
> `scripts/backup.sh` (SQLite `.backup` of `data/archive.db` to a
> destination given as argument, rsync of `data/objects`, integrity check
> of the copy), `scripts/verify_objects.py` (every `source_version.path`
> exists and matches its `sha256`; JSON report; no changes), and
> `scripts/ms_job.sh` (run a command under `flock data/ms.lock`, log start,
> end and exit code to `data/reports/ms_jobs.log`). Add tests for the
> Python script with a fixture database. Constraints: standard library
> only; never delete anything; never touch Microsoft Defender; nothing
> under `data/` is committed. Done when: tests pass, a restore check from
> a backup succeeds, and `docs/ORCHESTRATION.md` S0 says so.

### S1. Corpus and preservation

**Goal.** Every source stored with text, language and passages; Sérgio's
own PDFs attached. Serves all products.

**State.** Built and run: 6,804 sources, 97,537 passages. Not reviewed
line by line tonight (R1). Open: Sérgio's PDF collection; audio and video
files are counted, not transcribed; pages drawn by JavaScript and
two-column PDFs are on review lists only.

**Needs.** Sérgio copies his PDFs to the Mac Studio (D13).

**Steps.**

1. Claude session: R1, part (a).
2. Python: `archive report`; note the gate figure here.
3. Sérgio: copy the PDFs. Python: `attach-pdfs DIR --dry-run`, a session
   reads the doubtful matches, then without `--dry-run`, then `index`,
   then `match`.
4. Python: `scripts/verify_objects.py` (S0).
5. Later: transcription of audio and video without captions. Not before
   the demo, except for the one clip (S12).

**Commands.**

```bash
.venv/bin/python -m archive report
.venv/bin/python -m archive attach-pdfs ~/sogice-pdfs --dry-run
.venv/bin/python -m archive attach-pdfs ~/sogice-pdfs && .venv/bin/python -m archive index && .venv/bin/python -m archive match
```

**Gate.** The plan's Phase 1 gate: 90% of text sources stored with text,
language and passages. Doubtful PDF matches: none attached without a look.

**Estimate.** MS 1 h. Session 2 h. Sérgio 1 h.

**Risks.** New passages after the bulk reading has started: they are read
in a later batch; the release counts what is read. Do not re-cut existing
passages: every usage offset depends on them.

**Brief.**

> Goal: attach the researcher's own PDFs and confirm the preservation gate.
> Run `archive report`; run `attach-pdfs DIR --dry-run` and read the
> report in `data/reports/`; list doubtful matches for Sérgio with file
> name, candidate source and score; attach only the sure ones; then
> `index` and `match`. Constraints: PDFs are researcher copies, never
> committed, never published; do not re-index sources that already have
> passages. Done when: the report's gate figure and the count of attached
> PDFs are written into `docs/ORCHESTRATION.md` S1.

### S2. Seeds, vocabulary and lexicon curation

**Goal.** One vocabulary the taggers, the matcher and the lexicon share.
Serves the lexicon, tags, search.

**State.** Seeded: Lexicon v2.1, ontology, practices, functions, Entity
Registry, GOV.UK glossary; the April glossary through a merge map. Tag
lists: 19 actor types and the function definitions in
`seeds/tag_lists_2026-11-12.json`, the 7 clusters from
`seeds/SOGICE_Ontology_v3.0.md`; added by the first tagging run. Waiting
for Sérgio: the actor-type list, the function definitions (drafted by
Claude), the vocabulary boundaries the reviewers found unclear.

**Needs.** Sérgio's rulings (D5). No Mac Studio.

**Steps.**

1. Sérgio: D5. Each ruling is one or two sentences of definition.
2. Claude session: write the rulings into a new dated seed file (never
   edit a seed that a run used; the vocabulary hash is recorded per run),
   and into `prompts/tag-v5.md` only if a prompt must change.
3. Python: `archive seed`, then `archive match` for new labels.
4. Sérgio, monthly: the lexicon candidates list (new terms from the
   taggers; S5 item D). Accepted terms go into a seed file as concepts,
   with their first located quote.
5. Claude session: functions from the lexicon (TAGGING.md proposal 5): a
   software pass that adds a term's own function to a statement that uses
   it. Build after the demo set is tagged; measure on dev.

**Gate.** Every label a tagger sees has a definition. No concept without
a scheme. Each accepted new term has a located quote.

**Estimate.** Session 4 h. Sérgio 2 h, then 30 min a month.

**Risks.** A vocabulary change after the bulk tagging makes old and new
tags differ. Freeze the tag lists before the bulk run (gate G1).

**Brief.**

> Goal: apply Sérgio's vocabulary rulings (given below) to the tag lists.
> Write `seeds/tag_lists_<date>.json` as a new file, point
> `archive/tag.py` `LISTS_SEED` at it, update the tests, and add a short
> dated note to `docs/TAGGING.md`. Constraints: never edit a seed file or
> prompt that a run has used; model names only in `config/models.yaml`;
> `pytest` passes. Done when: `archive tag --db COPY --testset --split
> dev --dry-run` shows the new lists and the tests pass. (Since 17
> November a dry run keeps no data, but use a copy anyway.)

### S3. Reading: English, then Portuguese, then Spanish

**Goal.** Every source read by both readers: statements with speaker and
stance, entities, claims, relations. Serves everything.

**State.** Built and measured on the test set. Only the 24 test-set
sources are read. read-v4 is the default; read-v5 and read-v6 were tested
and not adopted. Time with read-v4: 31B 10.4 s and Qwen 9.0 s per 1,000
characters (read-v6: 12.5 and 10.7; `docs/MODELS.md`). English is 109.8
million characters: about 590 h with both readers at read-v4's speed,
about 710 h at read-v6's. Corpus selection is built (step 1, 17 Nov):
`read`, `tag`, `check` and `review prepare` take `--sources-file` and
`--limit`, and `scripts/reading_order.py` writes the order. Its first run
(17 Nov, read-v4, no `--first` file yet) lists **5,193 English sources,
107.5 million characters** (what `read` would send: page furniture,
repeats and flagged versions left out; merged copies counted with their
keeper). Left out: 22 merged, 1,094 mostly in another language, 496 with
no text to read, 21 already read by both readers. 4,188 of the 5,193 have
exact matches.

**Needs.** D1 (the reading prompt), D2 (the order and the demo set), S0
(backup and lock). The Mac Studio for about 630 to 760 h (all three
languages, depending on the prompt).

**Steps.**

1. Done (17 Nov). Claude session, no MS: corpus selection for `read`,
   `tag`, `check` and `review prepare` (S5 step 7 and S6 use all four):
   `--sources-file FILE` (one id per line; blank lines and `#` comments
   ignored, also after an id; file order kept; duplicates and merged ids
   kept once, by keeper; an unknown id stops the command before anything
   runs) and `--limit N` (the first N after selection). Both combine with
   `--source` and `--testset`: file first, then `--source`, then the test
   set. `review prepare` refuses an empty selection rather than preparing
   the whole archive. `scripts/reading_order.py` opens the database
   read-only and writes the pending sources of one language in priority
   order: the `--first` file (demo set) in its order, then exact matches
   (`usage.found_by = 'exact_match'`, not rejected or superseded) per
   1,000 characters, densest first, then the sources without matches.
   "Pending" means not read by both readers in `config/models.yaml` with
   the prompt as it is now (a source one reader has read is listed; `read
   --both` skips the reader that is done), and not failed twice by a
   reader still to read it (retried once, then left failed for a person:
   counted as "failed twice"). Each line carries a comment
   with its characters and density. Tests: `tests/test_selection.py`,
   `tests/test_reading_order.py`.
2. Sérgio: D1. Whatever he chooses, a session that has never seen the
   holdout expectations runs the holdout score once (S16).
3. MS: wave 1, the demo set (about 300 sources): `read --both`.
4. Claude session: read the difficulties report and 30 random statements
   of wave 1. Any change of prompt or rule goes through section 6.
5. Gate G1 (section 5): prompts frozen.
6. MS: wave 2, the rest of English, in the order file, in chunks per
   `ms_job.sh` call, tagging following batch by batch (S5). Daily: `read
   --status`, failed runs, disk. Size chunks by characters, not sources:
   in the 17 Nov order, 500 sources are anything from 1.1 to 17.1 million
   characters (about 6 to 92 h for both readers at read-v4's speed),
   because 21 sources over 500,000 characters hold 18% of the English
   text (the largest 3.3 million). The comment on each line of the order
   file gives its characters. Rerun `reading_order.py` before each chunk:
   what is done drops out, so `--limit N` always takes the next N.
7. MS: Portuguese, then Spanish, with the same prompts. First 10 sources
   of each: a session checks quote location rate and speaker fill before
   the rest runs. French: not in the plan; ask Sérgio after Spanish.

**Commands.** (`--sources-file`, `--limit` and `scripts/reading_order.py`
exist since step 1; `scripts/ms_job.sh` after S0 step 4. Until then, run a
model job directly, one at a time, after `ollama ps` shows nothing
loaded.) Look before a model job: `--dry-run` with the same selection
prints the sources and their sections and calls no model; `read
--dry-run` and `tag --dry-run` open the database like the real command,
so point them at a copy. The demo set (D2) goes in a file of ids given
with `--first`.

```bash
.venv/bin/python scripts/reading_order.py --db data/archive.db --language en --out data/reports/order-en.txt
.venv/bin/python scripts/reading_order.py --db data/archive.db --language en --first data/reports/demo-set.txt --out data/reports/order-en.txt
.venv/bin/python -m archive read --db data/scratch/COPY.db --sources-file data/reports/order-en.txt --limit 5 --dry-run
scripts/ms_job.sh .venv/bin/python -m archive read --both --sources-file data/reports/order-en.txt --limit 500
.venv/bin/python -m archive read --status
.venv/bin/python -m archive reconcile --all --report-only
```

**Gate.** The plan's Phase 3 gate on the test set (90% of quotes located;
speaker and stance filled; at most 15 s per 1,000 characters). For the
run: failed sources under 2%; every failed source listed with its error.

**Estimate.** MS about 590 to 710 h English (read-v4 or read-v6 speed);
Portuguese and Spanish (7.6 million characters) about 40 to 50 h of
reading, about 80 h with their tagging.
Session 1 h a week of checks. Sérgio: D1 and D2.

**Risks.** A prompt change after wave 2 starts costs up to 700 h: G1
exists to prevent it. A stalled model: `read --status` (reading runs
only; tagging and check runs: the stuck-runs query in section 1) exits 1
after 20 minutes without progress; kill, and the next call closes the stalled run
and goes on. Ollama or model update mid-run: do not update; record
`ollama --version` and the model digests at the start.

**Open decisions.** D1, D2.

**Brief.**

> Goal: let `read`, `tag`, `check` and `review prepare` run over the
> corpus in a given order. Add `--sources-file` and `--limit` to these
> four commands in `archive/__main__.py`, and write `scripts/reading_order.py`: sources
> not merged, with passages mostly in the given language, not yet read by
> both readers with the given prompt; demo-set ids first (a file given
> with `--first`), then by number of exact matches per 1,000 characters,
> descending. Tests on a fixture database. Constraints: no model calls;
> standard library; do not change how a source is read. Done when: tests
> pass and `--dry-run` with a sources file prints the first sources.

### S4. Reconcile and software rules

**Goal.** Software settles what needs no judgement, so the review queue
holds only real questions. Serves the network and the review load.

**State.** Built: `reconcile.py` (pairs statements by position, assesses
agreement), `rules.py` (`relations/v1`: ends must be names in the text,
role lines, bare professions, amendments). The seven rules of the review
trial are adopted. Open: an own-voice rule for author or publisher
speaker labels (78 disputes on the test set, 46 of them on dev); the two
test-set conflicts (fr-p42). (The 23 publisher disagreements of the titles
pass are settled: the old publisher is kept, `docs/TITLES.md`.)

**Needs.** D6 (own-voice rule), D7 (fr-p42). No Mac Studio: rules run on
stored readings.

**Steps.**

1. Claude session: R1, part (b).
2. Sérgio: D6, D7.
3. Claude session: the own-voice rule: when the two readers differ only
   in the speaker label, and both labels name the source's author or
   publisher (`source.author`, `source.publisher`, registry variants),
   keep one statement with the canonical label. Recorded as rule
   decisions, like `relations/v1`. Measure on a copy of the dev trial:
   disputes before and after, and the dev score must not fall.
4. Speaker lists for recordings without labels: `archive speakers SRC
   --set "Name=host"` for each recording in the demo set first.

**Commands.**

```bash
.venv/bin/python -m archive reconcile --db COPY --testset --split dev
.venv/bin/python -m archive testset score --db COPY --split dev
.venv/bin/python -m archive review prepare --db COPY --folder data/scratch/q --testset --split dev
```

**Gate.** Of the 78 own-voice disputes, 46 are on dev (France 24 40,
Christian Concern 6; Público's 30 and the APA's 2 are holdout entries, not
to be looked at by the tuner). Those 46 fall by at least 35; no dev
expectation lost; every rule decision carries its reason and quote.

**Estimate.** Session 5 h. Sérgio 30 min.

**Risks.** A rule that merges two different voices (a guest quoted on the
publisher's site). The rule must require both labels to resolve to the
same registry entity or the same normalised string as the source's
author or publisher; anything else stays a dispute.

**Brief.**

> Goal: an own-voice rule in `archive/reconcile.py` or `archive/rules.py`:
> two readers' copies of the same statement that differ only in the
> speaker label, where both labels name the source's author or publisher,
> become one agreed statement. Record each as a rule decision with the
> two labels and the reason; undoable. Tests with a fixture. Measure on a
> copy of the dev database (`reconcile`, `testset score --split dev`,
> `review prepare`): report disputes before and after. Constraints: dev
> split only; trials on copies; no silent merges. Done when: tests pass
> and the numbers are in `docs/REVIEW.md`.

### S5. Tagging (step 3b)

**Goal.** Each statement gets its actor types, term modes, clusters,
practices and functions; excerpts are kept whole. Serves the lexicon,
search, the network filters and the exhibition.

**State.** Built and tried four times on dev (`docs/TAGGING.md`). Not run
on the real archive. Agreement with tag-v3: actor types 60%, terms 90%,
clusters 30%, practices 43%, functions 23%. Dev queue: 476 tag packets
(404 now that new terms go to the lexicon list, item D). Built on 16–17
November on branch `fable/tagging-v4`, with unit tests (details in
`docs/TAGGING.md`, "Built, not yet measured"). Item A was measured on 17
November (step 1) and changed to `actors/v2`; B to F are not yet
measured. Not built: the parliamentary-record rule (D22).

| Item | What | Where |
|---|---|---|
| A | Actor rules `actors/v2`: an actor type is dropped when its words begin with, or directly follow, another person's name, or the Entity Registry says the speaker is the other kind (person against organisation). Words that are only the speaker's name are counted, not dropped (v1 dropped them; see step 1). Registry entry shown in actor packets | `archive/tag.py` `apply_actor_rules`; `tag --rules-only` |
| B | Prompt tag-v4 | `prompts/tag-v4.md` |
| C | The checker asks about practices and excerpts only | `archive/check.py` `SCHEMES`; `check --schemes` for trials |
| D | New terms leave the queue for a lexicon candidates list | `archive/review.py` `lexicon_candidates`; `review candidates` |
| E | A sample of agreed tags as blind packets | `review prepare --sample-agreed N` |
| F | One measurement script | `scripts/tagging_report.py` |

**Needs.** The Mac Studio for the dev runs (about 93 minutes for both
taggers) and then 425 h for English. D3 (checker), D4 (adopt tag-v4 and
the rules), D5 (vocabulary). The dev trial copies in `data/scratch`.

**Steps.**

1. Done on 17 November: A alone on the tag-v3 trial (Python, no model,
   seconds). To repeat it, on a fresh copy:

   ```bash
   rm -f data/scratch/tag-dev3-rules.db*
   cp data/scratch/tag-dev3.db data/scratch/tag-dev3-rules.db
   .venv/bin/python -m archive tag --db data/scratch/tag-dev3-rules.db --testset --split dev --rules-only
   .venv/bin/python scripts/tagging_report.py --db data/scratch/tag-dev3-rules.db --split dev \
       --calibration data/scratch/review-tags-dev2.db \
       --runs run_cec238c9341b45279d66e61f7685c3dd run_8fdb1b16ff2a484e88784a01193abf01
   ```

   Result. `actors/v1` failed the test "no type the calibration reviewers
   ruled right is dropped": it dropped 115 such tags against 163 ruled
   wrong, and agreement per speaker fell from 60% to 58%. Its name-only
   rule refused true types whose words were the name: deputies in the
   Assemblée nationale report as politicians, Harvest USA as a ministry,
   The Trevor Project as an advocacy group. The calibration reviewer had
   proposed flagging these, not refusing them. Its another-person rule
   matched a name anywhere in the words ("started Strong Support").
   `actors/v2` drops only role lines that begin with or directly follow
   another person's name, and counts name-only words (1,178 tags) without
   refusing them. On the same copy: no tag ruled right is dropped; 155
   ruled wrong are; actor agreement per tag 55% → 59%, per speaker 60% →
   60%; statements in held-tag packets 1,812 → 1,628; actor packets 49 →
   48; dev expectations unchanged (38 agreed; 43 and 43 alone). Two types
   both taggers gave lose one tagger's copy because its words were about
   someone else; the other copy goes to review, so neither type is lost.
2. MS: tag-v4 on a few dev entries first, then the full dev run. (Until
   S0 step 4 builds `scripts/ms_job.sh`, run the command without it, one
   job at a time, after `ollama ps` shows nothing loaded.)

   ```bash
   cp data/scratch/tag-dev3.db data/scratch/tag-dev4.db
   scripts/ms_job.sh .venv/bin/python -m archive tag --db data/scratch/tag-dev4.db --testset --split dev --prompt tag-v4 --entry KEY1 --entry KEY2
   scripts/ms_job.sh .venv/bin/python -m archive tag --db data/scratch/tag-dev4.db --testset --split dev --prompt tag-v4
   ```

   Pick the entries with `testset show` on dev keys only. A full run takes
   about 93 minutes (31B 55, Qwen 38). New runs supersede the tag-v3 runs
   per model in the copy.
3. Python: score and measure.

   ```bash
   P=.venv/bin/python; C=data/scratch/tag-dev4.db; T=evaluation/tags_2026-11-12.json
   $P -m archive testset score --db $C --split dev --tags $T
   $P -m archive testset score --db $C --split dev --tags $T --tagger gemma4:31b-mlx
   $P -m archive testset score --db $C --split dev --tags $T --tagger qwen3.8:27b-mlx
   scripts/ms_job.sh $P -m archive check --db $C --testset --split dev
   $P -m archive review prepare --db $C --folder data/scratch/queue-dev4 --testset --split dev
   $P scripts/tagging_report.py --db $C --split dev --calibration data/scratch/review-tags-dev2.db \
       --runs run_cec238c9341b45279d66e61f7685c3dd run_8fdb1b16ff2a484e88784a01193abf01
   ```

   (Model names as in `config/models.yaml`; they are written here only
   because the score command takes them as arguments.)
4. Claude session: the agreed-tags sample (item E), on the copy.

   ```bash
   $P -m archive review prepare --db $C --folder data/scratch/sample-dev4 --testset --split dev --sample-agreed 50 --sample-only
   ```

   `--sample-only` matters: without it the folder also gets the whole dev
   queue (about 400 packets), and sample packets cannot be told from the
   others without opening `keys/`. Then two or three fresh reviewer
   sessions, blind, exactly as the calibration did. Give each:
   `prompts/review-v3.md`, the tagging prompt the packets name, and a list
   of packet files from `data/scratch/sample-dev4/open/`. Forbid: `keys/`, the database, the
   code, `docs/`. Each writes verdicts to `verdicts/`. Then once:
   `$P -m archive review apply --db $C --folder data/scratch/sample-dev4`,
   then `$P scripts/tagging_report.py --db $C --split dev --sample-folder
   data/scratch/sample-dev4` prints the error rate of agreed tags per kind.
5. Orchestrator: write the numbers into `docs/TAGGING.md` ("Fifth trial")
   and ask Sérgio D4 with the table below filled in.

   | Measure | tag-v3 | tag-v3 + rules (`actors/v2`, 17 Nov) | tag-v4 + rules | Wanted |
   |---|---|---|---|---|
   | Actor types agreed (per speaker and type) | 60% | 60% | | ≥ 80% |
   | Clusters agreed | 30% | 30% (the rules touch actor types only) | | ≥ 45% |
   | Practices agreed | 43% | 43% | | ≥ 55% |
   | Functions agreed | 23% | 23% | | ≥ 35% |
   | Terms agreed | 90% | 90% | | ≥ 90% |
   | Dev expectations, agreed view (of 52) | 38 | 38 | | ≥ 40 |
   | Dev expectations, each tagger alone | 43, 43 | 43, 43 | | ≥ 43 |
   | Tag packets in the dev queue | 476 (404 with new terms on the list) | 403 (statements in held-tag packets 1,812 → 1,628) | | ≤ 330 |
   | Calibration: rejected tags no longer given | – | 155 of 305 (actor types: 155 of 237) | | most |
   | Calibration: confirmed tags lost | – | 0 (`actors/v1`: 115) | | none |
   | Agreed tags wrong (sample of 50) | not measured | – | | ≤ 10% |
   | Seconds per 1,000 characters (31B, Qwen) | 8.2, 5.7 | – | | not slower |

   The "wanted" column is this session's proposal, not a decision.
6. If adopted: a session that did not tune runs the holdout once (S16).
7. MS: tag wave 1 (demo set), then follow the reading batch by batch.

   ```bash
   # PROMPT: the tagging prompt Sérgio adopted at D4 (tag-v3 is still the command's default)
   scripts/ms_job.sh .venv/bin/python -m archive tag --prompt PROMPT --sources-file data/reports/order-en.txt --limit 500
   scripts/ms_job.sh .venv/bin/python -m archive check --sources-file data/reports/order-en.txt --limit 500
   ```

   (`--sources-file` and `--limit` exist after S3 step 1; `ms_job.sh`
   after S0 step 4. Better still: make the adopted prompt the default in
   `archive/__main__.py` when D4 is decided, so no bulk command can run the
   old one.)

**Gate.** To adopt tag-v4: no row of the table worse than tag-v3 beyond
noise (run-to-run variation is real: repeat one dev entry twice to see
it), the queue smaller, and the agreed-tag error rate measured. To start
the bulk tagging: G1.

**Estimate.** MS: 4 h for the dev measurement; 425 h English; 30 h
Portuguese and Spanish; check about 10 h (practices and excerpts only).
Sessions: 3 h. Sérgio: D4, 20 minutes.

**Risks.** tag-v4 lowers recall (stricter rules make both taggers give
less): the "each tagger alone" row shows it. tag-v4 asks for the words
that state a role rather than the name, and allows the name alone only
for a member speaking in a parliament's own debate; if the 31B or Qwen then gives fewer true actor types, the
calibration comparison shows it as "right: lost". Agreement
stays low for functions: then ask Sérgio whether functions are published
only from the lexicon (proposal 5) for now.

**Open decisions.** D3, D4, D5.

**Brief.**

> Goal: measure the tagging fixes of branch `fable/tagging-v4` on the dev
> split and report. Follow steps 1 to 5 of S5 in `docs/ORCHESTRATION.md`
> exactly, on copies of `data/scratch/tag-dev3.db`; never on
> `data/archive.db`. Constraints: dev split only (`--split dev` on every
> command; never print an entry of `evaluation/*.json` whose split is not
> "dev"); one model job at a time through `scripts/ms_job.sh`; do not
> edit `prompts/tag-v4.md` after its first run (write tag-v5; it was last
> changed on 17 November, before any run); model
> names stay in `config/models.yaml`. Done when: the table in S5 step 5
> is filled, `docs/TAGGING.md` has a dated "Fifth trial" section with the
> numbers and what failed, tests pass, and the question D4 is ready for
> Sérgio with a recommendation.

### S6. Review-queue operations and the weekly sample

**Goal.** Everything the models did not settle is ruled on before it is
published; Sérgio samples the rulings. Serves every published record.

**State.** Built: `review prepare / apply / status / undo`, prompt
review-v3, the `/review-queue` command, blind packets, per-speaker
groups, stance checks. Measured: about 10 seconds a packet; 150 tag
packets by three sessions in 8½ minutes each. Projection for English:
130,000 tag packets and about 100,000 reading packets (finding 1).

**Needs.** S3 and S5 output. Claude Code sessions. Sérgio weekly.

**Steps.**

1. Python, after each tagged batch: `review prepare` for those sources.
2. Claude sessions: `/review-queue 40`, in priority order (file names
   start with the priority). Two or three sessions in parallel is fine:
   give each its own list of packet files; `review apply` runs once, by
   the orchestrator, after they finish.
3. Python: `review apply`; read `problems`; stale or malformed verdicts
   go back to the queue.
4. Each review run ends with a patterns note. Recurring rulings become
   rules or prompt lines through section 6, not by habit.
5. Sérgio, weekly (D9): 30–50 items: all "for the researcher" packets
   first, then a random sample of that week's rulings, then a few agreed
   records that nobody reviewed. His verdicts are researcher decisions.
   Until the interface exists (S7): a Markdown file per week in
   `data/review/researcher/`, he writes agree / disagree and a line; a
   session records them.
6. Orchestrator, weekly: the error rate per kind from his sample. A kind
   above the agreed rate is not published (TAGGING.md, verification 4).

**Commands.**

```bash
.venv/bin/python -m archive review prepare --source SRC_ID        # or --sources-file after S3 step 1
.venv/bin/python -m archive review status
.venv/bin/python -m archive review apply
.venv/bin/python -m archive review candidates                     # new terms, for the lexicon
.venv/bin/python -m archive review undo --run RUN_ID --reason "why"
```

**Gate.** For the demo: every packet of the demo set ruled on or with
Sérgio. Weekly: his sample done; disagreement with reviewers under 10%
per kind, or that kind is held back.

**Estimate.** Demo set: about 11,500 packets, 35 session hours. The
whole English corpus: about 650 session hours at today's agreement (not
before 31 March; section 5). Sérgio: 1½ h a week.

**Risks.** Reviewer drift over thousands of packets: seed 1 known packet
in 50 (already ruled by Sérgio) and compare. "For the researcher" volume
beyond what he can do: cap it, order by priority, and let the rest wait
unpublished. Sessions seeing `keys/`: keep keys outside the folder a
reviewer is given (copy `open/` only).

**Open decisions.** D9; whether the bulk queue is worked in full, or by
priority with the rest held (section 5).

**Brief.**

> Goal: work review packets. Read `prompts/review-v3.md` and the tagging
> or reading prompt each packet names. For each file in the list given,
> write `verdicts/<issue>.json` as the packet says: verdict, the deciding
> quote copied exactly from the packet's source text, a reason,
> confidence. Low confidence is fine: say so. Constraints: never open
> `keys/`, the database, `archive/`, `docs/` or `evaluation/`; rule only
> from the packet. End with a patterns note: rulings that recurred, and
> definitions that were unclear. Done when: every packet in the list has
> a verdict file.

### S7. Researcher interface

**Goal.** A visual layer where Sérgio fixes mistakes, settles review
lists, attaches his PDFs, confirms or rejects AI decisions. Every action
is a recorded researcher decision and can be undone. No throwaway
CLI-only flows.

**State.** Not built. What exists to build on: `record_decision` with
`actor_kind = 'researcher'`, `review.undo`, `review._set` (the allowed
columns per table), packets as Markdown, JSON review lists.

**Needs.** D14 (the stack). No Mac Studio beyond hosting it.

**Steps.**

1. Claude session: `archive/researcher.py`: the actions as functions,
   each one transaction with a researcher decision: confirm, reject,
   edit a field (the `_SETTABLE` columns), undo a decision, attach a
   file to a source, set a speaker list. Tests first. This is the part
   that matters; the screen is thin.
2. Claude session: `python -m archive serve`: a local web app on
   127.0.0.1, standard library `http.server`, server-rendered HTML, no
   build step. Screens, in order of need:
   a. the weekly sample and "for the researcher" packets: the packet,
      agree / disagree / correct, a note;
   b. search (S9) with a record page: source, passage, statements, tags,
      relations, each with its decision history and an undo button;
   c. review lists: doubtful PDF and title matches, failed sources,
      lexicon candidates;
   d. attach a PDF to a source.
3. Sérgio: use screen (a) for one weekly sample; his notes drive the rest.

**Gate.** Sérgio does one full weekly sample in it without the terminal.
Every action appears in `decision` with `actor_kind = 'researcher'`, and
undo restores the previous state (tested).

**Estimate.** Sessions 15 h for (a) and (b), 10 h for the rest. Sérgio
2 h of use and comments.

**Risks.** It grows into a product. Keep it local, single user, no
login, no JavaScript framework. Writes while a model job runs: SQLite
handles one writer at a time; keep `busy_timeout`, keep transactions
short.

**Brief.**

> Goal: the researcher's actions as tested functions, then a thin local
> web page. Step 1: `archive/researcher.py` with `confirm`, `reject`,
> `edit`, `undo_decision`, each one transaction that changes the record
> and appends a `decision` with `actor_kind='researcher'`,
> `previous_state` and `new_state` (reuse `review._set` and
> `db.record_decision`). Step 2: `python -m archive serve --port 8765`:
> `http.server` on 127.0.0.1 only, server-rendered HTML, screen (a) of S7
> first. Constraints: standard library only; no data leaves the machine;
> nothing under `data/` committed; a reviewer's ruling is never shown as
> researcher-verified. Done when: tests cover each action and its undo,
> and the weekly sample can be worked in the browser.

### S8. Entity Registry, relations and the 2D network

**Goal.** A network where every edge reads "Source X states …" with its
quote. Serves the 2D network and the WebXR scene.

**State.** Entity Registry v1.1 seeded (people, organisations; a kind and
a description; entries marked unverified). 974 relations from the test
set, of which 354 current (576 superseded by later runs, 44 rejected). Relation rules built. No graph export, no co-mention layer, no
resolution of readers' entity names to registry entries beyond exact and
variant matches.

**Needs.** S3 (relations come from reading), S6 (held relations are not
published), S14 (the selection). Sérgio keeps the registry.

**Steps.**

1. Python: `archive export network`: nodes are entities (registry
   entries, and reader-proposed names that occur in at least two sources);
   edges are published relations, each with its type, attribution, quote,
   source and locator. Co-mention is a separate layer, built by software
   from entity matches in the same passage, never mixed with asserted
   edges. Output: one JSON file in the release folder.
2. Claude session: unresolved names. A report of frequent relation ends
   that match no registry entry, with their variants, for Sérgio: add to
   the registry, alias to an entry (a recorded decision), or leave.
   Embedding similarity may suggest, never merge.
3. Claude session: a static page with Sigma.js reading that JSON:
   filter by type, country, actor type; click an edge to see the quote
   and the source link.
4. Sérgio: the registry's judgement types (pseudo-professional body,
   influencer, network node) stay his; the page shows them as his.

**Gate.** The plan's Phase 5 gate: no edge without a located quote;
co-mention its own layer. A test fails the export if any edge lacks a
usage with `evidence_state = 'located'`.

**Estimate.** Sessions 10 h. Sérgio 2 h on names.

**Risks.** One person under several labels splits a node: the alias
report is the answer, not automatic merging. A defamatory edge from one
model's misreading: held relations are never exported; consequential
types (`CONSEQUENTIAL_TYPES`) are priority 1 in review.

**Brief.**

> Goal: `python -m archive export network --release DIR`: write
> `network.json` (nodes, asserted edges with quote, source, locator and
> attribution; a separate `co_mentions` list) from the records the
> release selection allows (`archive/release.py`, S14). Add a static
> `site/network.html` using Sigma.js from a pinned CDN version that loads
> the JSON, filters by relation type and shows the quote on click.
> Constraints: no edge without a located usage (test it); held, disputed
> or rejected relations never exported; no source text beyond the quote.
> Done when: tests pass and the page renders the test-set relations.

### S9. Embeddings and archive search

**Goal.** Sérgio finds anything: by words, by who speaks, term, practice,
side, place, date; and by meaning. Embeddings suggest; they are never
evidence.

**State.** `passage_fts` (FTS5) exists and only `match.py` uses it. The
embedding model is configured (`config/models.yaml`, role `embedding`)
and `OllamaClient.embed` exists. No vector table, no `sqlite-vec`
dependency, no search command.

**Needs.** For facets: S3 and S5 output. For vectors: about 3–6 MS hours
for 97,537 passages (unmeasured; measure on 1,000 first). D15.

**Steps.**

1. Claude session, no MS: `python -m archive search "words" --speaker
   --term --practice --stance --language --country --from --to`: FTS5
   with facet filters, results as passages with source, locator and the
   statements and tags on them. This alone meets most of "everything can
   be found".
2. Claude session: a table for vectors. Either `sqlite-vec` (a new
   dependency; CLAUDE.md names it) or a plain table of float32 blobs
   with a brute-force scan (97,537 × 768 is fast enough in numpy, slow
   in pure Python). Recommendation: `sqlite-vec`, in a separate file
   `data/vectors.db`, so the archive database stays small and the
   vectors can be rebuilt.
3. MS: embed 1,000 passages, measure seconds, then all text passages.
   Text only before the demo.
4. Python: hybrid search (FTS5 rank and vector rank fused), and
   "passages like this one". Suggestions of concept attachments go to a
   list for review; nothing is attached by similarity.
5. Remove `'embedding'` from `usage.found_by`, or document why it stays.

**Gate.** Twenty questions Sérgio writes ("where is X said about Y"):
the right passage is in the top 10 for at least 16. Search answers in
under 2 seconds.

**Estimate.** MS 6 h. Sessions 10 h. Sérgio 1 h (the questions).

**Risks.** Embedding non-English text with one model: measure Portuguese
and Spanish queries separately. A suggestion mistaken for evidence: the
interface labels suggestions and never counts them.

**Brief.**

> Goal: `python -m archive search`: full-text search over `passage_fts`
> with facet filters (language, speaker, stance, term, practice, cluster,
> source country, date range), printing passages with source title, URL,
> page or timecode, and the statements and tags on each. Put the query
> logic in `archive/search.py` so the interface (S7) can call it. Tests
> on a fixture database. Constraints: no model; standard library; FTS5
> query input escaped; results show review state. Done when: tests pass
> and ten example queries are documented in `docs/SEARCH.md`.

### S10. The lexicon product

**Goal.** A multilingual lexicon: each term with its definition, who uses
it and who holds it at a distance, with located quotes, dates and
sources. Version 0.1 with a DOI by 12 February (the plan's Phase 4).

**State.** Concepts and labels seeded; 151,271 exact matches; the view
`term_use` gives use from word and stance. No export. Nothing tagged in
the real archive.

**Needs.** S5 (term modes), S3 with D1 (stances), S14 (release), S15
(permission to deposit).

**Steps.**

1. Python: `archive export lexicon`: per concept: labels by language,
   definition and its source, counts of uses and descriptions by stance
   and by year, up to N quotes each for "uses" and "describes" with
   speaker, source, locator, and how far each got (both taggers;
   reviewed; sampled). First dated use from publication dates, marked
   with its certainty.
2. Claude session: a static page per term, from that JSON.
3. Sérgio: the definitions drafted by models are marked as such; he
   confirms or rewrites the 20 terms the demo shows first.
4. Deposit v0.1 (S14).

**Gate.** The plan's Phase 2 gate, now with tags: 20 English concepts
with usages from at least 3 sources each. Every quote shown opens its
source at the right place.

**Estimate.** Sessions 8 h. Sérgio 3 h.

**Risks.** A term shown as someone's own word because of a wrong stance
(finding 10): show only uses from statements both readers agree on, and
show the stance beside each quote.

**Brief.**

> Goal: `python -m archive export lexicon --release DIR`: write
> `lexicon.json` from the view `term_use`, the concept tables and the
> release selection, and static pages `site/lexicon/<slug>.html`. Each
> quote carries speaker, stance, source title, URL, archive URL, page or
> timecode, and its verification level. Constraints: only records the
> release selection allows; model-drafted definitions are labelled; no
> excerpt longer than the stored quote. Done when: tests pass on a
> fixture and 20 terms render from the dev trial copy.

### S11. Catalogue/wiki via Sanity

**Goal.** A public catalogue of sources, actors and terms. Sanity is the
publishing layer only, filled one way from each release.

**State.** Not built. An old Sanity export is in `seeds/` as a seed.
The Sanity connector did not load in the cloud session.

**Needs.** S14. D8.

**Steps.**

1. Sérgio: D8. Recommended: static pages for March, Sanity after.
2. Python: `archive export catalogue`: one JSON document per source,
   entity and concept, with stable ids (`_id` from the archive id).
3. Static route: pages generated beside the lexicon pages.
4. Sanity route (after the demo): a schema that mirrors those documents;
   a push script that creates or replaces by `_id` and deletes what the
   release no longer holds; never read back. Editors do not edit there.

**Gate.** A second push of the same release changes nothing. Nothing in
Sanity that is not in the release.

**Estimate.** Sessions: static 6 h; Sanity 12 h. Sérgio 1 h.

**Risks.** Hand edits in Sanity diverge from the archive: the push
overwrites; say so in the studio's description.

**Brief.**

> Goal: `python -m archive export catalogue --release DIR`: JSON
> documents for sources (title, publisher, author, dates with certainty,
> language, country, URL, archive URL, counts), entities and concepts,
> with stable ids, and static pages from them. Constraints: only the
> release selection; researcher-copy PDFs and their text never exported;
> no personal data beyond what the release allows (S15). Done when: tests
> pass and the pages build from a fixture release.

### S12. Documentary packets (timecoded clips)

**Goal.** For a film or a lesson: a packet of passages and excerpts on a
theme, each with source, speaker, and page or timecode; and clips.

**State.** Excerpts (`asset`) built: testimony, prayer script,
conversion script, course structure, counter-sermon. Passages from
captions carry `time_start`. No media stored, no transcription, no clip
record. The plan's gate: 10 located passages and 1 real clip.

**Needs.** S5 (excerpts), S6 (their review), D16 (rights and the clip
route).

**Steps.**

1. Python: `archive export packet --theme QUERY`: the excerpts and
   statements that match (search, S9), as Markdown and JSON: text,
   speaker, source, URL, timecode as `hh:mm:ss`, and for YouTube a link
   with `&t=`.
2. Sérgio: D16. For the demo: one clip, cut by hand with ffmpeg from a
   source whose rights he has cleared, its start and end taken from the
   packet; stored outside the repository; its checksum and timecodes
   recorded as a researcher decision.
3. After the demo: transcription (faster-whisper) for media without
   captions; a `clip` record.

**Gate.** The plan's: 10 located passages, 1 real clip, each opening at
the right time in its source.

**Estimate.** Sessions 5 h. Sérgio 3 h.

**Risks.** Rights to moving images of named people: his decision, with
UiB advice (S15). Caption timecodes drift: check the clip by eye.

**Brief.**

> Goal: `python -m archive export packet --theme "QUERY" --out DIR`:
> Markdown and JSON listing matching excerpts (`asset`) and statements
> with speaker, stance, source, URL, archive URL, page or timecode
> (`hh:mm:ss`, and a timed link for YouTube). Constraints: published
> records only unless `--internal`; full excerpt text only in the
> internal packet. Done when: tests pass and a packet builds from the
> dev trial copy.

### S13. The WebXR exhibition scene

**Goal.** The demo: a scene of 50–300 nodes that runs on a Quest and on
a projector, where a visitor moves through actors, terms and quotes,
each traceable to its source.

**State.** Nothing built. No design in the repository.

**Needs.** S8 (network JSON), S10 (terms), S12 (excerpts), S14. Sérgio's
design: what a visitor sees and does (D17). A Quest for testing.

**Steps.**

1. Sérgio: D17: one page: the visitor's path, what a node shows, the
   tone, what must never be shown (names of private people, slurs
   without frame).
2. Claude session, by mid-December: a spike with test-set data: a static
   page (A-Frame, pinned version) that loads `network.json`, lays out
   150 nodes, shows a quote panel on select, works with a controller and
   with mouse and keyboard. Test on the Quest browser now, not in March.
3. Python: `archive export scene`: the curated subset: chosen nodes,
   their edges, 1–3 quotes each, positions computed offline.
4. Claude sessions: iterate with Sérgio weekly from mid-January.
5. Projector mode: the same page, no headset, an auto-tour.
6. Freeze content on 19 March; rehearse on the real hardware.

**Gate.** The plan's Phase 6: runs on Quest and projector. 72 frames a
second on the Quest with 300 nodes. Every quote panel shows source and
date. Works offline from a folder.

**Estimate.** Sessions 30 h. Sérgio 15 h (it is his artwork).

**Risks.** Left to the end, as exhibitions are. The spike in December is
the mitigation. Text legibility in VR: test type sizes early.

**Brief.**

> Goal: a WebXR spike. One static page `site/scene/index.html` using
> A-Frame from a pinned CDN version; loads `scene.json` (nodes with
> label, kind, position; edges; quotes with source and date); renders up
> to 300 nodes; selecting a node shows a readable panel with one quote
> and its source; works with Quest controllers and with mouse and
> keyboard. Use the test-set relations as data. Constraints: static
> files only, works offline; no source text beyond stored quotes. Done
> when: it runs in a desktop browser at 60 frames a second with 300
> nodes, and the steps to open it on a Quest are in `docs/SCENE.md`.

### S14. Releases

**Goal.** One frozen selection of records that every product is built
from: GitHub (code and static site), Zenodo or DataverseNO (dataset with
a DOI), Sanity one way.

**State.** Tables `release` and `release_record` exist; no code writes
them; `release_record` cannot hold tags or excerpts; nothing sets
`public_eligible`; no licence file (findings 2 and 3).

**Needs.** D18 (the selection rule, licences, repository). S15.

**Steps.**

1. Claude session: `archive/release.py`: the selection, in one place.
   Proposed rule, for Sérgio (D18): a statement is in when both readers
   agree or a review confirmed it, and it is not rejected; a relation
   when both readers agree, or a rule or review confirmed it; a tag or
   excerpt when both taggers agree and no check failed, or a review
   confirmed it, **and** its statement is in; a kind of tag only if its
   sampled error rate is under the agreed limit; nothing from a
   researcher-copy PDF, a bot-check page or a quarantined copy.
2. Schema: a new version allowing `tag`, `asset` and `decision` in
   `release_record`; a migration test.
3. Python: `python -m archive release --name v0.1 --out
   data/releases/v0.1/`: sets `public_eligible` as recorded decisions,
   writes `release` and `release_record` with row hashes and the head of
   the decision chain, then JSONL per table, a SQLite subset, a
   manifest, the difficulties register, the method note (models,
   prompts, error rates).
4. Products build only from that folder (S8, S10, S11, S12, S13).
5. Sérgio: deposit by hand the first time; write the DOI back.
6. Add `LICENSE` and `CITATION.cff` (D18).

**Gate.** Two builds from the same release are byte-identical. A test
proves no held, disputed or rejected record can enter. The release
verifies against its own hashes.

**Estimate.** Sessions 12 h. Sérgio 2 h.

**Risks.** Publishing what should not be public: the selection is the
only door, and S15 decides what may pass. Copyright in long excerpts:
metadata and short quotes open; excerpt text restricted (D18).

**Brief.**

> Goal: `archive/release.py` and `python -m archive release`. Implement
> the selection rule of S14 step 1 as one function with tests for each
> clause (a held relation, a disputed statement, a tag on a held
> statement, a researcher-copy source: all excluded). Bump the schema so
> `release_record` accepts `tag`, `asset` and `decision`. Write the
> release folder (JSONL per table, manifest with hashes, decision chain
> head, method note). Constraints: no record changes except
> `visibility`, each as a recorded decision; reproducible output
> (sorted, canonical JSON); nothing committed from `data/`. Done when:
> tests pass and a release builds from the dev trial copy.

### S15. Governance

**Goal.** The project may lawfully and decently hold and publish what it
publishes. Serves every release and the demo.

**State.** Nothing in the repository (finding 8). Possibly handled
outside it; ask Sérgio first.

**Needs.** Sérgio, UiB (data protection officer, research data
support), Sikt. No session can decide this; sessions draft.

**Steps.**

1. Sérgio: what exists already: Sikt notification? DMP? ethics advice?
2. Claude session: draft a data management plan from the repository:
   what is collected, from where, how stored, who has access, what is
   published, retention, backups. He edits and submits.
3. Sérgio with UiB: the legal basis for special-category data of named
   people (public statements; research safeguards), and the rule for:
   private individuals named by others, people who withdrew testimony,
   minors, the dead.
4. Claude session: what the rule needs in the schema: a flag on `entity`
   (public figure, private individual, minor), a suppression record that
   every release honours, a takedown route on the public site.
5. Licences (D18). The YouTube captions decision recorded in the DMP.
6. Defender: never change its settings; never restore from quarantine.
   `verify_objects.py` (S0) lists what it removed; take it from the
   backup, or list the source for Sérgio. No automatic re-fetch: the same
   capture is quarantined again (`acquire.py`).
7. The public repository: D12.

**Gate.** Before any public release or exhibition: the Sikt and UiB
answers are on file; the suppression list is honoured by a test; the
takedown route exists.

**Estimate.** Sessions 6 h of drafting. Sérgio 8–12 h, with waiting
time at UiB and Sikt: start in November.

**Risks.** The answer comes late or says no to naming some people: the
demo then shows organisations, public figures and anonymised testimony.
Build the suppression flag early so this is a setting, not a rebuild.

**Brief.**

> Goal: a draft data management plan in `docs/DMP_DRAFT.md`, from the
> repository only: data types (public web pages, PDFs, captions,
> researcher's PDFs, model outputs, decisions), sources and how they are
> fetched (including the recorded decision on YouTube captions),
> storage and backup, access, special categories of personal data and
> the questions for the data protection officer, what is published and
> under which licence, retention, takedown. Mark every point that needs
> Sérgio's or UiB's answer as a question. Constraints: draft only;
> decide nothing; plain language. Done when: the draft exists and lists
> its open questions at the top.

### S16. Evaluation and accountability

**Goal.** Every method change is measured the same way; the holdout
stays unseen by whoever tunes; every decision can be traced and undone.

**State.** Two test sets (`evaluation/`): readings (dev and holdout) and
tags (no split field; matched by key). `testset score` works for both.
The decision chain is solid. Weak holdout (finding 11); no generic undo
(finding 5); two expectations "lost with the tactics"; three holdout
term expectations written under option A.

**Needs.** D7 (fr-p42), D19 (the drafted tag expectations and the two
lost ones). A session that has never tuned, for holdout work.

**Steps.**

1. Claude session (not a tuner): add `"split"` to each entry of the tags
   file, copied from the test set; make `testset show` and `score`
   refuse holdout entries unless `--split holdout` is given, and print a
   warning then.
2. The same session: re-check the three holdout term expectations under
   option B; propose changes to Sérgio without showing them to any
   tuning session.
3. Sérgio: D19, D7.
4. Claude session: `python -m archive undo --run RUN_ID --reason`: for a
   reading or tagging run, mark its records rejected with a researcher
   or orchestrator decision each, and restore what it superseded; for a
   review run, call `review.undo`. Tests.
5. Claude session: docs: README status; mark `HANDOFF.md` and
   `KICKOFF.md` as historical; propose to Sérgio the edits that bring
   `docs/PLAN.md` in line with his later decisions (clusters, no harm
   tags, dates).
6. Weekly: the sample (S6); per kind, the error rate, kept in
   `docs/ACCOUNTABILITY.md` as a dated table.
7. Before each release: one holdout run, by a non-tuner, reported as
   numbers only.

**Gate.** A tuner following the documented commands cannot print a
holdout expectation. `undo --run` restores a fixture exactly (tested).

**Estimate.** Sessions 8 h. MS 2 h per holdout run. Sérgio 2 h.

**Risks.** The holdout is public and small: 9 of the 28 entries. The
five of 10 November have been seen by tuning sessions (`docs/TESTSET.md`),
so only the four of 12 November are an unseen test, and a session once
saw three holdout term expectations by mistake (`docs/TAGGING.md`). Treat
its score as a smoke test, and Sérgio's weekly sample as the real measure.

**Brief.**

> Goal: protect the holdout and add a generic undo. (1) Add a "split"
> field to every entry of `evaluation/tags_2026-11-12.json` by key from
> `evaluation/testset_2026-11-10.json`, without printing any expectation;
> make `archive testset show` and `score` skip holdout entries unless
> `--split holdout` is given. (2) `python -m archive undo --run RUN_ID
> --reason TEXT` as in S16 step 4, with tests. Constraints: you must not
> have tuned any prompt in this session; never print holdout
> expectations; decisions are append-only. Done when: tests pass.

---

## 5. Critical path and schedule

### The budget

| | Supply to 31 March | Demand | Verdict |
|---|---|---|---|
| Mac Studio, one model at a time | 135 days: 3,240 h; about 2,750 h at 85% | Reading en 700; tagging en 425; check 10–16; pt and es about 80 both passes; embeddings about 6; dev and holdout runs about 30; in all about 1,270 h | Fits once. A second full reading does not fit comfortably |
| Review sessions | Not a fixed number; each hour is Enterprise seat use | Demo set about 35 h. Whole English corpus about 650 h at today's agreement | The demo set fits. The whole corpus does not, before the demo |
| Sérgio | About 19 weeks | Weekly sample 1½ h; decisions 1 h a week; governance 8–12 h; lexicon and vocabulary 5 h; the scene 15 h; "for the researcher" packets: unbounded unless capped | Fits only if the researcher packets are capped and the scene starts in December |
| Building | Claude sessions, in parallel with the Mac Studio | About 130 session hours for S0, S3 step 1, S4, S7–S16 | Fits if started now; it is the lane nobody has started |

The plan's dates give English reading 7 December to 22 January. The Mac
Studio is idle until then, about 480 hours. Start the demo set as soon
as the prompts are decided, and the bulk run right after.

### The demo-first route

The full corpus, reviewed, cannot be public by 31 March. This route
keeps every decision of Sérgio's and still makes the demo:

1. **A demo set of about 300 sources** (5% of English), chosen by
   Sérgio with a software shortlist: the 24 test-set sources, sources of
   the Entity Registry's European organisations, the richest sources by
   matches, the recordings that can give a clip, key parliamentary
   records. Read, tagged and **reviewed in full**: about 35 MS hours for
   reading, 21 for tagging, 35 session hours of review.
2. **The rest of the corpus is read and tagged in priority order behind
   it.** What both models agree on can be published without review, as
   decided. What is held stays unpublished and searchable internally.
   The review queue for the bulk is worked by priority (consequential
   relations, actor types, then the rest) as far as session hours go.
   No method decision changes; the public lexicon is thinner until the
   queue is worked.
3. **Minimum quality for the demo:** every item shown is agreed by both
   models or confirmed in review; the sampled error rate of agreed tags
   is measured and under the limit Sérgio sets (proposed 10% per kind; a
   kind above it is not shown); every quote opens its source.
4. **Cut or deferred to after the demo:** Sanity (static catalogue
   instead); image, audio and video embeddings; transcription beyond one
   clip; French; the full review of the bulk queue; functions if their
   agreement stays near 23%.

Options that would shrink the bulk queue but change a decision are in
section 7 (D20). None is assumed here.

### Gates and dates

| Gate | What must be true | Target |
|---|---|---|
| G0 | Backup restores; job lock; trial copies sorted (S0) | 20 Nov |
| G1 | Prompts frozen: D1 decided; tag-v4 and the actor rules measured and D4 decided; tag lists reviewed (D5) or frozen as they are; corpus selection built (S3 step 1); holdout run by a non-tuner (only its four entries of 12 November are unseen) | 27 Nov |
| G2 | Demo set read, tagged, checked | 4 Dec |
| G3 | Demo set reviewed; first weekly samples; error rates per kind | 18 Dec |
| G4 | WebXR spike runs on the Quest with test-set data (S13 step 2) | 18 Dec |
| G5 | Release v0 from the demo set builds; search and the interface's first screen work | 15 Jan |
| G6 | English bulk read and tagged (about 1,070 h from 27 Nov at 20 h a day: 54 days) | 22 Jan |
| G7 | Portuguese and Spanish read and tagged; text embeddings | 31 Jan |
| G8 | Lexicon v0.1 deposited with a DOI, if S15 allows | 12 Feb |
| G9 | Network and search (the plan's Phase 5) | 26 Feb |
| G10 | Scene content frozen; rehearsal on the hardware | 19–26 Mar |
| Demo | | 31 Mar |

The critical path: **D1 and D4 → G1 → bulk run → G6**, and beside it
**S14 (release selection) → S8, S10 → S13**, and beside both **S15**.
The Mac Studio lane and the building lane do not wait for each other.
S15 waits for other people: start it first.

### Lanes that run in parallel

| Lane | Needs the Mac Studio | Subprojects |
|---|---|---|
| A. Model jobs, one at a time | yes | S3, S5, S9 step 3, dev and holdout runs |
| B. Software, Claude sessions | no | S0, S3 step 1, S4, S7, S8, S9 steps 1–2, S10–S14, S16 |
| C. Review sessions | no | S6 |
| D. Sérgio | no | decisions, weekly sample, S15, S13 design |

### The first week on the Mac Studio

1. **S0**: backup to the destination Sérgio names, `verify_objects`,
   the job lock. Nothing long runs before a backup exists.
2. **S5 steps 2–5** (step 1, the actor rules alone, was done on 17
   November): tag-v4 on dev (93 minutes), the check, the queue, the
   agreed-tags sample. Bring Sérgio D4 with numbers.
3. **Send Sérgio the first batch of decisions** (section 7: D1, D2, D3,
   D10, D11, and the governance question of S15 step 1), and start lane
   B with S3 step 1 (corpus selection) and S14 (release selection).

---

## 6. Orchestration protocol

**Sessions.** One Claude Code session per subproject step, started with
its brief. Each works on its own branch (`s5-measure-tag-v4`) or git
worktree. Never two sessions writing the same files: `archive/tag.py`,
`archive/review.py`, `archive/__main__.py` and `archive/schema.sql` are
the crowded ones; give each to one session at a time and merge before
the next starts. A session ends with: tests passing, a commit, and a
three-line note of what changed and what was measured. You merge to
`main` only what Sérgio has adopted when it is a method change; code
that changes no method (exports, the interface, scripts) you may merge
after tests pass.

**The Mac Studio rule.** One resident model at a time. Only the
orchestrator starts model jobs, and only through the wrapper:

```bash
#!/bin/sh
# scripts/ms_job.sh: one model job at a time
mkdir -p data/reports
exec flock data/ms.lock sh -c 'echo "$(date -u +%FT%TZ) start: $*" >> data/reports/ms_jobs.log; "$@"; rc=$?;
  echo "$(date -u +%FT%TZ) end rc=$rc: $*" >> data/reports/ms_jobs.log; exit $rc' sh "$@"
```

(macOS has no `flock` by default: `brew install flock`, a small package,
or use `mkdir data/ms.lock.d` as the lock. Ask before installing.)
Sub-sessions never call `read`, `tag`, `check`, `titles` or an embedding
job themselves; they hand you the command. Before a job: `ollama ps`
shows nothing loaded. Long jobs run in `tmux` or under `nohup`, with
output to `data/reports/`. Check `read --status` and the stuck-runs
query (section 1) twice a day.

**Trials.** Only on copies in `data/scratch/`, never on
`data/archive.db`. Name the copy after the trial. Keep the copies a
later comparison needs; list the rest for Sérgio. Check `df` before
making one (0.45 GB each today; more after the bulk run).

**Measuring a change before adoption.**

1. State what should improve and what must not get worse, as numbers.
2. Tune on the dev split only: `--split dev` on every command.
3. Measure with the committed scripts (`testset score`,
   `scripts/tagging_report.py`), on a copy.
4. Never edit a prompt, seed or rule version that a run has used. Write
   the next version. The run records the prompt and vocabulary hashes.
5. Write the result in the subproject's doc, dated, with what failed.
6. Ask Sérgio (below). If he adopts it: a session that did not tune and
   has not seen the tuning conversation runs the holdout once and
   reports numbers only. If the holdout disagrees with dev, stop.

**Holdout secrecy.** Whoever tunes never sees holdout expectations.
Never open, print or list either file in `evaluation/` without first
keeping only entries whose split is "dev". The tags file has no split
field yet (S16 step 1): filter by key through the test-set file. Say
this in every brief that touches evaluation.

**Asking Sérgio.** Batch questions; at most once a day, better twice a
week. Each question: one line of context, the options, the measured
numbers, your recommendation, what happens if he does not answer (the
default, and whether work is blocked). Method changes wait for his yes.
He has the final say. Record his answer in `CLAUDE.md` ("Decisions
already made") with the date, in the same commit as the change.

**Cadence.**

| When | What |
|---|---|
| Twice a day | `read --status`; the stuck-runs query; the job log; disk |
| Daily | A five-line status in `data/reports/status-DATE.md`: jobs done, failures, queue size, blocked on |
| Weekly | Backup and restore check; Sérgio's sample (30–50); error rates per kind; this document's statuses; a short report to Sérgio |
| Before a bulk run | Backup; tests; model digests recorded; prompts frozen |
| Before a release | Holdout run by a non-tuner; `verify_objects`; the selection tests |

**Undo and rollback.** Every model record carries its `run_id`. A
review run: `review undo --run RUN --reason "…"`. A reading or tagging
run: run again (the new run supersedes the old), or after S16 step 4
`archive undo --run`. A dedupe merge: `dedupe.undo_merge`. A bad code
change: `git revert`; the database is not in git, so the backup of the
day before is the rollback for data. Never delete rows; never rewrite
the decision table (triggers refuse it).

**Never.**

- Commit source texts, PDFs, media, the database, model outputs,
  packets, credentials, or long quotations in docs. The repository is
  public.
- Write a model name outside `config/models.yaml`.
- Run a trial on `data/archive.db`.
- Show holdout expectations to a tuning session.
- Change Microsoft Defender settings or restore what it quarantined.
- Download a model without asking Sérgio and stating its size.
- Merge entities by embedding similarity; merge anything silently.
- Mark a reviewer's ruling as researcher-verified.
- Add approvals, ledgers or recovery machinery: a failed job is retried
  once, then marked failed, and the batch goes on.
- Change a decision listed in `CLAUDE.md`. Propose; he decides.
- Delete trial copies or anything in `data/` without his yes.

---

## 7. Decisions waiting for Sérgio

Ordered by what they block. "Rec." is this session's recommendation.

| # | Decision | Options | Rec. | Blocks |
|---|---|---|---|---|
| D1 | The reading prompt for the bulk run | (a) read-v4, the default; (b) read-v5: position on a ban, software sets the stance; (c) read-v6 | Already measured on dev (`docs/MODELS.md`): automatic-check failures, both readers reconciled, read-v4 3, read-v5 5, read-v6 7 (6 without `fr-p42-no-authorship`); read-v6 was not adopted. So the choice is read-v4, best on the checks, against read-v5, written to fix stance: the stance checks found read-v4 wrong 8 times in 8 on defenders of change efforts, and under option B stance decides whose word a term is. **Rec.: before deciding, compare the stances of those 8 statements in the read-v5 trial copy (`data/scratch/read-v5.db`; Python, no model). If read-v5 gets most of them right, read-v5; if not, read-v4, with stance checks kept in the review queue.** Either way: one holdout run by a non-tuner | G1, the whole run |
| D2 | Order of the bulk run and the demo set | (a) as queued; (b) demo set of about 300 sources first, then by match density | **(b)**; he picks from a software shortlist | G2 |
| D3 | The checker | (a) Gemma 4 12B with check-v2, as proposed; (b) none | **(a), on practices and excerpts only** (its cluster flags were false alarms 10 times in 12) | S5 |
| D4 | Adopt tag-v4 and the actor rules | After the numbers of S5 step 5 | The actor rules `actors/v2` are measured (S5 step 1: no right type lost, 155 wrong dropped, a 10% smaller held-tag load, agreement unchanged): adopt them alone now. tag-v4 after its dev run | G1 |
| D5 | Vocabulary: the actor-type list; where Spiritual Healing, Deliverance and Pastoral Care divide; prayer script against conversion script; ROGD-Frame under two clusters; Recruitment Frame against Pseudo-Diagnostic; is affirming theology that never mentions change efforts a counter-sermon | His rulings, a sentence or two each | Rule on the three practice boundaries first (practices agree 43%); ROGD-Frame under one cluster for tagging, cross-referenced; counter-sermon only when it answers change efforts, as defined | G1 (or freeze as is) |
| D6 | Own-voice rule in reconcile for author or publisher labels (78 disputes) | (a) build as in S4; (b) leave to review | **(a)**, measured on dev first | Review load |
| D7 | Test set conflicts: the fr-p42 nested-quote rule; `fr-p42-no-authorship` against the amendment rule | His ruling on what the right reading is | The expectation follows the plan's test case (never ministry authorship); the amendment rule gets an exception for quoted wording | S16 |
| D8 | Sanity for March | (a) Sanity now; (b) static catalogue for the demo, Sanity after | **(b)** | S11 |
| D9 | The weekly sample: day, size, mix | 30–50 | 40: researcher packets first, 20 random rulings, 10 agreed-unreviewed records | S6 |
| D10 | Backup destination | External disk; UiB storage; both | **Both** | G0 |
| D11 | Clean-up of `data/scratch` (21 GB) | Keep all; keep the three copies S5 needs and the newest reading trial | Keep those, delete the rest after a list he approves | Disk |
| D12 | Public repository: the April glossary's context quotes, the Sanity seed and registry entries naming people, the quarantined wiki copy | Keep; trim; move to `data/` | Move the quarantined file and the context quotes out of the public repository; keep names of public organisations and public figures | S15 |
| D13 | His PDF collection | Copy to the Mac Studio, when | Before G1, so they are read with the rest | S1 |
| D14 | Interface stack | (a) local page, Python standard library; (b) Datasette for reading plus a small write page; (c) a framework | **(a)** | S7 |
| D15 | Vectors | `sqlite-vec` in a separate file; plain blobs | **`sqlite-vec`, separate file** | S9 |
| D16 | The one clip: which source, which rights | His choice | A source whose rights are clear (his own recording, or an institution's open licence) | S12 |
| D17 | The scene: the visitor's path and what is never shown | One page from him | By 5 December, so the spike has a target | S13 |
| D18 | Release rule, licences, repository | Rule as in S14 step 1; code MIT or Apache-2.0; data CC BY 4.0 for metadata and short quotes, excerpt text restricted; Zenodo or DataverseNO | The rule as written; Apache-2.0; CC BY 4.0 with restricted excerpts; **DataverseNO** (UiB support, Norwegian research data) unless he prefers Zenodo's reach | S14 |
| D19 | The drafted tag expectations; the two "lost with the tactics"; three holdout term expectations under option B | Review; accept the fitting labels | Accept "euphemism, described" and the counselling-rebrand practice as passes; a non-tuner brings him the three holdout ones | S16 |
| D20 | The bulk review queue (about 230,000 packets) | (a) as decided: everything one by one, however long it takes, bulk items unpublished meanwhile; (b) one by one for relations, actor types, practices and excerpts; clusters and functions from one tagger stay internal until agreement improves; (c) one-tagger tags judged by sample per kind and tagger | **(a) for the demo set now; decide (b) after tag-v4 is measured.** (c) contradicts the calibration (57% right) | After the demo |
| D21 | Speaker lists for recordings without labels | Per recording, with `archive speakers` | Demo-set recordings first; he names the speakers, a session enters them | S4 |
| D22 | Parliamentary records: members speaking are "politician or legislator" by rule | Build as a proposal that goes to review; or leave to the taggers | The actor rules are measured (S5 step 1): actor packets on dev barely fell (49 → 48) and per-speaker agreement stayed at 60%, so build it, as a proposal that goes to review, measured on dev | S5 |
| D23 | Functions from the lexicon (proposal 5) | Build; not now | After the demo set | S2 |
| D24 | Governance: what exists already (Sikt, DMP, ethics) | – | Answer this week; it has the longest waiting time | Every release |

---

## 8. Risks

| Risk | Likelihood | Effect | Mitigation |
|---|---|---|---|
| Disk fault or Defender removes files, no backup | Medium | The project | S0 first; weekly restore check |
| A prompt changes after the bulk run starts | Medium | Up to 700 MS hours | G1; changes after it only as later passes on stored readings |
| Review queue never ends | High | Thin public lexicon; pressure to skip review | Demo set reviewed in full; bulk by priority; D20 after tag-v4 |
| Governance answer late or restrictive | Medium | No public release | Start S15 now; suppression flag; a demo that can run with public figures only |
| Products left to March | High | No demo | Lane B starts this week; WebXR spike by 18 December; release v0 by 15 January |
| Wrong stance makes a promoter's word "describes" (or the reverse) | High with read-v4 | Lexicon errors in public | D1; stance checks; show stance beside each quote |
| Agreed tags are wrong more often than assumed | Unknown | Errors published without review | The agreed-tags sample before G1; a limit per kind |
| A defamatory relation published | Low | Harm to a person; to the project | Held relations never exported; consequential types first in review; attribution and quote on every edge; takedown route |
| Holdout seen by a tuner | Medium | Scores mean less | S16 step 1; rely on Sérgio's weekly sample |
| Mac Studio down for days | Low | The slack shrinks (about 1,400 h of it) | Priority order means the best sources are done first |
| Model or Ollama update changes behaviour mid-run | Low | Mixed readings | Record digests; no updates during the bulk run |
| Sérgio's time | High | Decisions and samples pile up | Batched questions with defaults; capped researcher packets; the interface |
| One orchestrator loses context | Certain, over months | Repeated work, drift | This document kept true; daily status files; decisions dated in `CLAUDE.md` |
| Two sessions edit the same file | Medium | Lost work | One owner per crowded file; worktrees; merge before the next |
