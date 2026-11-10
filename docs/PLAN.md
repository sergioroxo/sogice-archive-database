# Plan (working summary, 9 Nov 2026)

## Pipeline

1. **Preserve (no model).** Fetch each queued URL; look up or capture on
   Wayback; store bytes by SHA-256; extract text (trafilatura for HTML, pdftotext
   for PDF with page breaks kept, standard library for DOCX); detect language per passage (lingua); cut gap-free passages of
   about 1,500 characters; build FTS5 index. Audio/video go to a transcription
   queue (faster-whisper) with timecodes.
   Dates come only from explicit page metadata (certainty 'inferred');
   scanned PDFs are flagged so their OCR text is treated with care.
   YouTube: metadata and description from the watch page (yt-dlp, no
   media) plus captions in the spoken language (never translations).
   YouTube's robots.txt disallows /api/, where captions live; Sérgio decided
   (9 Nov 2026) to fetch them for the selected public videos anyway. Direct audio/video
   files are counted for later transcription, not downloaded.
   Academic articles (URL holds a DOI, ~210 targets): registered title,
   journal and date from Crossref; when the publisher blocks us, the
   abstract (Crossref or OpenAlex) and any open-access copy, repository
   copies first. Short landing pages also get the PDF they declare
   (citation_pdf_url). Closed articles get full text from the researcher's
   own PDFs, named after their URLs: `attach-pdfs` matches them by file
   name, then DOI, then title (doubtful ones go to review), stores them as
   researcher copies and indexes them. They stay local, never published.
   Fetching rules: honest User-Agent, one request per host at a time (3 s
   apart, 4 s for the Internet Archive), robots.txt obeyed for live pages,
   no Save Page Now submissions. A blocked, parked or vanished live page
   falls back to the newest usable Wayback capture (flagged). Transient
   failures are retried once, then the target is marked failed.
   One work, one source: a URL's *work key* (YouTube video id or DOI) attaches
   new URLs to the source already holding that work; sources stored twice are
   merged by `dedupe` as recorded, undoable decisions (`source.merged_into`;
   nothing moved or deleted) and counted under their keeper. `recheck` runs
   today's bot-check detection over stored pages: a bot-check copy is kept,
   flagged `challenge_page`, never read, and its target is fetched again.
   Large pages with almost no extracted text (usually JavaScript-rendered)
   are listed in the preservation report for review, not fixed automatically.
2. **Match (no model).** Search every seed concept variant and entity name
   across all passages. Each hit is a usage marked `exact_match`. Count
   repeated navigation/promotional blocks separately.
3. **Read (local model).** One structured call per short document, or per
   section for long ones. Returns short verbatim quotes for: new terms and
   senses; entities with type (person, organisation, publication, law,
   event); speaker, quoted party, intermediary, stance; tactics, practices,
   harm from the facet lists; claims; relations from the closed vocabulary.
   Software locates quotes, drops what it cannot find, resolves endpoints.
   A local audit pass records a verdict per source.
   Completeness: the model receives every passage of every readable copy,
   in order, in sections of about 6,000 characters (a cut-off reply is read
   again as two halves, never shortened). Left out on purpose: blocks
   repeated on 3+ sources (furniture), word-for-word repeated passages,
   bot-check and quarantined copies. The model still chooses what to quote,
   so each run records the passages it quoted nothing from; long gaps are
   for review or a second pass. Text lost earlier, at extraction
   (JavaScript-drawn pages, two-column PDFs, chart data), is on the
   preservation report's review lists, not recovered by reading. The full
   text always stays stored and searchable; reading only adds to it.
4. **Suggest (embeddings).** EmbeddingGemma 2 over passages, page images,
   audio/video clips (≤ 5 min, with timecodes) and concept descriptions. Used
   for search and for suggesting concept attachments. Never evidence.

Phase 2 as built (9 Nov 2026): `archive/seeds.py` loads the Lexicon,
tactic tree, practices, functions, Entity Registry and GOV.UK glossary;
the April glossary goes through `seeds/merge_map_2026-11-09.json`
(model decisions with reasons, recorded and undoable). `archive/match.py`
turns every exact label hit into a usage; `seeds/match_rules.json` lists
labels left for context reading (ordinary words, identity words, short
acronyms). Reference terms are matched but do not count for the gate.

Phase 3 test set as built (10 Nov 2026): `evaluation/testset_2026-11-10.json`
(20 entries, 112 expectations, 5 holdout) and `archive/testset.py`
(prepare, show, score); see `docs/TESTSET.md`.

## The local–Claude loop

1. Local model processes a batch overnight.
2. A Claude agent reads the results, the test set and a random sample.
3. It proposes prompt / vocabulary / rule changes as commits, with reasons.
4. The anchor set is re-run locally; keep the change only if scores improve.
5. Disputed cases go to a review queue worked through with Sérgio.

## Data model

Source → Version → Section → Passage → Usage → (Concept | Entity) → Relation;
Source → Target (what to fetch: the live URL and each Wayback capture; a
fetched target becomes a Version);
plus Claim, Decision (AI or researcher), Run, Release. Every record keeps
evidence state, review state and visibility separate.

## Vocabulary

One SKOS-style concept scheme with Lexicon v2.1 as backbone; labels per
language; facets as concept schemes:

- Document level: genre, source stance, medium, actor type, country (ISO 3166),
  language (ISO 639), era, religious tradition.
- Passage level: tactic (44-entry tree), practice (14, ILGA-aligned),
  function (12), harm type and severity (separate), target (orientation,
  gender identity, expression, behaviour; minor/adult).

Merge the 210 April glossary terms into concepts (about 80 collapse into ~9).
Add the ~45 concepts that Lexicon cross-references point to but lack.
Quarantine `Term_More.md`.

## Products (all from one frozen release)

Lexicon · Catalogue/wiki (via Sanity) · 2D network (Sigma.js) · WebXR scene
(50–300 nodes, Quest + projector) · Archive search (FTS5 + vectors) ·
Documentary packets (timecoded clips). Dataset deposit with DOI per release.

## Researcher interface (internal)

A visual layer where Sérgio works on the data directly: fix mistakes,
settle review lists (doubtful PDF and title matches, failed sources),
attach his own files to sources, correct metadata, confirm or reject AI
decisions. Every action is recorded as a researcher decision and can be
undone. Until it exists, review lists are written as JSON reports; every
matching step keeps candidates, scores, rule and reason so the interface can
show them.

## Schedule and gates

| Phase | Dates | Gate |
|---|---|---|
| 0 Repo and setup | 10–27 Nov | Repo, model config, backup working |
| 1 Preserve corpus | 16 Nov–23 Dec | 90% of text sources stored with text, language, passages |
| 2 Seed and match | 30 Nov–18 Dec | 20 English concepts with usages from ≥ 3 sources |
| 3 Local reading, English | 7 Dec–22 Jan | 20-source test set: 90% quotes located; speaker/stance filled; package test cases pass; ≤ 10 s per 1,000 characters (decided 10 Nov: time scales with length) |
| 4 Audit, samples, lexicon | 11 Jan–12 Feb | Sample error rate measured; lexicon v0.1 deposited with DOI |
| 5 Network and search | 25 Jan–26 Feb | No edge without a located quote; co-mention its own layer |
| PT/ES runs | 1 Feb–12 Mar | — |
| 6 WebXR and documentary | 15 Feb–26 Mar | Runs on Quest and projector; 10 located passages, 1 real clip |
| Demo | 31 Mar 2027 | |

## Test cases (from the Transfer package, section 11)

- French report p. 42: Petit attributes healing wording to Torrents de Vie;
  never ministry authorship.
- French pp. 42–43: keep the turn across the page break.
- French pp. 48–49: prudence/time used in opposite arguments; not one sense.
- French pp. 45–46: numerical claims stay attributed.
- French pp. 63–64: list of people heard creates no endorsement edges.
- Exodus p. 1: "transforming" targets churches/community; umbrella closure
  separate from affiliates continuing.
- Harvest covenant: two samples kept separate; template roles, not testimony.
- Curriculum title attests wording without inventing a definition.
- Repeated site furniture counted apart from article body.
