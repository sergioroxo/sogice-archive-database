# Tags (draft, 11 November 2026)

**Status: draft.** On 11 November Sérgio decided that tags are made in a
separate step after reading, not by the readers. The rest of this page is a
proposal until it has been tested on the dev set and he has ruled on the
open questions at the end.

## What tags are for

Tags are how the archive is searched and reused (`docs/PLAN.md`, Purpose):

- find every place a tactic, practice or harm is described;
- show how a term is used, by whom and on which side;
- filter the network and the exhibition;
- pick material for a lesson or a film.

A tag never stands alone. It says something about one statement the readers
have already recorded, with its quote, speaker and stance:

> "In this quote, *speaker X* **uses** tactic Y" or "In this quote, *speaker X*
> **describes** practice Z".

So a tag can always be shown as "Source S states …", like every other record.

## What exists now

| Kind | Vocabulary | What the archive holds |
|---|---|---|
| Terms | Lexicon v2.1 (292 terms) | 151,271 exact matches of 181 terms, found by software. They have no speaker or stance. |
| Tactics | 44 (ontology v3.0, with definitions) | Nothing. They describe moves, not words, so exact matching finds none. |
| Practices | 14 (ILGA-Europe aligned) | Nothing |
| Functions | 12 | Nothing |
| Harm | Not settled (see the questions at the end) | Nothing; not seeded |
| Target | Orientation, gender identity, gender expression, sex characteristics; minor or adult | Nothing; not seeded |
| Document facets | Genre, source stance, actor type, country, era, religious tradition | Empty on all 6,804 sources. Language is set on 6,378 and date on 3,801. |

## Three kinds of tag, made three ways

### 1. Terms in use: software, no model

A term match that falls inside a statement takes that statement's speaker
and stance. "ex-gay" inside a quote by Alan Chambers becomes a use of
"ex-gay" by Alan Chambers, with the stance the readers recorded.

The link is worked out when a release is built and is not stored a second
time: a fact is stored once. A match outside any statement stays a plain
match: it can be searched, but it is not credited to anyone.

The lexicon then shows each term's uses grouped by side:

- those who promote the practice;
- those who criticise it;
- laws;
- research.

This follows the ontology's stance profile. A term quoted by a critic is
never counted as the critic's own use.

### 2. Statement tags: a separate pass by local models

This runs after reading and `reconcile`, one section at a time. The tagger
is given:

- the section's text;
- its statements, numbered, each with its quote, speaker and stance;
- the closed lists, with each item's one-line definition from the
  ontology.

For each statement it returns:

- **tags** from the lists (tactic, practice, function, harm, target), each
  with a **mode**:
  - `uses`: the speaker uses this tactic, or does or offers this practice;
  - `describes`: the speaker names, reports or criticises it.

  A critic describing "pray the gay away" is not tagged as using it.
- **cue**, optionally: the few words that show the tag, copied exactly.
  Software locates them inside the statement's section. A cue it cannot
  find is dropped.
- **new term candidates**: a word or phrase the text uses as a special term
  that is not in the lexicon. These become proposals for the lexicon, not
  entries.

Harm is tagged only when a statement reports harm to someone: the type, and
the severity only if the text states it. It is never inferred. Target is
tagged only when the text says who is aimed at.

Clusters (C1–C7) are not tagged. Software derives them from the tactics,
using the ontology's mapping.

**Why tag statements and not passages.** Tags then come with a speaker and
a stance, so "Source S states …" stays true. And the difference between
using and describing a tactic is about a voice, which a passage does not
have.

**Why a separate pass.**

- The reading stays as tested.
- Tags can be made again, with a new vocabulary version or a new tactic,
  without reading the corpus again (about 700 machine hours for English).
- The tagger writes short labels, not quotes, so it should be much faster
  than reading.
- It can be tested on its own.

**Two taggers, as for reading** (the 31B and Qwen 3.8 27B), compared by
software:

- tags both give are published;
- a tag only one gives is held for review;
- `uses` against `describes` is disputed.

A tag such as Groomer-Panic or Physical-Coercion says what a person or an
organisation does, so it gets the same care as a statement. Tags on a held
or disputed statement are held with it.

**Records.** A small table links a statement to a concept, with the mode,
the cue span, the run and a review state. Every tag is an undoable,
recorded decision. Each run records its prompt version and the vocabulary
version (ontology v3.0 and the seed date).

**Time.** Unknown until measured. A first guess is a third to half of
reading time: 230 to 350 hours for both taggers over the English corpus,
10 to 15 days of continuous running. Only one model fits in memory at a
time, so tagging cannot run at the same time as reading. It can follow the
reading batch by batch, but the machine time adds up either way.

### 3. Document tags: software first

| Facet | Where it comes from |
|---|---|
| Genre | The medium, the DOI record (journal article), the kind the titles step names (report, law, letter, statement…), recordings |
| Actor type, religious tradition | The publisher's entry in the Entity Registry (ministry, therapy practice, advocacy, academic, media, government…) |
| Country | The organisation's country, not the text's language (ontology rule): the Entity Registry, then fingerprints (a UK charity number means GB). A country-code domain is only a hint, marked inferred. |
| Source stance | From the readings: the stance of the source's own voice (statements by its author or publisher). If it is split, "mixed". The registry's pro/anti lists are a hint to check, never the answer. |
| Era | The publication date; when there is none, the capture date, marked as such |

What software cannot decide stays empty and goes on a review list for the
researcher interface. A model is used only where there is no other route,
as a recorded decision with a quote.

## Testing

- Add about 30 tag expectations to the dev entries. They are drafted by
  Claude agents from the stored text and reviewed by Sérgio. They include
  "must not" cases:
  - a critic describing a tactic is not tagged as using it;
  - the list of people heard in the French report carries no tags;
  - template wording in the Harvest covenant is not tagged as practice done
    to someone.
- New holdout entries get tag expectations too.
- Proposed gate: every tag expectation passes on dev and holdout. Phase 4
  then measures the sample error rate.

## Order of work

1. Seed the harm and target lists. Build the terms-in-use link and the
   document tags (software); report coverage.
2. Write prompt `tag-v1` and the tag table. Run both taggers on the dev set;
   measure time and score.
3. Sérgio decides on the open questions. Tagging then follows the English
   reading batch by batch.

## Open questions for Sérgio

1. Two taggers, as for reading, or one tagger plus a sampled audit (half
   the time)?
2. Which lists in the first pass? Proposed: tactics, practices, functions,
   harm and target, keeping only what passes on dev.
3. Harm vocabulary. The old Sanity tags mixed types (psychological,
   medical, faith-based) with severity (mild, moderate, severe) and a
   content warning (graphic); its severity tags are marked superseded.
   Which harm types and which severity scale?
4. The ontology's other kinds: evidence, migration, network, landmark, and
   assets such as testimony excerpts, prayer scripts and course structures.
   Now or later? The assets matter for the documentary and for teaching.
