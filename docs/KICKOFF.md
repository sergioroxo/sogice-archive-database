# First Claude Code session — paste this as the opening prompt

Read `CLAUDE.md`, `docs/PLAN.md` and `docs/PORTING.md`. Then do Phase 0:

1. Create the package layout: `archive/` (Python package), `tests/`,
   `config/models.yaml`, `data/` (gitignored), `seeds/`, `docs/`.
2. Port `atomic_io.py`, `citation_units_v2.py`, `quote_locator.py` and
   `relationship_vocabulary.py` with their tests. Remove dependencies on the
   old control machinery. Make `pytest` pass.
3. Write the new SQLite schema (`archive/schema.sql`) for: source, version,
   section, passage, usage, concept, concept_label, entity, relation, claim,
   decision, run, release. Base it on the old `knowledge_registry._SCHEMA`;
   decisions record actor (model or researcher), action, reason, audit verdict
   and previous state, append-only.
4. Write `config/models.yaml` with reading (gemma4 12B), hard cases/audit
   (gemma4 31B) and embeddings (EmbeddingGemma 2), read from one place.
5. Check Ollama: list installed models; confirm EmbeddingGemma 2 accepts
   text, image, audio and video inputs; report what works.
6. Stop and summarise what was built, what failed, and the next step
   (Phase 1: preserve the corpus from the old `state/source_queue.db`).

Work in small commits. Ask before deleting anything outside this repository.
