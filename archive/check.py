"""The check: one more short question for agreed tags and excerpts (docs/TAGGING.md, verification, layer 3).

What the two taggers agree on is checked by a model that tagged nothing:

- every excerpt both taggers kept (testimony, prayer script, conversion script, course structure,
  counter-sermon);
- every agreed "uses" tag of a cluster or a practice: what someone argues, or does first-hand.

A trial can also ask about what only one tagger gave (``held=True``), to see whether the check could settle
those instead of the review queue.

The checker sees only the item (a statement with its speaker and stance, or the excerpt's text), the label
and its definition, and answers yes or no, copying the words that show it. Software looks for those words
inside the item. Each answer is recorded as an ``audit`` decision on the item, by the checker model:

- yes, words found → ``pass``;
- no → ``fail``;
- yes, words not found → ``uncertain``.

A fail or an uncertain sends the item to the review queue; the item itself is not changed.

To measure a checker (``decoys=True``), each item is also asked about a label it was not given: another
cluster or practice of the same list, another excerpt kind. Those answers are kept in the run only, never
as decisions. A good checker says yes to the real labels and no to the decoys.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import time
import uuid
from collections import Counter
from typing import Callable

from . import read
from .db import canonical_json, record_decision, utc_now
from .quote_locator import locate_quote
from .tag import EXCERPT_KINDS

DEFAULT_PROMPT = "check-v2"   # since the dev trial (docs/TAGGING.md)
JOB = "check"
SCHEMES = ("cluster", "practice")
MAX_WORDS_CHARS = 400
CONTEXT_CHARS = 1500
CONTEXT_PROMPTS = {"check-v2"}   # these prompts also show the source title and the passage around a statement
EXCERPT_DEFINITIONS = {   # as in prompts/tag-v3.md
    "testimony": "a first-person account of going through, giving or leaving change efforts",
    "prayer_script": "the words of a prayer said for or over someone to change them",
    "conversion_script": "the words to be said, prayed, vowed or signed in a session, group or programme: "
                         "scripts, dialogue, vows, a covenant (template wording counts)",
    "course_structure": "the outline of a programme or meeting, without the words to be said: its modules, "
                        "steps, sessions, schedule, or the parts of a group meeting",
    "counter_sermon": "affirming theology that answers change efforts",
}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer", "words", "reason"],
          "properties": {"answer": {"type": "string", "enum": ["yes", "no"]},
                         "words": {"type": "string"}, "reason": {"type": "string"}}}


def items_to_check(conn: sqlite3.Connection, source_ids: list[str], *, held: bool = False) -> list[dict]:
    """The agreed items to check: the kept copy of each (the other tagger's is superseded).

    ``held`` adds what only one tagger gave (a trial: could the check settle those?).
    """
    marks = ",".join("?" for _ in source_ids)
    confidences = ("two_readers_agree", "one_reader") if held else ("two_readers_agree",)
    which = f"a.confidence IN ({','.join('?' for _ in confidences)})"
    items = []
    for r in conn.execute(
            f"""SELECT g.id, a.source_id, a.confidence, c.scheme, c.id AS concept_id, c.pref_label, c.definition,
                       u.id AS usage_id, u.quote, u.speaker, u.stance, u.char_start, u.char_end,
                       p.text AS passage, src.title
                FROM tag g JOIN assessment a ON a.target_table = 'tag' AND a.target_id = g.id AND a.current = 1
                JOIN concept c ON c.id = g.concept_id JOIN usage u ON u.id = g.usage_id
                JOIN passage p ON p.id = u.passage_id JOIN source src ON src.id = a.source_id
                WHERE {which} AND g.review_state NOT IN ('superseded', 'rejected')
                  AND g.mode = 'uses' AND c.scheme IN ({','.join('?' for _ in SCHEMES)})
                  AND a.source_id IN ({marks}) ORDER BY a.confidence DESC, a.source_id, g.id""",
            (*confidences, *SCHEMES, *source_ids)):
        items.append({"table": "tag", "id": r["id"], "source_id": r["source_id"], "kind": r["scheme"],
                      "held": r["confidence"] != "two_readers_agree",
                      "concept_id": r["concept_id"], "usage_id": r["usage_id"], "label": r["pref_label"],
                      "definition": r["definition"] or "", "text": r["quote"], "speaker": r["speaker"],
                      "title": r["title"], "context": _around(r["passage"], r["char_start"], r["char_end"]),
                      "stance": r["stance"]})
    for r in conn.execute(
            f"""SELECT s.id, s.source_id, a.confidence, s.kind, s.text, s.speaker, src.title
                FROM asset s JOIN assessment a ON a.target_table = 'asset' AND a.target_id = s.id AND a.current = 1
                JOIN source src ON src.id = s.source_id
                WHERE {which} AND s.review_state NOT IN ('superseded', 'rejected')
                  AND s.kind IN ({','.join('?' for _ in EXCERPT_KINDS)}) AND a.source_id IN ({marks})
                ORDER BY a.confidence DESC, a.source_id, s.id""", (*confidences, *EXCERPT_KINDS, *source_ids)):
        items.append({"table": "asset", "id": r["id"], "source_id": r["source_id"], "kind": "excerpt",
                      "held": r["confidence"] != "two_readers_agree",
                      "label": r["kind"], "definition": EXCERPT_DEFINITIONS[r["kind"]], "text": r["text"],
                      "title": r["title"], "context": None,
                      "speaker": r["speaker"], "stance": None})
    return items


def _around(text: str, start: int, end: int, chars: int = CONTEXT_CHARS) -> str:
    """The statement's passage, cut to about ``chars`` around the statement."""
    if len(text) <= chars:
        return text
    room = max(0, chars - (end - start)) // 2
    a, b = max(0, start - room), min(len(text), end + room)
    return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")


def question(item: dict) -> str:
    if item["kind"] == "cluster":
        return f"Does the speaker argue this way ({item['label']})? {item['definition']}"
    if item["kind"] == "practice":
        return (f"Does the speaker, or the organisation speaking, do, offer or run this practice, or go through it, "
                f"first-hand ({item['label']})? {item['definition']}")
    return f"Is this passage a {item['label'].replace('_', ' ')}: {item['definition']}?"


def build_messages(prompt: read.Prompt, item: dict) -> list[dict]:
    who = f"Speaker: {item['speaker'] or 'not named'}" + (f"; stance: {item['stance']}" if item["stance"] else "")
    parts = [f"Question: {question(item)}", who]
    if prompt.version in CONTEXT_PROMPTS:
        if item.get("title"):
            parts.insert(0, f"Source: {item['title']}")
        if item.get("context") and item["context"] != item["text"]:
            parts.append(f"Context (around the passage; only to understand it):\n\"\"\"\n{item['context']}\n\"\"\"")
    parts.append(f"Passage:\n\"\"\"\n{item['text']}\n\"\"\"")
    return [{"role": "system", "content": prompt.text}, {"role": "user", "content": "\n".join(parts)}]


def decoy(conn: sqlite3.Connection, item: dict) -> dict | None:
    """The same item with a label it was not given (seeded by the item, so a re-run asks the same)."""
    rng = random.Random(hashlib.sha256(item["id"].encode()).hexdigest())
    if item["table"] == "asset":
        kind = rng.choice([k for k in EXCERPT_KINDS if k != item["label"]])
        return {**item, "label": kind, "definition": EXCERPT_DEFINITIONS[kind]}
    given = {r[0] for r in conn.execute("SELECT concept_id FROM tag WHERE usage_id = ?", (item["usage_id"],))}
    others = conn.execute("SELECT id, pref_label, definition FROM concept WHERE scheme = ? ORDER BY id",
                          (item["kind"],)).fetchall()
    left = [o for o in others if o[0] not in given]
    if not left:
        return None
    choice = rng.choice(left)
    return {**item, "concept_id": choice[0], "label": choice[1], "definition": choice[2] or ""}


def verdict(item: dict, data) -> tuple[str, str | None]:
    """(audit verdict, the located words or None) for one reply."""
    if not isinstance(data, dict) or data.get("answer") not in ("yes", "no"):
        return "uncertain", None
    if data["answer"] == "no":
        return "fail", None
    words = data.get("words") if isinstance(data.get("words"), str) else ""
    record = [{"unit_id": "Q", "text": item["text"]}]
    found = locate_quote(words.strip()[:MAX_WORDS_CHARS * 2], record)
    if found:
        return "pass", found["quote"]
    # pieces joined by an ellipsis: each must be found, in order
    pieces = [w for w in re.split(r"\s*(?:\.\.\.|…)\s*", words.strip()) if len(w.split()) >= 2]
    located, after = [], 0
    for w in pieces:
        hit = locate_quote(w[:MAX_WORDS_CHARS * 2], [{"unit_id": "Q", "text": item["text"][after:]}])
        if hit is None:
            return "uncertain", None
        located.append(hit["quote"])
        after += hit["end"]
    return ("pass", " … ".join(located)) if located else ("uncertain", None)


def check(conn: sqlite3.Connection, client, role, source_ids: list[str], *, prompt: read.Prompt | None = None,
          decoys: bool = False, held: bool = False, log: Callable[[str], None] = lambda _: None) -> dict:
    """Check the agreed items of these sources with one model. Returns counts and every answer."""
    conn.row_factory = sqlite3.Row
    prompt = prompt or read.load_prompt(DEFAULT_PROMPT)
    items = items_to_check(conn, source_ids, held=held)
    run_id = f"run_{uuid.uuid4().hex}"
    inputs = {"job": JOB, "sources": sorted(source_ids), "items": len(items), "decoys": decoys, "held": held,
              "prompt_sha256": prompt.sha256}
    with conn:
        conn.execute("INSERT INTO run(id, role, model, prompt_version, inputs, started_at) VALUES (?, 'audit', ?, ?, ?, ?)",
                     (run_id, role.model, prompt.version, canonical_json(inputs), utc_now()))
    counts: Counter = Counter()
    answers, seconds, tokens_in, tokens_out = [], 0.0, 0, 0
    started = time.monotonic()

    def ask(item):
        nonlocal seconds, tokens_in, tokens_out
        for attempt in (1, 2):   # retried once
            try:
                result = client.chat_json(role.model, build_messages(prompt, item), schema=SCHEMA,
                                          temperature=0 if role.temperature is None else role.temperature,
                                          num_predict=int(role.extra.get("num_predict", 512)),
                                          num_ctx=role.extra.get("num_ctx", 8192), think=role.extra.get("think", False),
                                          keep_alive=role.extra.get("keep_alive", "10m"))
            except Exception as exc:
                if attempt == 2:
                    return {"answer": None, "error": str(exc)[:300]}, "uncertain", None
                continue
            seconds += result.seconds or 0
            tokens_in += result.prompt_tokens or 0
            tokens_out += result.output_tokens or 0
            v, words = verdict(item, result.data)
            return (result.data if isinstance(result.data, dict) else {}), v, words

    def check_one(item) -> dict:
        data, v, words = ask(item)
        group = f"held_{item['kind']}" if item["held"] else item["kind"]
        counts[f"{group}:{v}"] += 1
        with conn:
            record_decision(conn, actor_kind="model", actor_id=role.model, run_id=run_id, action="audit",
                            target_table=item["table"], target_id=item["id"], audit_verdict=v, quote=words,
                            reason=f"{prompt.version}: {question(item)[:200]} Answer: {data.get('answer')}; "
                                   f"{(data.get('reason') or data.get('error') or '').strip()[:300]}"
                                   + ("" if v != "uncertain" or data.get("answer") != "yes"
                                      else " (the words given are not in the item)"))
        row = {"table": item["table"], "id": item["id"], "kind": item["kind"], "held": item["held"], "label": item["label"],
               "answer": data.get("answer"), "verdict": v, "words": words, "given": data.get("words"),
               "reason": data.get("reason")}
        fake = decoy(conn, item) if decoys else None
        if fake:
            d_data, d_v, _ = ask(fake)
            counts[f"{group}:decoy_{'no' if d_v == 'fail' else 'yes'}"] += 1
            row["decoy"] = {"label": fake["label"], "answer": d_data.get("answer"), "verdict": d_v,
                            "reason": d_data.get("reason")}
        return row

    try:
        for n, item in enumerate(items, 1):
            answers.append(check_one(item))
            if n % 25 == 0:
                log(f"  {n}/{len(items)} checked, {seconds:.0f} s")
    except Exception as exc:   # what was checked stays recorded; the run is marked failed
        with conn:
            conn.execute("UPDATE run SET outcome = 'failed', error = ?, seconds = ?, finished_at = ? WHERE id = ?",
                         (str(exc)[:1000], round(seconds, 1), utc_now(), run_id))
        raise
    finally:   # one resident model at a time
        try:
            client.unload(role.model)
        except Exception:
            pass
    calls = len(items) + sum(1 for a in answers if "decoy" in a)
    with conn:
        conn.execute("UPDATE run SET outcome = 'succeeded', seconds = ?, input_tokens = ?, output_tokens = ?,"
                     " inputs = ?, finished_at = ? WHERE id = ?",
                     (round(seconds, 1), tokens_in, tokens_out,
                      canonical_json({**inputs, "calls": calls, "counts": dict(counts),
                                      "wall_seconds": round(time.monotonic() - started, 1)}), utc_now(), run_id))
    return {"run_id": run_id, "model": role.model, "items": len(items), "calls": calls,
            "seconds": round(seconds, 1), "counts": dict(counts), "answers": answers}
