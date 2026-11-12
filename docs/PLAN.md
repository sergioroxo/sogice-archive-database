# Plan (working summary, 9 Nov 2026)

## Purpose (Sérgio, 11 Nov 2026)

The archive is first of all Sérgio's research tool: a searchable record of
all the material he has encountered on SOGICE, each item kept with its
source. It is also the basis for the socio-educational work he will make
from it: the lexicon, the wiki, the VR documentary and exhibition, and
teaching material. Some of that work is not planned yet. So:

- **Everything can be found.** Search works on the full text and on what
  the archive knows about it: who speaks, which term, tactic or practice,
  which side, where and when.
- **Everything can be cited.** Each item carries its source, its quote and
  where in the source it sits. It can then go into a film, a lesson or an
  exhibit and still be checked.
- **Products are built from releases.** They are never edited by hand in
  their own copy, so a new output can be built later from the same store.

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
   **Fingerprints (no model):** each preserved page's own code is scanned for
   identifiers only its owner or web manager can put there (analytics and
   donation accounts, charity and tax numbers, declared profiles) and for
   its self-description; sites sharing one are reported, with platform and
   provider identifiers set apart (`docs/FINGERPRINTS.md`).
2. **Match (no model).** Search every seed concept variant and entity name
   across all passages. Each hit is a usage marked `exact_match`. Count
   repeated navigation/promotional blocks separately.
3. **Read (local model).** One structured call per short document, or per
   section for long ones. Returns short verbatim quotes for statements
   (speaker, quoted party, intermediary, stance), entities with type
   (person, organisation, publication, law, event), claims, and relations
   from the closed vocabulary. Tactics, practices, harm and new terms are
   not asked of the readers: they are tagged in a separate step (3b).
   Software locates quotes, drops what it cannot find, resolves endpoints.
   Two readers (decided 10 Nov): two different local models read every
   source independently with the same prompt; `reconcile` pairs their
   statements by quote position and records a confidence level per record
   (both agree / one reader / readers disagree), which models read it and
   when. Disagreements are disputed and go to review, not to publication.
   A difficulties register (what the readers found hard) ships with each
   release. See `docs/ACCOUNTABILITY.md`.
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

   **3b. Tag (local models, separate pass; Sérgio, 11–12 Nov 2026).** After
   reading and reconcile, two taggers (the reading pair) give each
   statement its actor, terms used, clusters (since tag-v2, instead of the
   tactics), practices and functions, each marked as used or only
   described by the speaker. They also mark excerpts to keep whole
   (testimonies, prayer and conversion scripts, course structures,
   counter-sermons, new terms). Software compares them and a third model
   checks agreed items (`archive/check.py`). What the two taggers do not
   settle (one tagger only, opposite modes, agreed but flagged by the
   check) is reviewed one by one in the review queue before publication
   (Sérgio, 12 Nov). A term counts as the speaker's own word in the lexicon
   only when the statement endorses change efforts; a critic's plain use is
   "describes" (Sérgio, 12 Nov; software, view `term_use`). Document facets
   come from software first. See `docs/TAGGING.md`, which also has the dev
   trials: tagging takes about two-thirds of reading time.
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
5. Software rules decide the relations that need no judgement (role lines,
   ends that are not names, amendments; `archive/rules.py`). Disputed cases
   and relations only one reader proposed go to the review
   queue (`docs/REVIEW.md`): a Claude Code session rules on them with a saved
   prompt (`/review-queue`), blind to which model wrote which reading, on a
   quote from the source; unsure cases go to Sérgio, who also samples the
   rulings. Each review run ends with a patterns note: recurring rulings
   become rules (in the prompt or in software) once tested on the test set.
   Later (Phase 4), a local pass with the lexicon and the entity registry as
   context can settle the mechanical ones first.

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
| 3 Local reading, English | 7 Dec–22 Jan | 20-source test set: 90% quotes located; speaker/stance filled; package test cases pass; ≤ 15 s per 1,000 characters (decided 10 Nov: time scales with length; the 31B reads) |
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
