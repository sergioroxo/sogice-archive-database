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
- **Qwen 3.8 27B:** the Stop the Ban stance; the Le Refuge figure. Not yet run
  on the long sources.

Qwen 3.8 27B and the 31B are level on these sources (2 vs 3 failures out of
about 50 checks is within noise). The 31B reads (Sérgio, 10 Nov). Add a new
model as a `trial_…` role in `config/models.yaml` and run the same test.
