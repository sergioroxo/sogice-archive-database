# Tags and excerpts

Tags are made in a separate step after reading, on the readers' statements,
not by the readers (Sérgio, 11 November 2026). The choices below are his
(12 November). How it works in detail is a proposal until it has been
tested on the dev set.

## Decisions (12 November 2026)

1. **Two taggers**: the reading pair, Gemma 4 31B and Qwen 3.8 27B. They
   read best on the test set, are installed, and we know their weak spots.
   Model names are in `config/models.yaml` (roles `tagging` and
   `tagging_second`).
2. **What is tagged**: actor, terms used, tactics, practices, functions.
3. **Harm is not tagged**: how much harm something did is too relative to
   label. Target (orientation, gender identity, minors) is not in the
   first pass either.
4. **Excerpts now**: testimonies, prayer scripts, conversion scripts,
   course structures and counter-sermons are kept whole, for the
   documentary and for teaching.
5. **A verification step**: everything must be checkable against the
   source.

## What tags are for

Tags are how the archive is searched and reused (`docs/PLAN.md`, Purpose):

- find every place a tactic or practice is described;
- show how a term is used, by whom and on which side;
- filter the network and the exhibition;
- pick material for a lesson or a film.

A tag never stands alone. It says something about one statement the readers
have already recorded, with its quote, speaker and stance:

> "In this quote, *speaker X* **uses** tactic Y" or "In this quote, *speaker X*
> **describes** practice Z".

So a tag can always be shown as "Source S states …", like every other record.

## What is tagged

All tags are given per statement. Both taggers see the same numbered
statements, so their answers can be compared one by one.

| Tag | List | Mode |
|---|---|---|
| Actor | Who the speaker is, as the text states it (below) | the speaker **is** it |
| Term used | The lexicon terms software found inside the statement (151,271 matches so far) | **uses** (the speaker's own word) or **describes** (quoted, in scare quotes, or criticised) |
| Cluster | The ontology's 7 clusters, C1–C7 (since tag-v2; tag-v1 offered the 44 tactics) | **uses** (argues this way) or **describes** |
| Practice | 14 (ILGA-Europe aligned) | **uses** (first-hand: does, offers, runs or went through it) or **describes** (reports, explains, promotes or criticises it from outside) |
| Function | 11 of the 12, now defined (slur, euphemism, pseudo-diagnostic…) | **uses** or **describes**, with the words that do it |

**Uses and describes.** A critic who says that a ministry "prays the gay
away" describes a practice, and is never tagged as using it. The lexicon
counts a term as someone's own word only when they use it; what "use"
means for a critic's plain wording is question 1 below.

**Actor types.** The tagger gives up to two, only when the text says them:
a job title, a self-description, or a role line ("Alan Chambers, Exodus
president"). The list (`seeds/tag_lists_2026-11-12.json`) is for Sérgio to
change:

- **People:** leader or founder, therapist or counsellor, pastor or clergy,
  someone who went through change efforts (by their own account),
  researcher, medical professional, politician or legislator, journalist,
  activist or campaigner, family member.
- **Organisations:** ministry, therapy practice, church or religious body,
  advocacy group, professional body, academic body, media outlet,
  government or parliament, political party.

The ontology's judgement types ("pseudo-professional body", "influencer",
"network node") are not asked of the taggers. They come only from the Entity
Registry, which Sérgio keeps.

**Terms in use**, without a model: software credits each term match inside
a statement to that statement's speaker, with its stance. The tagger adds
whether it is the speaker's own word. Matches outside any statement stay
plain matches: they can be searched, but are not credited to anyone.

**Tactics** are not tagged since tag-v2: the families overlapped too much
for two taggers to agree (Sérgio, 12 November). They stay in the
vocabulary, each tied to its cluster, for a later finer pass if needed.

## Excerpts

Some material is used whole, not as a short quote. The tagger names it and
copies its first and last words (at most 12 each). Software finds both in the
section and keeps the exact text between them, with its place in the
preserved copy.

| Kind | What |
|---|---|
| Testimony | A first-person account of going through, giving or leaving change efforts |
| Prayer script | The words of a prayer said for or over someone to change them |
| Conversion script | Scripted steps or dialogue for a session, group or covenant |
| Course structure | A programme's modules, steps or schedule |
| Counter-sermon | Affirming theology that answers change efforts |
| New term | A word or phrase used as a special term that is not in the lexicon (a proposal for the lexicon, never an entry) |

The ontology's other excerpt kinds are already covered:

- numbers are claims;
- network links are relations;
- legislative quotes are statements in legislative sources;
- a term's first use is worked out from dates.

Images and video frames come later, with embeddings.

## Verification

Everything published must be checkable: anyone can go from a tag or an
excerpt to the exact words in the preserved copy, and see who decided it
and how it was checked. Four layers, each recorded on the item:

1. **Found in the source (software).** The statement quote, the cue, the
   term and the excerpt are each the exact slice of the preserved text at a
   recorded place (page or timecode). That copy has a checksum and its
   Wayback link. Anything software cannot find is dropped.
2. **Two taggers.**
   - A tag both give, with the same mode, is kept.
   - A tag only one gives is held.
   - "Uses" against "describes" is disputed.

   Excerpts are paired by overlap, as statements are. Tags on a held or
   disputed statement are held with it.
3. **Checked (the extra layer).** Some agreed items get one more short
   question, from a checker that did not tag them:
   - every excerpt;
   - every "uses" tag of a tactic or practice: what someone argues or does.

   The checker sees only the item, its speaker and the definition: "Does
   this show X? Answer yes or no, and copy the words that show it." Those
   words must be found inside the item. A "no", or words that cannot be
   found, sends the item to the review queue. Which checker is to be tried
   on dev: Qwen 3.6 35B and Gemma 4 12B are installed. If neither checks
   well, the check falls to Claude in the review queue.
4. **Review and samples.**
   - Claude Code works the queue: held, disputed and failed checks.
   - Sérgio samples each week.
   - Each kind of tag gets a measured error rate. A kind above the agreed
     rate is not published until it is fixed.

The products show how far each item got (for example "both taggers, checked,
sampled"), and link it to its source.

## Document tags: software first

| Facet | Where it comes from |
|---|---|
| Genre | The medium, the DOI record (journal article), the kind the titles step names (report, law, letter, statement…), recordings |
| Actor type, religious tradition | The publisher's entry in the Entity Registry; otherwise the actor tags of the source's own voice |
| Country | The organisation's country, not the text's language (ontology rule): the Entity Registry, then fingerprints (a UK charity number means GB). A country-code domain is only a hint, marked inferred. |
| Source stance | From the readings: the stance of the source's own voice (statements by its author or publisher). If it is split, "mixed". |
| Era | The publication date; when there is none, the capture date, marked as such |

What software cannot decide stays empty and goes on a review list for the
researcher interface.

## Records

- Tags go in a table `tag`: the statement (a usage), the concept, the mode,
  the optional cue span, the run and a review state.
- Excerpts go in a table `asset`: the kind, the version and the span in its
  text, the speaker, the run and a review state.
- Each is proposed, compared and checked as recorded decisions, and can be
  undone. Runs record the prompt version and the vocabulary version.

## Time

Unknown until measured. A first guess is a third to half of reading time:

- 230 to 350 hours for both taggers over the English corpus;
- the checker's short questions on top.

Only one model fits in memory at a time. Tagging follows the reading batch
by batch; the machine time adds up either way.

## Testing

- **Dev expectations.** About 30 tag and excerpt expectations on the dev
  entries, drafted by Claude agents from the stored text and reviewed by
  Sérgio. They include "must not" cases:
  - a critic describing a practice is not tagged as using it;
  - the list of people heard in the French report carries no tags;
  - the Harvest covenant is a conversion script, template wording, not a
    testimony.
- **New holdout entries** get them too.
- **Proposed gate:** every expectation passes on dev and holdout, and the
  check layer's error rate is measured.

## Order of work

1. Tables `tag` and `asset`, the actor list, prompt `tag-v1`, the tagging
   pass and the comparison of the two taggers.
2. Run both taggers on dev, measure time, score against the expectations.
3. The checker: try the candidates on dev.
4. Terms in use and the document tags (software).

## Questions from writing the dev expectations (12 November 2026)

A Claude agent drafted 34 tag and excerpt expectations for 13 dev entries
(`evaluation/tags_2026-11-12.json`), from the stored text, before any
tagging. Writing them raised seven questions. Sérgio settled six the same
day; prompt `tag-v2` and `seeds/tag_lists_2026-11-12.json` carry the
answers.

1. **Is a critic's plain name for the practice "uses"?** Open: Sérgio
   asked for the options. Prompt tag-v2 holds the recommended one, "uses"
   for the speaker's own word whatever their side; "describes" only when
   the word itself is held at a distance (quoted, scare quotes,
   "so-called", rejected as a label). The statement's stance shows the
   side.
2. **Advocating a practice** (Claude's choice, on Sérgio's word). For a
   practice, "uses" means first-hand: the speaker or their organisation
   does, offers or runs it, or went through it. Promoting or defending it
   from outside is "describes"; the stance shows that they endorse it.
3. **Overlapping tactics: clusters instead** (Sérgio). The 44 tactics are no
   longer offered to the taggers. They tag the ontology's seven clusters
   (C1–C7), each with its core question and definition. The tactics stay
   in the vocabulary, each tied to its cluster.
4. **Actor types** (Claude, on Sérgio's word).
   - Up to two per statement, each with the words that state it.
   - Ex-gay or ex-trans means "went through change efforts".
   - A board member is a leader.
   - A theologian or ethicist is a researcher unless a religious office is
     stated.
   - A template has no actor type.
   - "Activist or campaigner" is added.
5. **Function definitions** (Claude, on Sérgio's word). All twelve are
   defined. "Disinformation Narrative" is not offered: calling a narrative
   false is a ruling on truth, which the archive does not make. The
   definitions are recorded decisions by the review model, with
   definition source "model".
6. **Conversion script or course structure** (Claude, on Sérgio's word). A
   conversion script holds the words to be said, prayed, vowed or signed.
   A course structure is the outline without the words: modules, steps,
   sessions, schedule, the parts of a group meeting.
7. **New entries for the missing excerpt kinds** (Sérgio: add them). Four
   dev and four holdout entries with prayer scripts, course structures and
   counter-sermons, drafted by a Claude agent; whoever tunes the taggers
   sees only the dev ones.
