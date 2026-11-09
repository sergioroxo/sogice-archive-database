# Next Claude Code session — paste this as the opening prompt

Read `CLAUDE.md`, `docs/PLAN.md` and `docs/PORTING.md`.

Already done (9 Nov 2026, built in Cowork): package layout, `pyproject.toml`,
`archive/atomic_io.py`, `archive/passages.py`, `archive/quote_locator.py`,
`archive/relationship_vocabulary.py`, `archive/schema.sql`, `archive/db.py`,
`archive/config.py`, `config/models.yaml`; 115 tests passing.

Finish Phase 0 on this Mac:

1. Create a venv, `pip install -e ".[dev]"`, run `pytest`.
2. Run `ollama list`. Put the exact tags for Gemma 4 12B, Gemma 4 31B and
   EmbeddingGemma 2 into `config/models.yaml`.
3. Write `scripts/check_models.py`: send one short text to each reading model
   (expect JSON back), and embed one text, one image, one short audio clip and
   one short video with EmbeddingGemma 2. Report which inputs work.
4. Commit and push to `origin main`.

Then start Phase 1 (preserve the corpus): read the old queue at
`~/Library/Application Support/SurvivingSOGICE/projects/surviving-sogice/state/source_queue.db`
(read-only), import its rows into `source`, and port the fetch, Wayback and
text-extraction code listed in `docs/PORTING.md`. Store data under `data/`.

Work in small commits. Ask before deleting anything outside this repository.
