# sogice-archive-database — instructions for Claude Code

A research archive of SOGICE (sexual orientation and gender identity change
efforts) material, worldwide with a European focus. Sole researcher: Sérgio
Galvão Roxo (UiB). One evidence store feeds six products: lexicon, catalogue/wiki,
2D network, WebXR exhibition scene, archive search and documentary packets.

Above all it is Sérgio's research tool: a searchable archive of everything he
has encountered on the topic, and the basis for the socio-educational work he
makes from it (lexicon, wiki, VR documentary, teaching). Design for finding
and citing things, and for reuse in outputs not planned yet (`docs/PLAN.md`,
Purpose).

The full plan is the "SurvivingSOGICE — Project Redesign" doc (9 Nov 2026).
`docs/PLAN.md` is the working summary. Read it before planning work.

## The core rule

A fact is stored once, as a **usage**: a short verbatim quote located by
software in a preserved source, with speaker, stance, language and date. Every
lexicon entry, tag, edge and documentary clip points back to usages. Nothing
is published as plain fact; edges read "Source X states…" with the quote.

## How the work is split

| Who | Does what |
|---|---|
| Python, no model | Fetch, preserve (Wayback), extract text, detect language, cut passages, full-text index, exact matching of seed vocabularies |
| Local models (Ollama on the Mac Studio) | Two independent readers: Gemma 4 31B and Qwen 3.8 27B, reconciled by software. Embeddings: EmbeddingGemma 2 (text, image, audio, video) |
| Claude agents (Claude Code / Cowork, Enterprise seat) | Review results and samples, score against the test set, propose prompt/vocabulary/rule changes as commits, organise the review queue. No API pipeline |
| Sérgio | Samples 30–50 AI decisions a week; final say on method changes |

The model quotes; software locates. Models never compute offsets.

## Decisions already made (9 Nov 2026)

- New project; SQLite + content-addressed files are the source of truth.
- Sanity is the publishing layer only, filled one way from each release.
- Languages: detect all; process English, then Portuguese, then Spanish.
- No human gate before publication. Every AI decision records actor, model,
  prompt version, quote, reason and audit verdict, and can be undone.
- All public testimony, from every side, is in scope with attribution.
- 10 Nov 2026: two readers. Gemma 4 31B and Qwen 3.8 27B each read every
  source; `reconcile` records agreement, which models read it and when;
  disagreements are disputed, not published. Reading time is budgeted per
  1,000 characters (15 s per reader), not per source. The Mac Studio may run
  continuously. See `docs/ACCOUNTABILITY.md`.
- 10 Nov 2026: a relation only one reader proposed is held from publication
  until reviewed. The review queue (`docs/REVIEW.md`) is worked by Claude
  Code with `/review-queue`; rulings are recorded decisions, never
  researcher-verified; low-confidence ones go to Sérgio.
- 10 Nov 2026: the review trial's seven rules adopted. Relations that need
  no judgement are decided by software after `reconcile` (`archive/rules.py`:
  ends must be names in the text; role lines are employed_by; a bare
  profession or a speaker label states nothing; amendments are authorship).
  Prompt read-v5 asks for the speaker's position on a ban, and software sets
  the stance from it. See `docs/REVIEW.md`.
- 11–12 Nov 2026: tags are made in a separate pass after reading, on the
  readers' statements, by two taggers (the reading pair): actor, terms
  used, tactics, practices, functions, each marked as used or only
  described. No harm tags. Excerpts (testimonies, prayer and conversion
  scripts, course structures, counter-sermons, new terms) are kept whole.
  Everything passes a verification step. See `docs/TAGGING.md`.
- Releases: GitHub (code, static site); Zenodo or DataverseNO (dataset + DOI).
- Target: working exhibition demo by 31 March 2027.

## Boundaries

- This repository is public. Never commit source texts, PDFs, media, the
  database, model outputs or credentials. They live under `data/` (ignored).
- One resident model at a time on the Mac Studio (64 GB).
- No signed approvals, budgets, ledgers or recovery machinery. A failed job
  is retried once, then marked failed; the batch continues.
- No silent merges: exact or variant matches attach; other merges are
  recorded AI decisions; embedding similarity alone never merges.
- Model names live only in `config/models.yaml`.

## Where old material lives

- Old repo: `~/surviving-sogice-ingest` (retired). Live old code is on its
  branch `codex/archive-evidence-foundation`; read with
  `git -C ~/surviving-sogice-ingest show codex/archive-evidence-foundation:<path>`.
  `docs/PORTING.md` lists what to bring over.
- Old project data: `~/Library/Application Support/SurvivingSOGICE/projects/surviving-sogice/`
  (source queue `state/source_queue.db`, registry `state/knowledge.db`).
- Evaluation sources (French report 4501, Exodus, Harvest covenant):
  `~/surviving-sogice-ingest/Transfer/SurvivingSOGICE_Complete_Package/`.
  Use locally; do not commit.

## Conventions

- Python 3.10+, standard library first; SQLite with FTS5; `sqlite-vec` for vectors.
- Every module has tests; `pytest` must pass before a commit.
- Write plain-language docs in `docs/`; keep one plan, not many.
