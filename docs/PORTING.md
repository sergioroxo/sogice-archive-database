# What to bring over from the old repository

Old repo: `~/surviving-sogice-ingest`, branch `codex/archive-evidence-foundation`.
Read a file with:

```bash
git -C ~/surviving-sogice-ingest show codex/archive-evidence-foundation:<path>
```

Port one module at a time, with its tests, and strip anything that depends on
signatures, budgets, ledgers, leases, sealed plans or Streamlit.

| Old path | Lines | Action |
|---|---:|---|
| `runner/pipeline/citation_units_v2.py` (+ `tests/test_citation_units_v2.py`) | 415 | Copy as is |
| `runner/pipeline/quote_locator.py` (+ `tests/test_phase1_extraction_contract.py`, locator parts) | 287 | Copy as is |
| `runner/pipeline/relationship_vocabulary.py` | 102 | Copy; add `cites`, `responds_to`, `translates`, `co_signatory_of`, `trains` |
| `runner/pipeline/knowledge_registry.py` | 2,092 | Take the schema, state enums, append-only decisions, snapshots; rewrite the rest small |
| `runner/pipeline/retrieval_index.py`, `retrieval.py` (+ tests) | 482 | Keep FTS5 and rank fusion; replace numpy vectors with `sqlite-vec` |
| `runner/pipeline/preprocess.py` | 1,359 | Take URL, PDF and video extraction paths |
| `runner/pipeline/transcripts.py` | 258 | Copy |
| `runner/pipeline/project_acquisition.py` | 1,013 | Take URL validation, fetch, Wayback lookup/fetch |
| `runner/pipeline/source_depot.py` (+ tests) | 406 | Content-addressed storage; copy core |
| `runner/pipeline/source_identity.py` | 516 | DOI and identity helpers |
| `runner/pipeline/acquire.py` | 783 | Challenge/blocked-page detection |
| `runner/pipeline/core_research.py` | 1,104 | Take only family instructions and payload schemas |
| `runner/pipeline/embed.py` | 104 | Rewrite for EmbeddingGemma 2 via Ollama |
| `runner/pipeline/atomic_io.py` (+ tests) | 72 | Copy |
| `runner/pipeline/govuk_glossary.py` | — | Loader reference for the GOV.UK glossary |
| `docs/delivery/PRODUCT_CONTRACTS.md` | 29 | Copy into `docs/` |
| `scripts/backup_project.sh` | 70 | Adapt for the new data folder |

Do not port: `app.py` and `*_ui.py` (Streamlit), `factory_*`, `workflow_*`,
`batch*`, budgets, manifests, `single_host*`, `run027*`, `run028*`, Sanity
upload/seed clients, `studio/`.

## Seed files (in `seeds/`, copied 9 Nov 2026)

- `SOGICE_Lexicon_v2.1.md` — concept backbone
- `SOGICE_Ontology_v3.0.md` — facets and relations
- `Entity_Registry_v1.1.md` — seed entities
- `govuk_conversion_therapy_glossary_2021.json` — external scheme (exactMatch)
- `sanity_seed_2026-05-05.json` — 44-entry tactic tree, 14 practices
- `sogice_glossary_2026-04-03.json`, `sogice_vocabulary_2026-04-03.csv` — 210 discovered terms to merge (CSV has two tables; second header at row 525)
- `quarantine/Term_More.md` — unchecked; keep only verified slurs

Known problems in the seeds: 45 Lexicon cross-references point to missing
entries; duplicate actors (IFTCC ×2, TLC ×3); "Network:" tags turned into fake
organisations in the Sanity seed; parser leftovers saved as terms.
