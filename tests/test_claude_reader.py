import importlib.util
import json
from pathlib import Path

from archive import read

SPEC = importlib.util.spec_from_file_location("claude_reader", Path(__file__).resolve().parent.parent / "scripts" / "claude_reader.py")
claude_reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(claude_reader)


def test_a_section_is_found_whatever_context_came_before_it(tmp_path):
    prompt = read.load_prompt("read-v4")
    chunk = [{"text": "Alan Chambers said the ministry would close.", "passage_id": "p1", "version_id": "v",
              "page_start": None, "page_end": None, "time_start": None, "time_end": None}]
    exported, _ = read.build_messages(prompt, "Source URL: x", chunk,
                                      {"speaker": "(yours)", "tail": "…end of section one"}, (2, 3))
    replayed, _ = read.build_messages(prompt, "Source URL: x", chunk, {"speaker": "Alan Chambers", "tail": "…end"}, (2, 3))
    key = claude_reader.section_key(exported[1]["content"])
    assert key == claude_reader.section_key(replayed[1]["content"])
    answer = {"statements": {"P1": [{"quote": "the ministry would close"}]}, "entities": [], "relations": []}
    (tmp_path / "answers" / "src").mkdir(parents=True)
    (tmp_path / "answers" / "src" / "2.json").write_text(json.dumps(answer))
    client = claude_reader.ReplayClient(tmp_path, [{"source_id": "src", "keys": ["other", key]}])
    assert client.chat_json("m", replayed).data == answer
    gap = client.chat_json("m", [{}, {"content": "Units:\n\n[P1]\nsomething else"}],
                           schema=read.response_schema(["P1", "P2"]))
    assert gap.data == {"statements": {"P1": [], "P2": []}, "entities": [], "relations": []}
