# Plan (working summary, 9 Nov 2026)

## Pipeline

1. **Preserve (no model).** Fetch each queued URL; look up or capture on
   Wayback; store bytes by SHA-256; extract text (trafilatura for HTML, docling
   for PDF); detect language per passage (lingua); cut gap-free passages of
   about 1,500 characters; build FTS5 index. Audio/video go to a transcription
   queue (faster-whisper) with timecodes.
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
4. **Suggest (embeddings).** EmbeddingGemma 2 over passages, page images,
   audio/video clips (≤ 5 min, with timecodes) and concept descriptions. Used
   for search and for suggesting concept attachments. Never evidence.

## The local–Claude loop

1. Local model processes a batch overnight.
2. A Claude agent reads the results, the test set and a random sample.
3. It proposes prompt / vocabulary / rule changes as commits, with reasons.
4. The anchor set is re-run locally; keep the change only if scores improve.
5. Disputed cases go to a review queue worked through with Sérgio.

## Data model

Source → Version → Section → Passage → Usage → (Concept | Entity) → Relation;
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

## Schedule and gates

| Phase | Dates | Gate |
|---|---|---|
| 0 Repo and setup | 10–27 Nov | Repo, model config, backup working |
| 1 Preserve corpus | 16 Nov–23 Dec | 90% of text sources stored with text, language, passages |
| 2 Seed and match | 30 Nov–18 Dec | 20 English concepts with usages from ≥ 3 sources |
| 3 Local reading, English | 7 Dec–22 Jan | 20-source test set: 90% quotes located; speaker/stance filled; package test cases pass; ≤ 5 min/source |
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
