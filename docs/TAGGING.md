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
president"). A role is usually stated once, so software gives a type it has
found stated for a speaker to all of that speaker's statements in the source,
each citing the same words (rule `tags/v2`, since the second trial).
Agreement and review of actor types go per speaker and type, not per
statement. The list (`seeds/tag_lists_2026-11-12.json`) is for Sérgio to
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
   question, from a checker that did not tag them (`archive/check.py`,
   prompts `check-v1` and `check-v2`):
   - every excerpt;
   - every "uses" tag of a cluster or practice: what someone argues or does.

   The checker sees only the item, its speaker and the definition: "Does
   this show X? Answer yes or no, and copy the words that show it." Those
   words must be found inside the item. A "no", or words that cannot be
   found, sends the item to the review queue. Each answer is an audit
   decision on the item: pass, fail, or uncertain (yes, but the words are
   not in the item).

   The checker sees the source's title and the text around a statement
   (prompt `check-v2`). A trial also asks each item about a label it was
   not given (a decoy: another cluster, practice or excerpt kind). A good
   checker says yes to the real labels and no to the decoys. Gemma 4 12B is
   proposed after the dev trial (below).
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

Measured on dev (trials below): two-thirds of reading time, not the third
to a half first guessed.

- About 430 to 480 hours for both taggers over the English corpus, on top
  of about 700 for reading.
- The checker's short questions on top.

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

## First trial: prompt tag-v1 on dev (12 November 2026)

Both taggers tagged the 15 dev sources' 1,845 statements (73 sections), on a
copy of the archive, with tag-v1: 44 tactics, one actor type per statement.

**Time.** Tagging costs about two-thirds of reading, not a third:

| Tagger | Seconds per 1,000 characters | Tokens per call, in / out |
|---|---:|---:|
| Gemma 4 31B | 8.3 | 7,000 / 1,550 |
| Qwen 3.8 27B | 6.0 | 7,000 / 1,600 |

For the English corpus that is about 430 hours for both taggers, on top of
about 700 for reading: some 47 days of continuous running in all. A third
of each call's prompt was the tactic definitions, which tag-v2 drops.

**Agreement.** Counted per tag:

| Kind | Both taggers | Mode disputed | One tagger only |
|---|---:|---:|---:|
| Actor type | 475 | 0 | 616 |
| Term | 188 | 199 | 13 |
| Tactic | 105 | 3 | 282 |
| Practice | 64 | 3 | 104 |
| Function | 5 | 0 | 67 |

- **Term modes split on question 1 below.** For a critic's plain
  "conversion therapy", one tagger said "uses" and the other "describes".
- **Tactics agreed least.** That is one reason for clusters (question 3).
- **Functions had no definitions.** Almost every function tag came from one
  tagger only.

**Expectations.** 33 of the 52 dev expectations could be scored; the other
19 belong to the four new dev entries, not read yet.

| View | Pass | Fail |
|---|---:|---:|
| What both taggers agree on | 29 | 4 |
| 31B alone | 31 | 2 |
| Qwen alone | 32 | 1 |

What failed in the agreed view:

- Cohen's own story was not kept as a testimony.
- The Exodus "Moving On" programme got no practice that both agreed on.
- The Stop the Ban caption under a name got no actor type.
- One anchor has no statement at all: the readers recorded none there.

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

## Second trial: prompt tag-v2 on dev (12 November 2026)

Both taggers tagged the 18 dev sources that have statements (2,051
statements, the four new entries included), on a fresh copy of the archive.
Prompt tag-v2 carries the answers above: the seven clusters instead of the
tactics, up to two actor types, defined functions, and option A for terms.
The comparisons with tag-v1 below are on the 15 sources both trials tagged.

**Time.** Slower, not faster. The prompt is a seventh shorter, but the
taggers write more: actor types with their words, more cues.

| Tagger | Seconds per 1,000 characters, v1 → v2 | Tokens written, v1 → v2 |
|---|---|---|
| Gemma 4 31B | 8.3 → 9.0 | 113,000 → 125,000 |
| Qwen 3.8 27B | 6.0 → 6.8 | 118,000 → 141,000 |

For the English corpus that is about 480 hours for both taggers (20 days),
on top of about 700 for reading.

**Agreement**, per tag:

| Kind | Both | Mode disputed | 31B only | Qwen only | Share agreed, v1 → v2 |
|---|---:|---:|---:|---:|---|
| Actor type | 456 | 0 | 70 | 460 | 44% → 46% |
| Term | 374 | 17 | 0 | 9 | 47% → 94% |
| Cluster (v1: tactic) | 133 | 15 | 126 | 228 | 27% → 26% |
| Practice | 80 | 2 | 206 | 48 | 37% → 24% |
| Function | 67 | 0 | 34 | 110 | 7% → 32% |

- **Option A settled the terms.** The taggers now agree on 94% of them.
- **The function definitions worked**, from 7% to 32%.
- **Clusters did not agree better than tactics.** The taggers seldom pick
  different clusters for the same statement. They disagree on whether a
  statement shows one at all:
  - Qwen tags statements for a ban, or about the bill, as
    Policy-Resistance, which is the argument against a ban;
  - the 31B tags every general mention of "conversion therapy" as a
    practice described.

**Expectations**, what both taggers agree on:

| Entries | v1 | v2 |
|---|---|---|
| The 13 of the first trial (33 scored) | 29 pass, 4 fail | 25 pass, 8 fail |
| The four new ones (18) | not read yet | 12 pass, 6 fail |

Each tagger alone: the 31B 28 and 13 pass, Qwen 29 and 13. What failed:

- **Nothing to tag.** In three places the readers recorded no statement, or
  the only one is held (Stop the Ban on regret, the Lifebuilders worksheet,
  Church in the Cliff on the programme).
- **Actor types lost.** The 31B gives a speaker's type on each of their
  statements but copies the stating words only where they stand. Software
  dropped the others (362 for the 31B, 309 for Qwen), Justin's included.
- **Organisations.** The 31B gave Exodus International no type; Qwen gave it
  "leader or founder", a type for people.
- **Scare quotes.** The 31B left out "‘conversion therapy’" and
  "‘same-sex attraction’" instead of marking them "describes".
- **Lost with the tactics.** Two expectations asked for the rebranding
  tactic. With clusters, the taggers gave "euphemism, described" and the
  counselling-rebrand practice instead: these fit, but the expectations do
  not list them (for Sérgio's review).
- **Practices missed.** Coast Christian's own programme (both), Brookman's
  ministry (Qwen).
- **Excerpts.** Cohen's story is still not kept. At Coast Christian both kept
  the class schedule as a course structure, but not the parts of a meeting.
  Qwen's testimony of Justin stops early.

**The speaker rule** (software, `tags/v2`) answers the lost actor types.
Applied to the v2 results:

- agreed actor types per statement rise from 456 to 1,738, and Justin's
  expectation passes;
- counted per speaker and type, the taggers agree on 79, the 31B alone gives
  9 and Qwen alone 50;
- it also spreads a tagger's mistakes. Qwen gave Mike Davidson three types
  for organisations, now on each of his 144 statements: they are held, since
  the 31B did not give them. So the review queue shows actor types per
  speaker, not per statement.

## Third trial: prompt tag-v3 on dev (12 November 2026)

Tag-v3 changes the wording only; the lists stay those of tag-v2:

- **Actor types once per speaker**, on their first statement in the
  section, with words from anywhere in the section. Organisations get a type
  for organisations, from the words that say what they are.
- **Terms in quotation or scare quotes** are "describes", never left out.
- **A practice** is tagged only when the statement names or shows what is
  done, not for a general mention of conversion therapy.
- **A cluster** is an argument for change efforts or against a ban. A
  statement for a ban, or about a bill or a vote, shows none unless it
  reports or answers one of those arguments.

Run on the same 18 sources, on a fresh copy, with the speaker rule. Tag-v3
is the default prompt since.

**Time.** Faster: a quarter fewer tokens written, since an actor type is
given once.

| Tagger | Seconds per 1,000 characters, v2 → v3 | Tokens written, v2 → v3 |
|---|---|---|
| Gemma 4 31B | 8.9 → 8.2 | 138,000 → 105,000 |
| Qwen 3.8 27B | 6.6 → 5.7 | 154,000 → 111,000 |

For the English corpus: about 425 hours for both taggers (18 days).

**Agreement**, per tag (actor types per speaker and type):

| Kind | Both | Mode disputed | 31B only | Qwen only | Share agreed, v2 → v3 |
|---|---:|---:|---:|---:|---|
| Actor type | 75 | 0 | 14 | 36 | 57% → 60% |
| Term | 384 | 18 | 0 | 24 | 92% → 90% |
| Cluster | 120 | 10 | 115 | 158 | 27% → 30% |
| Practice | 82 | 5 | 60 | 42 | 25% → 43% |
| Function | 55 | 3 | 37 | 143 | 34% → 23% |

- Actor words that software could not find fell from 362 to 89 (31B) and
  from 309 to 53 (Qwen).
- The 31B no longer tags every mention of conversion therapy as a practice.
- Qwen now gives many functions alone. It still gives people types for
  organisations ("advocacy group" for Mike Davidson).

**Expectations** (52 on dev):

| View | v2 with the speaker rule | v3 |
|---|---|---|
| What both taggers agree on | 38 pass | 37 pass |
| 31B alone | 42 | 43 |
| Qwen alone | 43 | 43 |

- **Now pass:** Breton on detransition; Exodus as a ministry.
- **Now fail:**
  - France 24 on medication: neither tagger gave the medicalisation
    practice;
  - Cohen's self-description: Qwen gave no type;
  - Justin's exorcism: the 31B said "uses" and Qwen "describes", so it is
    disputed. He went through it, so "uses" is right by the prompt.
- **Replies vary between runs.** In the Christian Concern scare quotes, the
  31B gave no answer for the term in the run. Asked again with the same
  section, it marks it "describes", as it should. Temperature 0 does not
  make the replies the same from run to run.

**What this says.** Each tagger alone passes 43 of the 52 expectations, but
what both agree on passes only 37. Most failures in the agreed view are one
tagger missing something the other gave, not a wrong tag. With v3 on dev,
about 580 tags are given by one tagger only (most of them clusters and
functions): far too many for the review queue at the scale of the corpus.
The checker trial below asks whether a third model can settle them.

## Checker trial on dev (12 November 2026)

Two candidate checkers, Qwen 3.6 35B and Gemma 4 12B, each answered 364
questions on the tag-v3 results:

- what both taggers agreed on: 110 cluster and 22 practice "uses" tags, and
  11 excerpts;
- what only one tagger gave: 170 clusters, 28 practices and 23 excerpts, to
  see whether the check could settle those;
- once more for each item, about a label it was not given (a decoy).

Two prompts:

- **check-v1** shows only the statement, its speaker and the label's
  definition.
- **check-v2** also shows the source's title and the text around the
  statement. It says that the whole definition decides, not its first
  line, and asks for one continuous run of words.

| | v1, Qwen 35B | v1, Gemma 12B | v2, Qwen 35B | v2, Gemma 12B |
|---|---:|---:|---:|---:|
| Decoys rejected | 96% | 98% | 88% | 93% |
| Agreed clusters accepted | 52% | 53% | 79% | 68% |
| Agreed practices accepted (of 22) | 12 | 12 | 21 | 17 |
| Agreed excerpts accepted (of 11) | 2 | 6 | 9 | 10 |
| One-tagger clusters accepted | 31% | 34% | 63% | 38% |
| Agrees with Claude's reading of 14 cases | 6 | 6 | 9 | 10 |
| Seconds per question | 0.8 | 0.9 | 1.0 | 1.5 |

The 14 cases are agreed cluster tags that both checkers rejected with
check-v1. Claude judged 8 of them right in their context and 6 weak.

- **Check-v1 judged the statement out of context.** It could not see that a
  question in the French hearing belongs to the debate on gender-affirming
  care. It also read a cluster's first line literally: "banning it isn't
  going to stop people" was not opposing a ban, because it makes no rights
  argument.
- **For excerpts, check-v1 copied pieces joined by "..."**, which software
  could not find. Software now accepts such pieces when each is found, in
  order.
- **Context makes both checkers say yes more often, to decoys too.** Qwen
  35B with check-v2 accepts plainly wrong labels: an exorcism for Brookman's
  ministry, "Pro-Trans-SOGICE" for Cohen.
- **Gemma 4 12B with check-v2 is the better balance.** It rejects 93% of
  decoys, accepts two-thirds of agreed clusters and nearly all agreed
  practices and excerpts, and agrees most with Claude's reading. Proposed as
  the checker (role `checking`), for Sérgio to confirm.
- **No checker can settle what one tagger alone gave.** Gemma 12B accepts
  38% of those clusters, Qwen 35B 63%, and the decoys show how far a "yes"
  can be trusted.

**Proposal for what one tagger alone gives** (for Sérgio):

- It stays in the archive, searchable and marked "one tagger": it is
  research material.
- It is not published.
- It is not reviewed one by one; the weekly samples measure how often such
  tags are right.

The review queue then holds:

- disputes, where the taggers gave opposite modes;
- agreed items the checker rejected: with check-v2 about a quarter of the
  agreed clusters, and few practices or excerpts.

The checker costs little: on dev, about 8 questions per source.
