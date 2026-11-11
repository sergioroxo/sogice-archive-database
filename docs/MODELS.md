# Models on the Mac Studio

Checked 9 November 2026 with `python scripts/check_models.py` (full JSON report
under `data/model_check/`, not committed). Model names live only in
`config/models.yaml`; re-run the check after changing it.

## Setup

| Item | Value |
|---|---|
| Machine | Mac Studio M2 Ultra, 64 GB |
| Python | 3.14.7 (Homebrew), venv in `.venv/` |
| Ollama server used | 0.40.3 at `http://127.0.0.1:11434`, started by the LaunchAgent `com.ollama.server` with `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_KEEP_ALIVE=0`, `OLLAMA_NUM_PARALLEL=1` |
| Client | `archive/ollama.py` (standard library). Every call sends `keep_alive: 0`; chat calls send `think: false` and a `num_predict` cap |

**Two servers are running.** The Ollama menu-bar app starts a second server
(0.40.12) that answers on `[::1]:11434` without the one-model limit. Both
share the same model store, so digests now match. Our config uses
`127.0.0.1`, so the pipeline reaches the limited server. Anything that uses
`localhost` may reach the other one. The check script warns while both run.

## Roles

| Role | Ollama tag | Digest | Use |
|---|---|---|---|
| reading | `gemma4:12b-mlx` (nvfp4) | `117d0d84cf2a` | Bulk structured reading |
| audit | `gemma4:31b-mlx` (nvfp4) | `637cc0ff1570` | Hard cases, audit pass |
| embedding | `embeddinggemma-2:740m` (nvfp4) | `969600645b52` | Search and suggestions only |

Both Gemma models report the `thinking` capability; we switch it off. The
12B model also reports `vision` and `audio`, the 31B only `vision`.

## Results

### Reading and audit (one JSON-schema call on an invented two-speaker passage)

| Model | Time | Tokens in / out | JSON | Quotes located | Speaker + stance right |
|---|---:|---:|---|---|---|
| 12B | 7.4 s | 177 / 88 | yes | 2 / 2 | 2 / 2 |
| 31B | 15.2 s | 177 / 103 | yes | 2 / 2 | 2 / 2 |

The 12B model chose the shortest supporting span ("I believed I was
broken"); the 31B copied the whole sentence. Both are located by
`quote_locator`. Times include loading the model from disk.

### Embeddings

| Input | Works | Vector | Time | Sanity check |
|---|---|---:|---:|---|
| Text | yes | 768 | 2.1 s for 4 (incl. load) | — |
| Image (PNG, base64 in `input[].image`) | yes | 768 | 0.3 s | red image closer to "a plain red image" (0.72) than "a plain blue image" (0.63) |
| Audio (16 kHz WAV, `input[].audio`) | yes | 768 | 1.0 s | spoken sentence closer to its text (0.89) than to unrelated text (0.56) |
| Video (`input[].video`) | **no**: HTTP 400 `input.video not supported` | — | — | — |
| Video as frames (1 fps) + audio track | yes | 768 each | 0.8 s for a 4 s clip | audio track matches the speech clip (≈1.0) |

`ollama show` lists `embedding_length 512` and a 262k context. Both are
misleading: the vectors are 768-dimensional (the configured value) and the
server enforces **8,192 tokens per input**.

### Audio length per input

| Clip | Result |
|---|---|
| 60 s | ok, 0.7 s |
| 300 s | ok, 8.8 s |
| 330 s | fails: 8,254 tokens > 8,192 |
| 400 s | fails: 10,004 tokens |

About 25 tokens per second of audio. **Cut audio into chunks of at most
5 minutes** (`max_audio_seconds: 300`), with timecodes. Video is embedded as
one image per sampled frame plus the audio chunk for the same span.

## Consequences for the pipeline

- Embedding calls use `truncate: false`: an over-long input is an error, not a
  silently shortened vector.
- Video embeddings are a set of frame vectors and an audio vector per span,
  not one vector per clip.
- Reading calls always set `num_predict` (4,096 reading, 2,048 audit) and
  `num_ctx` 32,768 (see `config/models.yaml`).

## Reading baseline (10 November 2026)

Same test for every model: prompt `read-v3`, sections of 6,000 characters,
temperature 0, the 12 short dev entries of the test set (13 sources, 69,351
characters; 30 usage anchors plus the other software checks). Each trial
model read into its own copy of the database (`read --model-role trial_…
--db data/scratch/…`); judged expectations are not counted here.

| Model | Kind | Expectations failed | Quotes located | Speaker filled | s per 1,000 chars | English corpus (109 M chars) |
|---|---|---:|---:|---:|---:|---:|
| `gemma4:12b-mlx` | Gemma 4, 12B dense | 15 | 98% | 97% | 7.0 | ≈ 215 h |
| `gemma4:31b-mlx` | Gemma 4, 31B dense (**chosen**) | 3 | 98% | 97% | 12.9 | ≈ 390 h |
| `qwen3.6:35b-mlx` | Qwen 3.6, 35B mixture of experts | 11 | 98% | 97% | 12.0 | ≈ 365 h |
| `qwen3.8:27b-mlx` | Qwen 3.8, 27.8B dense | 2 | 98% | 97% | 11.9 | ≈ 360 h |

Corpus hours are measured on short pages, which cost the most per character
(each call repeats the prompt). On the three long dev sources the 31B read
at 8.7 s per 1,000 characters (French report 154,499 chars in 23.5 min;
Davidson interview 9.6 min; Trevor report 9.0 min), so the real total should
be lower.

What still goes wrong, by model:

- **12B, Qwen 3.6 MoE:** opposing a ban marked "criticizes"; testimony
  captions credited to the website; quoted parties and intermediaries left
  empty; whole sentences skipped.
- **31B:** the curriculum title page yields nothing; the Stop the Ban site's
  stance; one figure credited to the outlet instead of Le Refuge. On the long
  sources: the French list of people heard (pp. 63–64) became 20
  `affiliated_with` edges ("Name, role at organisation") and 3 stances, and
  in the Davidson captions (no speaker labels) the host's words went to
  Davidson twice.
- **Qwen 3.8 27B:** the Stop the Ban stance; the Le Refuge figure.

### Full dev set, including the long sources

All 16 dev entries (the 13 short sources above plus the French report, the
Davidson interview and the Trevor report; 360,000 characters), same settings.

| Model | Expectations failed | Located quotes | Speaker filled | Long sources, s per 1,000 chars |
|---|---:|---:|---:|---:|
| `gemma4:31b-mlx` | 7 | 810 on the long sources | 98.4% | 8.7 |
| `qwen3.8:27b-mlx` | 6 | 1,116 on the long sources | 99.7% | 8.4 |

Long sources one by one (located quotes, minutes):

| Source | 31B | Qwen 3.8 27B |
|---|---|---|
| French report 4501 (154,499 chars) | 412, 23.5 min | 612, 25.2 min |
| Davidson interview (73,676 chars) | 224, 9.6 min | 287, 8.3 min |
| Trevor report (62,669 chars) | 174, 9.0 min | 217, 7.3 min |

Failures both models share: the French list of people heard (pp. 63–64)
turned into `affiliated_with` edges (31B 20, Qwen 10) and stances; the host's
words in the Davidson captions (no speaker labels) credited to Davidson; the
Le Refuge figure credited to France 24. Only the 31B: no quote for Petit on
p. 49; a Harvest support-group sentence skipped. Only Qwen: the Stop the
Ban stance.

The two are level within noise. Qwen quotes 25–50% more on long sources at
the same speed, and proposes more relations (French report: 94 against 39),
which is more detail but also more to check. The shared failures point at
the prompt and the test set (is a stated role in a list an affiliation?),
not at the model. The 14 judged expectations are not scored yet.

Qwen 3.8 27B and the 31B are level on these sources (2 vs 3 failures out of
about 50 checks is within noise). The 31B reads (Sérgio, 10 Nov). Add a new
model as a `trial_…` role in `config/models.yaml` and run the same test.

### Judged expectations (blind A/B, 10 November)

`scripts/judge_packets.py` wrote one packet per judged expectation (source
text plus both readings, labelled A/B in random order per packet); a
separate Claude agent judged them without the key. Both models pass 8 of the
14. With these verdicts, the whole dev split (88 expectations): 31B fails 13,
Qwen 3.8 27B fails 12.

| | Expectations |
|---|---|
| Both fail (9) | interviewer's question credited to the interviewee (Christian Concern); Chambers quoted by Gagnon given to Gagnon (Eternity); Le Refuge figure credited to France 24; Petit's "pauses" use missing (French pp. 48–49); list of people heard turned into `affiliated_with` edges and stances (French pp. 63–64, two checks); covenant Sample 1 exceptions missing (Harvest); the host's words in the Davidson captions credited to Davidson (two checks) |
| Only the 31B fails (4) | all missed statements: Exodus affiliates continuing; Petit p. 49; Harvest church-discipline exception; MoU para. 6 exemptions |
| Only Qwen fails (3) | "transforming communities" marked as endorsing change; Stop the Ban stance; a pastoral exception missed |

What this says about using two models: the 7 model-specific failures are
what a second model can fix (5 missed statements that the other model
found, 2 stance disagreements a comparison would flag). The 9 shared ones it
cannot: both models make the same mistake, so they agree. Those need prompt
rules (who owns an interviewer's question, nested quotes, a stated role is
`employed_by`, a list of people heard states no stance, unlabelled captions)
and Sérgio's weekly sample. Correct records were never at issue: quotes are
located by software, so a model cannot invent text, only misattribute it.

## Two readers with prompt read-v4 (10 November, evening)

Both models read all 16 dev entries with `read-v4` (the rules for the shared
hard cases); `reconcile` then compared them. Each model's reading was also
scored alone, in a copy of the database with the other reader hidden.

Automatic checks (74 of the 88 expectations):

| Reading | read-v3 failures | read-v4 failures |
|---|---:|---:|
| 31B alone | 7 | 4 |
| Qwen 3.8 27B alone | 6 | 7 |
| Both, reconciled (what would be published) | – | 5 |

Judged expectations (14), blind, three readings shuffled per packet and
judged by a separate agent without the key: each reading fails 3 (read-v3:
6 each). Whole dev split (88 expectations):

| Reading | read-v3 | read-v4 |
|---|---:|---:|
| 31B alone | 13 | 7 |
| Qwen 3.8 27B alone | 12 | 10 |
| Both, reconciled | – | 8 |

Judged failures: all three still give the interviewer's question to Jack
Sterling (Christian Concern) and miss Petit's "pauses" use (French pp. 48–49;
the judge read the criterion as requiring all three uses, a stricter reading
than "keeps the senses apart"). The 31B alone still leaves out the covenant's
Sample 1 confidentiality promise and its exceptions, which Qwen records, so
the combined reading passes. Qwen gives Chambers' words, quoted by Gagnon,
to Gagnon (Eternity), and that one-reader record carries into the combined
reading.

What read-v4 changed:

- **Fixed for both:** the Le Refuge figure is now credited to Le Refuge.
- **Fixed for the 31B:** the list of people heard (pp. 63–64) no longer
  becomes `affiliated_with` edges (it gives `employed_by` from stated roles,
  as the rule says); Petit's p. 49 statement; one of the two Davidson host
  checks.
- **Still shared:** the list of people heard still gets stances (both); the
  host's Pink Therapy remark in the Davidson captions is credited to the
  guest (both).
- **New failures:** the 31B makes Torrents de Vie the speaker of words that
  reach the report only through Petit (p. 42); Qwen turns amendments into
  `co_signatory_of` and `authored_by` edges for the deputies who moved them
  (p. 42), and gives Petit's words after a page break to the rapporteure
  (p. 43).

What reconciling did, against the 31B alone:

- **Removed 31B errors** where Qwen disagreed: the Torrents de Vie line was
  disputed, so it is not published.
- **Added Qwen's misses back:** the Harvest church-discipline exception,
  which only Qwen recorded.
- **Added Qwen's errors:** every failing relation in the combined reading
  (7 records: amendments as `co_signatory_of`, the list of people heard as
  `affiliated_with` and `employed_by`) was proposed by Qwen alone.
- **Held back a correct reading:** the Stop the Ban stance (31B right, Qwen
  wrong) is disputed, so neither is published. That fails the check, but it
  is the safe kind of failure: nothing wrong is said.

Disagreements in the dev set (456 statements in 228 pairs, 19 relation
pairs): 171 on speaker, 128 on stance, 19 on relation type. Of the speaker
pairs, 49 are "unknown" against a name or a site, many from the list of
people heard. The commonest stance clash is criticizes against endorses on
statements that support the ban (49 pairs with the 31B saying criticizes,
which is the rule; 26 the other way): read-v4 says in words that stance is
toward the practice, never the ban, and Qwen often does not follow it.

Relations are where the readers agree least: 81 of 280 published relations
were proposed by both (63 of them `employed_by`); Qwen alone proposed 172,
the 31B alone 27.

Time (current read-v4 runs, 359,859 characters each):

| Model | Total | s per 1,000 chars | long sources | short sources |
|---|---:|---:|---:|---:|
| 31B | 62.4 min | 10.4 | 9.2 | 15.6 |
| Qwen 3.8 27B | 54.2 min | 9.0 | 8.3 | 12.0 |

The longer prompt costs about 20% on short sources (31B 12.9 → 15.6 s). Five
short sources go over 15 s per 1,000 characters for one reader, because each
call repeats the prompt; no source came near the run-time ceiling. Both
readers over the English corpus (109 M characters, about 60 M in short
sources): roughly 700 hours, a month of continuous running.

### Review of disputed and held records (10 November)

With single-reader relations held from publication, the combined reading
fails 3 automatic checks instead of 5. A first blind review of 61 packets by
Claude (`/review-queue`, on a copy of the database) brought it to 2: the
Stop the Ban stance is settled. Where the reviewer chose a reading it chose
the 31B 17 times and Qwen 17 times; in 9 of 26 statement disputes both were
wrong. Details and the reviewer's proposed rules: `docs/REVIEW.md`.

The scorer used to miss relations whose evidence quote had been folded into
the other reader's copy; it now finds relations through any located quote
in the source. The combined figure above (5 before holding) was re-checked
with the fixed scorer and holds.

## Prompt read-v5 and the relation rules (11 November)

Both readers read the 16 dev entries with `read-v5` (the trial's rules:
`docs/REVIEW.md`), on a copy of the database; reconcile v3 and the relation
rules (`archive/rules.py`) then ran on it. The comparison is with the read-v4
readings put through the same reconcile and rules. Each reader alone is
scored in a copy where the other reader is hidden and the rules are applied
again to that reader's records (`scripts/solo_copy.py --rules`). Judged
expectations: four readings per packet, shuffled, two blind judges (seven
packets each) without the key.

| Reading | read-v4 + rules | read-v5 + rules |
|---|---:|---:|
| Both, reconciled: automatic (74) | 3 | 5 |
| Both, reconciled: judged (14) | 2 | 2 |
| 31B alone: automatic | 4 | 5 |
| 31B alone: judged | 3* | 4 |
| Qwen 3.8 27B alone: automatic | 7 | 7 |
| Qwen 3.8 27B alone: judged | 3* | 3 |

\* from the 10 November judging, before the rules; the rules decide
relations only.

read-v5 is not better on the dev set, so read-v4 stays the default.

**What the ban field did.** Software set the stance from the speaker's
position on a ban in 37 of the 31B's statements and 200 of Qwen's. Against
the same quotes read with read-v4, Qwen had given `endorses` to supporters
of a ban 57 times and `criticizes` to its opponents 41 times (stance toward
the ban, not the practice); the 31B 5 times. Stop the Ban now passes, for
both together and for Qwen alone. Disputes on stance alone fell from 57
pairs to 31.

**What went wrong:**

- **A law that is not a ban.** The CNN interview is about repealing a 1950
  California law ordering research into a "cure". Qwen marked Lowenthal as
  opposing "the law", and software made her criticism `endorses`.
- **Descriptions became criticism.** The first mapping (`ban/v1`) replaced
  every stance of a ban supporter, so "la sanction est portée à trois ans"
  (what a bill contains) and "22 states have enacted bans" became
  `criticizes` (about 30 Qwen statements). `ban/v2` keeps describes and
  reports as the reader gave them.
- **A catalogue title as a speaker.** The French report's title in the
  archive is "r4501". The 31B named the report's own text by it 116 times
  (read-v4: never), so 85 of 262 disputed statement pairs are "r4501"
  against the mission or the commission. Without them, disputes would have
  fallen from 218 pairs to 177. 820 sources have no title and many have
  poor ones ("Untitled", "Home", "Accueil").
- **Two 31B slips.** It gives Rod Benson's quote (introduced at the end of
  the unit before) to "unknown", and the MoU's exemption for exploratory
  help `not_applicable`. Qwen gave the exemption `mixed`, but the stances do
  not clash, so reconcile kept the primary reader's.

**Other effects:**

- Fixed for the 31B: Torrents de Vie as the speaker of Petit's words
  (p. 42). The list of people heard gets fewer stances (both together: 3
  forbidden stances became 1).
- "host"/"guest" against a name: 31 disputed pairs became 13. The host's
  Pink Therapy remark still fails: the 31B now says "unknown" instead of
  "guest", Qwen still names the guest. A speaker list for the recording
  (`python -m archive speakers`) is what would settle it.
- Reference lists now give relations: 31B 127 → 228, Qwen 272 → 318. Most
  are held as one reader's (held relations 139 → 223, of which `authored_by`
  69 and `cites` 61).
- The review queue grew from 368 packets to 505 (statement disputes 218 →
  262, held relations 139 → 223); consequential relations 39 → 44.
- Time on the same 359,859 characters: 31B 61 → 74 min (10.2 → 12.3 s per
  1,000 characters), Qwen 54 → 63 min (9.0 → 10.6 s). Both are within the
  15 s budget; the longer prompt costs about 20%.

## Prompt read-v6 (11 November)

`read-v6` keeps read-v5 and adds four short clarifications aimed at its
failures (`docs/READING.md`). Same dev set, same reconcile and rules, and
the narrower ban rule (`ban/v2`). Automatic checks (74):

| Reading | read-v4 + rules | read-v5 + rules | read-v6 + rules |
|---|---:|---:|---:|
| Both, reconciled | 3 | 5 | 7 |
| 31B alone | 4 | 5 | 8 |
| Qwen 3.8 27B alone | 7 | 7 | 7 |

One of the read-v6 failures in each row is `fr-p42-no-authorship`: both
readers now give the amendments as authorship, as adopted rule 3 says, so
the relation is no longer held and the test set's opposite expectation
fails (`docs/REVIEW.md`). Without it, read-v6 fails 6, 7 and 6. The judged
expectations were not run: on the automatic checks alone read-v6 is not
better, so it is not adopted and read-v4 stays the default.

None of the four clarifications did what it was written for:

- **The catalogue title.** The 31B still names the French report "r4501"
  (111 statements) although the prompt now says never to use a file name or
  code: it is what the header calls the report. The fix is the title, not
  the prompt.
- **The law that is not a ban.** Qwen still marks Lowenthal as opposing "the
  law", although the prompt now names a law ordering research into a "cure"
  as the example; its own stance was `criticizes`, and software turned it
  into `endorses`. The 31B said `supports` (the repeal bill), which keeps
  her stance right.
- **Benson.** The 31B still gives his quote to "unknown".
- **The MoU exemption.** Still `not_applicable` from the 31B, which reconcile
  keeps as the primary reader's.

And the new wording moved other things: the 31B now misses Petit's "pauses"
(p. 49) and the Exodus affiliates' continuation, and Qwen gives the
Davidson host's own story to the guest, which carries into the combined
reading.

The readers agree more. Disputed statement pairs: read-v4 218, read-v5 262,
read-v6 198. The "r4501" disputes alone are 78 of the 198; without them the
pairs fall to 120, and those on "unknown" (49 → 22), on host and guest
labels (31 → 8) and on two different names (81 → 57) all fell. Agreement is
not correctness, but it is what makes the review queue smaller: 479
packets, of which 33 consequential relations (read-v4 with rules: 368 and
39). Time: 31B 12.5 s and Qwen 10.7 s per 1,000 characters.

What this says about working with these models: at temperature 0 the
readings are repeatable, but any change of wording moves answers well away
from the passage it was written for, so a dev set of 74 checks can only
tell large effects from small ones. read-v5 fixed two of the cases it was
written for (Stop the Ban, stances on the list of people heard) and broke
three other checks; read-v6 fixed none of its four and broke three.
The gains that held came from software (relation rules, the ban field's
stance rule, reconcile) and from what the readers are told about a source.
The next steps are therefore not a read-v7 but: repair poor source titles
(`docs/FINGERPRINTS.md`), speaker lists for the three unlabelled recordings,
and settling the amendment expectation.

## Claude as a test reader (11 November)

Sérgio decided that open models read, because their readings can be
re-run and checked; Claude is a helper that makes the system better. To
see how far the local readers are from a strong reader, six Claude Code
agents read the dev set exactly as a local model does: prompt read-v4, the
same 73 sections and units, blind to the test set and to every other
reading (`scripts/claude_reader.py`). Their answers were replayed through
the normal reading code, so the quotes were located, the records written
and the rules applied as for any reader, under the role `trial_claude`.
None of it is stored in the archive.

| Reading (alone, with the rules) | Automatic (74) | Judged (14) | Total (88) |
|---|---:|---:|---:|
| Claude (read-v4) | 3 | 0 | 3 |
| 31B (read-v4) | 4 | 4 | 8 |
| Qwen 3.8 27B (read-v4) | 7 | 3* | 10 |

\* from the 10 November judging. Claude and the 31B were judged together,
blind, by one judge.

Claude's three automatic failures are mostly disagreements with the test
set rather than mistakes: it records the amendments as authorship, as rule
3 says (`fr-p42-no-authorship`); it gives Torrents de Vie the words Petit
reads out from its site, with Petit as intermediary, as the prompt's rule
for quotations inside quotations says, where the test set forbids it
(`fr-p42-tdv-not-speaker`, which the 31B also fails); and the check on
pp. 63–64 counts the committee's closing request to adopt the bill, which
falls inside its window. Both conflicts are for Sérgio to settle. Every one
of Claude's 2,250 quotes was located.

Compared quote by quote with the local readers:

- Claude records more: 2,250 statements against 1,354 (31B) and 1,691
  (Qwen); 834 of its statements have no 31B counterpart.
- On the quotes both recorded, the speaker differs from the 31B's in 199 of
  1,416 (mostly "Mme la rapporteure" or the website against the named
  rapporteure) and the stance is opposite in 64; against Qwen, 276 and 172.
- The opposite stances show the readers' confusions the prompt already
  rules on: the 31B marks an ex-gay activist who criticises Exodus's
  president as "criticizes", where the prompt says defending change efforts
  is "endorses"; Qwen marks speeches for the French ban as "endorses".

Limits of the comparison. The agents were not the same kind of reader: one
went back and corrected earlier sections of the Davidson recording after
seeing all of it (a local reader sees one section at a time), and they
checked their own quotes with small scripts. Their readings cannot be
re-run: the hosted model will change. The cost was about 1.26 million
tokens for the 0.36 M characters of the dev set, roughly 3,500 tokens per
1,000 characters; the English corpus alone (109 M characters) would need
some 380 million. So Claude cannot be the reader of record here, and was
not meant to be. What it is good for:

- a reference that shows where the local readers fail and why, by kind,
  across a whole source rather than on 88 checks;
- finding where the test set and the rules disagree;
- drafting corrections that Sérgio checks, as the review queue does now.
  Using its readings as training data for an open model needs a check of
  UiB's agreement with Anthropic first.
