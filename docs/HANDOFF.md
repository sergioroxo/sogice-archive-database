# Handoff to Claude Code — 9 November 2026

Read this file, then `CLAUDE.md`, `docs/PLAN.md` and `docs/PORTING.md`.
The full reasoning is in the "SurvivingSOGICE — Project Redesign" doc:
https://claude.ai/artifact/Birwc68ZN83XGaFgzuVaqV

## 1. Where things stand

| Item | State |
|---|---|
| Repo | `~/sogice-archive`, branch `main`, 2 local commits, **not yet pushed** to `github.com/sergioroxo/sogice-archive-database` |
| Code | `archive/atomic_io.py`, `passages.py` (gap-free citation units), `quote_locator.py`, `relationship_vocabulary.py` (20 types), `schema.sql` (20 tables, FTS5), `db.py` (connect, init, hash-chained decisions), `config.py` + `config/models.yaml` |
| Tests | 115 passing on Python 3.10 (standard library only) |
| Done 9 Nov (evening) | Venv (Python 3.14), exact Ollama tags, `archive/ollama.py`, `scripts/check_models.py`, results in `docs/MODELS.md` |
| Done 9 Nov (night) | Phase 1 code (import, fetch, Wayback, DOI, captions, extraction, passages, language, attach-pdfs); full preservation run started; Phase 2 seeds and exact matching, gate passed (60 English concepts in 3+ sources) |
| Done 10 Nov | Phase 3 test set: 20 entries, expectations, scorer (`docs/TESTSET.md`) |
| Done 10 Nov | Work keys and `dedupe` (22 duplicate groups merged); bot-check `recheck` (7 flagged); thin-page and quarantined-file review lists |
| Done 10 Nov | Phase 1 complete: 93.4% of 5,062 text sources with text (gate 90%); 0 pending. Phase 2 rerun: 105 English concepts in 3+ sources, 151,262 usages |
| Done 10 Nov | Exodus apology ("I Am Sorry"): the 22 Jun 2013 capture was quarantined by Defender; the 14 Jul 2013 capture (same text, 8,865 chars; no `cdn.widgetserver.com` script) stored and indexed; the report marks the old copy as replaced. loveinaction.org still on the review list |
| Done 10 Nov | Phase 3 reader (`archive/read.py`, `docs/READING.md`): prompt read-v3 (one list per passage), gap pass, cut-off salvage, supersede on re-read, heartbeat + `read --status`, time gate 10 s per 1,000 chars. Dev entries (short sources): 12B fails 15 expectations, 31B fails 3; 12B ≈ 7 s and 31B ≈ 13 s per 1,000 chars (English corpus ≈ 215 h vs ≈ 390 h) |
| Not yet done | Choose the bulk reading model (Sérgio); read the long dev sources and the holdout; audit pass; garbled-OCR detection; researcher interface |
| Old system | Retired. Its UI on :8503 and LiteLLM gateway may still run; do not depend on them |

## 2. Environment facts

- Mac Studio M2 Ultra, 64 GB (about 52 GB usable by Metal). One resident model at a time.
- Python: the old venv `~/surviving-sogice-ingest/.venv` is Python 3.11; create a new venv for this repo.
- Ollama: `http://127.0.0.1:11434`. Installed per October records: `gemma4:12b-mlx`, `gemma4:31b-mlx`, `qwen3-embedding:8b`, `bge-m3`, `embeddinggemma-2:740m` (never used for inference; its digest differed between two Ollama servers — check only one Ollama runs).
- Free disk was about 37 GB in October. Check before fetching the corpus.
- Old data (read-only): `~/Library/Application Support/SurvivingSOGICE/projects/surviving-sogice/`
  - `state/source_queue.db`: 6,923 rows; 4,420 webpages, 1,496 video, 541 PDF, 265 YouTube, 99 docx, 94 social, 7 audio; 1,564 are Wayback URLs; 1,729 domains; no language column; titles empty.
  - `state/knowledge.db`: old registry (2,050 records, mostly unusable: 89% language `und`, 0 resolved edges). Do not import wholesale.
  - 80 July texts were copied into the old corpus (`logs/july-text-import-20261031.json`).
- Evaluation sources: `~/surviving-sogice-ingest/Transfer/SurvivingSOGICE_Complete_Package/` (French report text, Exodus PDF/text, Harvest covenant). Never commit them.
- Housekeeping: delete `~/surviving-sogice-ingest/.git/index.lock` (empty, left by a review agent).

## 3. Decisions (owner, 9 Nov 2026)

- Local Gemma models do all bulk reading. Claude (Claude Code / Cowork, Enterprise seat) works as an agent: reviews samples, scores, edits prompts and rules. No API pipeline.
- Embeddings: EmbeddingGemma 2, local (text, image, audio, video; 768 dims; 8K context ≈ 5.5 min audio or 58 frames per input).
- No human gate. Every AI decision is recorded and reversible. Edges are shown only as "Source X states…" with the quote; the public site needs a correction/takedown route.
- All public material, including testimony from all sides, is in scope with citation. The old testimony form is retired.
- English first, then Portuguese, then Spanish. Detect every language.
- Sanity = publishing layer only. Datasets to Zenodo or DataverseNO with DOI. Code and static site on GitHub.
- Demo by 31 March 2027.

## 4. Work queue

### Phase 0 — finish (this session)
1. `python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest`.
2. `ollama list`; put exact tags into `config/models.yaml`.
3. Write `scripts/check_models.py`: one JSON-returning prompt to each reading model; embed one text, one image, one short audio clip, one short video with EmbeddingGemma 2. Record what works in `docs/MODELS.md`.
4. Push: `git push -u origin main`.

### Phase 1 — preserve the corpus (16 Nov–23 Dec)
1. `archive/queue_import.py`: copy rows from the old `source_queue.db` (open read-only, `mode=ro`) into `source`. Keep the old row id, URL, type hints, review flags; normalise Wayback URLs (keep original URL and capture date separately).
2. Port acquisition (`project_acquisition.py`: URL validation, fetch, Wayback lookup/fetch; `acquire.py`: blocked-page detection; `source_depot.py`: content-addressed storage). Polite fetching: per-domain delay, retries once, then `failed`.
3. Port text extraction (`preprocess.py` URL and PDF paths; trafilatura for HTML, docling or pdftotext for PDF with form-feed page breaks). Store canonical text as a `source_version`.
4. Language detection per passage (lingua-py). Cut passages with `archive/passages.py`; fill `passage` + `passage_fts`.
5. Audio/video: queue only (yt-dlp captions first, faster-whisper later). Keep timecodes.
6. CLI: `python -m archive preserve --limit N`, resumable, idempotent by URL + content hash.
7. Gate: 90% of text sources stored with text, language and passages. Report failures by domain.

### Phase 2 — seed and match (30 Nov–18 Dec)
1. `archive/seeds.py`: load Lexicon v2.1 as concepts (definition_source = seed), labels per language; Ontology facets as concept schemes; the 44-entry tactic tree and 14 practices from `seeds/sanity_seed_2026-05-05.json`; entities from `Entity_Registry_v1.1.md`; GOV.UK glossary as an external scheme linked by `exactMatch`.
2. Merge the 210 terms in `seeds/sogice_glossary_2026-04-03.json` as labels of existing concepts where they are translations (about 80 collapse into ~9 concepts); the rest become `proposed` concepts. Each merge is a recorded decision.
3. Known seed problems: 45 Lexicon cross-references point to missing entries (add them as proposed concepts: Deliverance, Exorcism, Conversion Therapy, SOCE, GICE, Cristoterapia, Blanchard Typology…); duplicate actors (IFTCC ×2, TLC ×3); "Network:" pseudo-organisations; parser leftovers saved as terms; the vocabulary CSV has a second table from row 525. Do not use `seeds/quarantine/`.
4. `archive/match.py`: exact and variant matching of every label across `passage_fts`; each hit becomes a `usage` with `found_by = exact_match`. Count repeated page furniture separately.
5. Gate: 20 English concepts with usages from at least 3 sources.

### Phase 3 — local reading, English (7 Dec–22 Jan)
1. One structured call per short document (< ~20k chars), per section for long ones. Output: short verbatim quotes (≤ 300 chars) + unit id for terms/senses, typed entities (person, organisation, **publication**, law, event), speaker / quoted party / intermediary / stance, tactics/practices/harm from the facet lists, claims, relations from `relationship_vocabulary.py`. Software locates every quote (`quote_locator.py`) and drops what it cannot find.
2. Audit pass with the 31B model: speaker, target and quote fit; verdict stored as a decision.
3. Lessons from October trials: cap output tokens (a 31B call once looped for 14 minutes to 32k tokens of "-S"); about 93% of tokens were re-sent input, so do not resend the whole document per family; retry once, then mark failed and continue.
4. 20-source test set first: include the Transfer package cases (`docs/PLAN.md`, test cases), a non-English source, a duplicate, a PDF needing page locators and a deliberate failure. Gate: 90% of quotes located, speaker and stance filled, ≤ 5 min per web source.

### The local–Claude loop (from Phase 3)
Local model runs a batch overnight → a Claude Code session reads results, the test set and a random sample, and scores them → proposes prompt/vocabulary/rule changes as commits with reasons → re-run the anchor set; keep the change only if scores improve → disputed cases go to a review queue worked through with Sérgio.

### Later phases
Phase 4 audit, samples and lexicon v0.1 with DOI (by 12 Feb) · Phase 5 2D network (Sigma.js) and hybrid search (by 26 Feb) · PT/ES runs (Feb–Mar) · Phase 6 WebXR scene and documentary packets (by 26 Mar) · demo 31 Mar 2027.

## 5. Rules that must not slip

- Public repo: nothing under `data/`, no source texts, PDFs, media, databases, model outputs or keys in git.
- Models never compute offsets. A quote that cannot be located is not a usage.
- No automatic merges from embedding similarity.
- No control machinery (signed approvals, budgets, ledgers). Keep it simple.
- Small commits; `pytest` green before each.
