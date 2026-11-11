"""Claude as a test reader: the same sections, prompt and software as the local readers, for comparison only.

Claude is a helper here, not a reader of record (open models read; docs/MODELS.md). This script lets
Claude Code agents read the dev set exactly as a local model would, so their readings can be scored with
the same checks and compared with the local readers' — to see where the local readers fail and why.

    python scripts/claude_reader.py export --db data/scratch/claude.db --prompt read-v4 --out data/scratch/claude-reader
    # agents write answers/<source_id>/<n>.json, one per section (see the README the export writes)
    python scripts/claude_reader.py replay --db data/scratch/claude.db --prompt read-v4 --out data/scratch/claude-reader

``export`` writes, for each dev source, its sections as the local readers see them (the header, the
units, the previous section's tail), plus the reading prompt and the answer format. ``replay`` runs the
normal reading code (archive/read.py) with a client that answers each section from the agents' files, so
the quotes are located, the records written and the runs recorded exactly as for a local model, under
the ``trial_claude`` role in config/models.yaml. A section with no answer file stops the replay; the
reader's second look at passages it skipped gets an empty answer (the agents read every unit once).
Only for scoring, on a copy of the database: never point it at data/archive.db.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archive import db, read, testset  # noqa: E402
from archive.config import load_models_config  # noqa: E402
from archive.ollama import ChatResult  # noqa: E402

UNITS = "\n\nUnits:\n\n"


def section_key(user_content: str) -> str:
    """The same for a section whatever came before it (the carried context is left out)."""
    return hashlib.sha256(user_content.split(UNITS, 1)[-1].encode("utf-8")).hexdigest()[:20]


def dev_sources(conn, split: str) -> list[str]:
    data = testset.load_testset(Path(__file__).resolve().parent.parent / "evaluation" / "testset_2026-11-10.json")
    out = []
    for entry in data["entries"]:
        if entry["split"] == split:
            out += [read.keeper(conn, s) for s in testset.entry_sources(entry)]
    return list(dict.fromkeys(out))


def export(args) -> int:
    conn = db.connect(args.db)
    prompt = read.load_prompt(args.prompt)
    from archive.match import furniture_hashes
    furniture = furniture_hashes(conn)
    out = Path(args.out)
    (out / "sources").mkdir(parents=True, exist_ok=True)
    (out / "answers").mkdir(exist_ok=True)
    (out / "PROMPT.md").write_text(prompt.text, encoding="utf-8")
    manifest = []
    for n, sid in enumerate(dev_sources(conn, args.split), 1):
        units, _ = read.source_units(conn, sid, furniture)
        if not units:
            continue
        chunks = read.make_chunks(units, args.chunk_chars)
        header = read.source_header(conn, sid)
        lines = [f"# Source {sid}", "", f"{len(chunks)} section(s). Write each answer to "
                 f"`answers/{sid}/<section>.json`.", ""]
        keys = []
        for i, chunk in enumerate(chunks, 1):
            carry = None
            if i > 1:
                carry = {"speaker": "(the speaker of your last statement in the previous section)",
                         "tail": chunks[i - 2][-1]["text"][-read.CARRY_CHARS:]}
            messages, records = read.build_messages(prompt, header, chunk, carry, (i, len(chunks)))
            keys.append(section_key(messages[1]["content"]))
            lines += [f"## Section {i} of {len(chunks)} -> answers/{sid}/{i}.json",
                      f"Units in this section: {', '.join(r['unit_id'] for r in records)}", "",
                      "````text", messages[1]["content"], "````", ""]
        (out / "sources" / f"{n:02d}-{sid}.md").write_text("\n".join(lines), encoding="utf-8")
        (out / "answers" / sid).mkdir(exist_ok=True)
        manifest.append({"source_id": sid, "file": f"sources/{n:02d}-{sid}.md", "sections": len(chunks),
                         "chars": sum(len(u["text"]) for u in units), "keys": keys})
    example = read.response_schema(["P1", "P2"], prompt.features)
    (out / "README.md").write_text(
        "# Reading as a test reader\n\n"
        "Read PROMPT.md: it is the instruction every reader gets. Then, for each section of your source file,\n"
        "write one JSON object to the answer file named in the section heading, following PROMPT.md and this\n"
        "JSON schema (shown for a section with units P1 and P2; use the section's own unit ids, every one of\n"
        "them, in order, each with a list, empty when the unit holds nothing relevant):\n\n"
        f"```json\n{json.dumps(example, ensure_ascii=False, indent=1)}\n```\n\n"
        "Quotes must be copied exactly from the unit (software finds them). The 'Last speaker' line in a\n"
        "section's context is yours to fill: the speaker of your last statement in the previous section.\n",
        encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps([{k: m[k] for k in ("source_id", "file", "sections", "chars")} for m in manifest], indent=1))
    return 0


class ReplayClient:
    """Answers each section from the agents' files; the reader's gap pass gets an empty answer."""

    def __init__(self, out: Path, manifest: list[dict]):
        self.answers = {}
        for m in manifest:
            for i, key in enumerate(m["keys"], 1):
                self.answers[key] = out / "answers" / m["source_id"] / f"{i}.json"
        self.calls = 0

    def chat_json(self, model, messages, *, schema=None, **_):
        self.calls += 1
        path = self.answers.get(section_key(messages[1]["content"]))
        unit_ids = list(((schema or {}).get("properties", {}).get("statements", {}).get("properties", {})).keys())
        if path is None:   # a second look at skipped passages: the agents read every unit once
            data = {"statements": {u: [] for u in unit_ids}, "entities": [], "relations": []}
        else:
            if not path.exists():
                raise RuntimeError(f"no answer yet: {path}")
            data = json.loads(path.read_text(encoding="utf-8"))
        text = json.dumps(data, ensure_ascii=False)
        return ChatResult(data, text, model, 0.0, None, None, "stop")

    def unload(self, model):
        pass


def replay(args) -> int:
    if Path(args.db).name == "archive.db":
        print("replay only into a copy of the database (data/scratch/…)", file=sys.stderr)
        return 2
    out = Path(args.out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    conn = db.connect(args.db)
    db.init_db(conn)
    role = load_models_config().role("trial_claude")
    client = ReplayClient(out, manifest)
    results = read.read_sources(conn, client, role, [m["source_id"] for m in manifest],
                                prompt=read.load_prompt(args.prompt), chunk_chars=args.chunk_chars, again=True,
                                log=lambda line: print(line, flush=True))
    print(json.dumps({"sources": len(results), "calls": client.calls}, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("export", "replay"))
    ap.add_argument("--db", required=True)
    ap.add_argument("--prompt", default=read.DEFAULT_PROMPT)
    ap.add_argument("--out", required=True)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--chunk-chars", type=int, default=read.DEFAULT_CHUNK_CHARS)
    args = ap.parse_args()
    return export(args) if args.action == "export" else replay(args)


if __name__ == "__main__":
    sys.exit(main())
