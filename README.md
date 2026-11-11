# SOGICE Archive Database

An evidence-based archive of material on sexual orientation and gender identity
change efforts (SOGICE). Every lexicon entry, tag, network edge and documentary
clip links back to a located quote in a preserved public source.

Products: multilingual lexicon, catalogue and wiki, 2D network, WebXR scene,
archive search and documentary research packets.

Researcher: Sérgio Galvão Roxo, University of Bergen.

Status: Phase 1, preserving the corpus (November 2026). See `docs/PLAN.md`.

## Running it

Source files, the database and reports live in `data/` (never committed).

```bash
python3.14 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest
python scripts/check_models.py          # local Ollama models (docs/MODELS.md)
python -m archive import-queue          # old source queue -> sources and targets
python -m archive preserve --limit 50   # fetch, store, extract text, passages, language
python -m archive report                # gate figure and failures by domain
python -m archive attach-pdfs ~/sogice-pdfs --dry-run   # match your own PDFs to sources (then without --dry-run)
python -m archive recheck --dry-run     # find stored pages that are really bot checks (then without --dry-run)
python -m archive dedupe --dry-run      # list sources that are the same video/DOI (then without --dry-run)
python -m archive seed                  # load seed vocabularies, entities, merge decisions
python -m archive match                 # find every seed label in every passage (usages)
python -m archive testset prepare       # queue, attach and index the 20 test-set sources
python -m archive testset show fr-4501 --grep "Torrents"   # read an entry's passages with pages/times
python -m archive read --testset --split dev             # Phase 3: local reading (docs/READING.md)
python -m archive read --status                          # runs in progress; exit 1 if one has stalled
python -m archive read --testset --both                  # two readers, reconcile, relation rules (docs/ACCOUNTABILITY.md)
python -m archive speakers src_… --set "Name=host"       # who speaks in a recording without labels (read-v5)
python -m archive fingerprints                          # identifiers in page code shared by sites (docs/FINGERPRINTS.md)
python -m archive queue-add URL … --origin NAME         # add pages to the source queue
python -m archive titles                                # repair poor source titles (docs/TITLES.md)
python -m archive titles --reissue                      # re-read first pages whose publisher title-v1 set
python -m archive authors                               # source authors: DOI records, page code, first pages (docs/AUTHORS.md)
python -m archive review prepare                         # review queue; then /review-queue in Claude Code (docs/REVIEW.md)
python -m archive testset score         # check expectations and the Phase 3 gate
```
