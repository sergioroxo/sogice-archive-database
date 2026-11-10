import json
import re

import pytest

from archive import db, read
from archive.config import RoleConfig
from archive.ollama import ChatResult, OllamaError
from archive.passages import sha256_text
from archive.urls import source_id_for

NOW = "2026-11-10T12:00:00Z"
ROLE = RoleConfig(name="reading", model="test-model:1b", temperature=0,
                  extra={"num_predict": 512, "num_ctx": 4096, "keep_alive": "1m"})
TEXT_A = ("Alan Chambers, president of Exodus, said: “We are closing the ministry today.” "
          "Exodus says that local affiliated ministries will continue their work.")
TEXT_B = "Board member Tony Moore said a new generation is looking for change. Exodus was founded in 1976."


def add_source(conn, url, passages, *, flags=None, version="v"):
    sid = source_id_for(url)
    conn.execute("INSERT OR IGNORE INTO source(id, url, title, created_at) VALUES (?,?, 'A title', ?)",
                 (sid, url, NOW))
    vid = f"{version}_{sid}"
    conn.execute("INSERT INTO source_version(id, source_id, sha256, path, mime_type, acquisition_route,"
                 " quality_flags, byte_size, created_at) VALUES (?,?,?, 'x', 'text/html', 'live_fetch', ?, 1, ?)",
                 (vid, sid, sha256_text(vid), json.dumps(flags or []), NOW))
    offset = 0
    for n, text in enumerate(passages, 1):
        conn.execute("INSERT INTO passage(id, version_id, sequence, char_start, char_end, text, text_sha256,"
                     " language, created_at) VALUES (?,?,?,?,?,?,?, 'en', ?)",
                     (f"p{n}_{vid}", vid, n, offset, offset + len(text), text, sha256_text(text), NOW))
        offset += len(text)
    return sid


class FakeClient:
    """Answers each call from a queue of replies (dicts, exceptions or 'truncate')."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.unloaded = []

    def chat_json(self, model, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if reply == "truncate":
            return ChatResult(None, '{"statements": [', model, 1.0, 100, 512, "length")
        if isinstance(reply, tuple) and reply[0] == "cut":   # a reply cut off part-way, as raw text
            return ChatResult(None, reply[1], model, 1.0, 100, 512, "length")
        return ChatResult(reply, json.dumps(reply), model, 2.0, 100, 50, "stop")

    def unload(self, model):
        self.unloaded.append(model)


def statement(unit, quote, speaker, **kw):
    return {"unit": unit, "quote": quote, "speaker": speaker, "quoted_party": kw.get("quoted_party", ""),
            "intermediary": kw.get("intermediary", ""), "attribution": kw.get("attribution", "direct_quote"),
            "stance": kw.get("stance", "describes"), "claim": kw.get("claim", False)}


def reply(statements=(), entities=(), relations=()):
    """A model reply: statements keyed by unit (the unit is not repeated inside each item)."""
    keyed = {}
    for s in statements:
        keyed.setdefault(s["unit"], []).append({k: v for k, v in s.items() if k != "unit"})
    return {"statements": keyed, "entities": list(entities), "relations": list(relations)}


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "a.db")
    db.init_db(connection)
    with connection:
        add_source(connection, "https://example.org/exodus", [TEXT_A, TEXT_B])
        connection.execute("INSERT INTO entity(id, kind, label, normalized_label, created_at)"
                           " VALUES ('ent_exodus', 'organization', 'Exodus International', 'exodus international', ?)",
                           (NOW,))
        connection.execute("INSERT INTO entity_variant(id, entity_id, label, normalized_label, language, kind,"
                           " created_at) VALUES ('var1', 'ent_exodus', 'Exodus', 'exodus', 'en', 'alt', ?)", (NOW,))
    yield connection
    connection.close()


SID = source_id_for("https://example.org/exodus")


def good_reply():
    return reply(
        statements=[
            statement("P1", "We are closing the ministry today.", "Alan Chambers, president of Exodus",
                      intermediary="example.org", claim=True),
            statement("P1", "local affiliated ministries will continue their work", "Exodus",
                      attribution="reported_speech"),
            statement("P2", "a new generation is looking for change", "Tony Moore",
                      attribution="reported_speech", stance="mixed"),
            statement("P2", "This sentence is not in the text at all.", "Tony Moore"),
        ],
        entities=[{"name": "Alan Chambers", "kind": "person", "unit": "P1", "quote": "Alan Chambers"},
                  {"name": "Exodus", "kind": "organization", "unit": "P1", "quote": "Exodus"},
                  {"name": "Nobody Here", "kind": "person", "unit": "P1", "quote": "Nobody Here"}],
        relations=[{"subject": "Alan Chambers", "type": "employed_by", "object": "Exodus",
                    "stated_by": "example.org", "unit": "P1", "quote": "Alan Chambers, president of Exodus"},
                   {"subject": "Exodus", "type": "funded_by", "object": "Someone",
                    "stated_by": "example.org", "unit": "P1", "quote": "invented funding sentence"}])


def test_reading_writes_located_records_with_decisions(conn):
    client = FakeClient([good_reply()])
    results = read.read_sources(conn, client, ROLE, [SID])
    assert results[0]["outcome"] == "succeeded"
    assert client.unloaded == ["test-model:1b"]                    # freed when the batch ends
    assert client.calls[0]["keep_alive"] == "1m"
    run = conn.execute("SELECT * FROM run WHERE role = 'reading'").fetchone()
    assert run["model"] == "test-model:1b" and run["prompt_version"] == read.DEFAULT_PROMPT
    assert json.loads(run["inputs"])["source_id"] == SID
    usages = conn.execute("SELECT * FROM usage WHERE found_by = 'model' AND entity_id IS NULL"
                          " ORDER BY char_start IS NULL, passage_id, char_start").fetchall()
    located = [u for u in usages if u["evidence_state"] == "located"]
    assert len(located) == 4 and all(u["run_id"] == run["id"] for u in usages)  # 3 statements + relation evidence
    unlocated = [u for u in usages if u["evidence_state"] == "unlocated"]
    assert len(unlocated) == 1 and unlocated[0]["passage_id"] is None
    for u in located:  # stored quotes are exact slices
        text = conn.execute("SELECT text FROM passage WHERE id = ?", (u["passage_id"],)).fetchone()[0]
        assert text[u["char_start"]:u["char_end"]] == u["quote"]
    chambers = next(u for u in located if u["quote"] == "We are closing the ministry today.")
    assert chambers["speaker"] == "Alan Chambers, president of Exodus" and chambers["intermediary"] == "example.org"
    claim = conn.execute("SELECT * FROM claim").fetchone()
    assert claim["claimant_label"].startswith("Alan Chambers") and claim["factual_status"] == "not_assessed"
    # entities: 'Exodus' attaches to the seed entity by variant; Chambers is new; a name not in the text is dropped
    mentions = conn.execute("SELECT entity_id FROM usage WHERE entity_id IS NOT NULL").fetchall()
    assert len(mentions) == 2 and "ent_exodus" in {m[0] for m in mentions}
    new = conn.execute("SELECT * FROM entity WHERE id <> 'ent_exodus'").fetchall()
    assert [e["label"] for e in new] == ["Alan Chambers"] and new[0]["review_state"] == "proposed"
    relation = conn.execute("SELECT * FROM relation").fetchall()
    assert len(relation) == 1                                       # the unlocated funding edge is dropped
    assert relation[0]["type"] == "employed_by" and relation[0]["resolution_status"] == "resolved"
    assert relation[0]["attributed_source_id"] == SID and relation[0]["attribution"] == "example.org"
    tables = [r[0] for r in conn.execute("SELECT target_table FROM decision WHERE action = 'propose'")]
    assert tables.count("usage") == 7 and tables.count("claim") == 1
    assert tables.count("entity") == 1 and tables.count("relation") == 1
    assert db.verify_decision_chain(conn).ok


def test_prompt_shows_units_and_constrains_unit_ids(conn):
    client = FakeClient([reply()])
    read.read_sources(conn, client, ROLE, [SID])
    call = client.calls[0]
    assert call["schema"]["properties"]["statements"]["required"] == ["P1", "P2"]   # one list per unit
    user = call["messages"][1]["content"]
    assert "[P1]" in user and TEXT_B in user and "Title: A title" in user
    assert call["messages"][0]["content"] == read.load_prompt().text


def test_skips_bot_check_furniture_merged_and_repeats(conn):
    with conn:
        bot = add_source(conn, "https://example.org/bot", ["Verifying your browser"], flags=["challenge_page"])
        add_source(conn, "https://example.org/exodus", [TEXT_A], version="w")        # same text again
        for n in range(3):
            add_source(conn, f"https://example.org/s{n}", ["Subscribe to our newsletter today"])
        add_source(conn, "https://example.org/s0", ["Subscribe to our newsletter today", "Body text here."],
                   version="w")
        dup = add_source(conn, "https://example.org/copy", [TEXT_B])
        conn.execute("UPDATE source SET merged_into = ? WHERE id = ?", (SID, dup))
    units, skipped = read.source_units(conn, SID)
    assert [u["text"] for u in units] == [TEXT_A, TEXT_B] and skipped["repeated_passages"] == 1
    units, skipped = read.source_units(conn, bot)
    assert units == [] and skipped["flagged_versions"][0]["flags"] == ["challenge_page"]
    s0 = source_id_for("https://example.org/s0")
    units, skipped = read.source_units(conn, s0)
    assert [u["text"] for u in units] == ["Body text here."] and skipped["furniture_passages"] == 1
    client = FakeClient([reply(), reply()])
    results = read.read_sources(conn, client, ROLE, [dup, SID, bot])
    assert [r["source_id"] for r in results] == [SID, bot]          # the copy is read as its keeper, once
    assert len(client.calls) == 1                                    # nothing to send for the bot check
    assert results[1]["outcome"] == "succeeded" and results[1]["located"] == 0


def test_chunks_keep_versions_apart_and_respect_size():
    units = [{"version_id": "a", "text": "x" * 40}, {"version_id": "a", "text": "y" * 40},
             {"version_id": "a", "text": "z" * 40}, {"version_id": "b", "text": "w" * 10}]
    chunks = read.make_chunks(units, 90)
    assert [[u["text"][0] for u in c] for c in chunks] == [["x", "y"], ["z"], ["w"]]


def test_cut_off_reply_is_read_as_two_halves(conn):
    client = FakeClient(["truncate",
                         reply([statement("P1", "We are closing the ministry today.", "Alan Chambers")]),
                         reply([statement("P1", "a new generation is looking for change", "Tony Moore")])])
    results = read.read_sources(conn, client, ROLE, [SID])
    assert results[0]["outcome"] == "succeeded" and results[0]["located"] == 2
    halves = [c["schema"]["properties"]["statements"]["required"] for c in client.calls]
    assert halves == [["P1", "P2"], ["P1"], ["P1"]]
    assert "Last speaker: Alan Chambers" in client.calls[2]["messages"][1]["content"]  # carried across


def test_failed_source_writes_nothing_and_batch_continues(conn):
    with conn:
        other = add_source(conn, "https://example.org/other", ["Another page about the ministry closing."])
    client = FakeClient([OllamaError("timeout"), OllamaError("timeout"), reply()])
    results = read.read_sources(conn, client, ROLE, [SID, other])
    assert [r["outcome"] for r in results] == ["failed", "succeeded"]
    failed = conn.execute("SELECT * FROM run WHERE outcome = 'failed'").fetchone()
    assert "timeout" in failed["error"]
    assert conn.execute("SELECT COUNT(*) FROM usage WHERE run_id = ?", (failed["id"],)).fetchone()[0] == 0


def test_reading_again_supersedes_and_scorer_sees_only_the_latest(conn):
    from archive import testset
    first = reply([statement("P1", "We are closing the ministry today.", "Exodus")])
    read.read_sources(conn, FakeClient([first]), ROLE, [SID])
    skipped = read.read_sources(conn, FakeClient([]), ROLE, [SID])
    assert skipped[0]["outcome"] == "skipped"                        # same prompt, same model
    second = reply([statement("P1", "We are closing the ministry today.", "Alan Chambers")])
    results = read.read_sources(conn, FakeClient([second]), ROLE, [SID], again=True)
    assert results[0]["superseded_runs"] == 1
    states = dict(conn.execute("SELECT speaker, review_state FROM usage WHERE found_by = 'model'").fetchall())
    assert states == {"Exodus": "superseded", "Alan Chambers": "proposed"}
    decision = conn.execute("SELECT * FROM decision WHERE action = 'supersede'").fetchone()
    assert decision["target_table"] == "run"
    view = testset.entry_view(conn, {"urls": ["https://example.org/exodus"]})
    assert [u["speaker"] for u in view.usages] == ["Alan Chambers"] and len(view.runs) == 1
    assert db.verify_decision_chain(conn).ok


def test_prompt_file_exists_and_names_every_field():
    prompt = read.load_prompt()
    schema = read.response_schema(["P1"])
    for name in schema["properties"]["statements"]["properties"]["P1"]["items"]["required"]:
        assert re.search(rf"\b{name}\b", prompt.text), name
    with pytest.raises(read.ReadError):
        read.load_prompt("no-such-prompt")


def test_long_passage_with_no_quote_gets_a_second_look(conn):
    long_text = ("The ministry announced a new programme of counselling for same-sex attracted men. " * 6).strip()
    with conn:
        sid = add_source(conn, "https://example.org/long", ["Short opening line about the ministry.", long_text])
    first = reply([statement("P1", "Short opening line about the ministry.", "Ministry")])
    second = reply([statement("P1", "a new programme of counselling", "Ministry", claim=False)])
    client = FakeClient([first, second])
    results = read.read_sources(conn, client, ROLE, [sid])
    assert results[0]["located"] == 2 and results[0]["passages_without_quotes"] == 0
    assert client.calls[1]["schema"]["properties"]["statements"]["required"] == ["P1"]
    assert long_text in client.calls[1]["messages"][1]["content"]
    assert "Short opening line" not in client.calls[1]["messages"][1]["content"]
    run = conn.execute("SELECT inputs FROM run WHERE role = 'reading'").fetchone()
    assert json.loads(run[0])["gap_calls"] == 1
    assert "Site: example.org" in client.calls[0]["messages"][1]["content"]


def test_old_list_format_is_still_understood():
    data = {"statements": [{"unit": "P1", "quote": "q", "speaker": "s"}]}
    assert read.statements_of(data) == data["statements"]
    keyed = {"statements": {"P1": [{"quote": "a"}], "P2": [], "P3": [{"quote": "b"}]}}
    assert [(s["unit"], s["quote"]) for s in read.statements_of(keyed)] == [("P1", "a"), ("P3", "b")]


def test_progress_is_recorded_after_each_section(conn):
    read.read_sources(conn, FakeClient([reply()]), ROLE, [SID])
    run = conn.execute("SELECT * FROM run WHERE role = 'reading'").fetchone()
    assert json.loads(run["progress"])["sections_done"] == 1 and run["heartbeat_at"]
    assert json.loads(run["inputs"])["chars"] == len(TEXT_A) + len(TEXT_B)


def test_source_over_its_time_ceiling_is_stopped(conn, monkeypatch):
    with conn:
        sid = add_source(conn, "https://example.org/many", [f"Passage {n} about the ministry." for n in range(4)])
    ticks = iter([0, 0, 10_000, 10_000, 10_000])
    monkeypatch.setattr(read.Reader, "ceiling_seconds", lambda self, chars: 600)
    reader_clock = lambda: next(ticks)
    original = read.Reader.__init__

    def patched(self, *a, **k):
        original(self, *a, **k)
        self.clock = reader_clock
    monkeypatch.setattr(read.Reader, "__init__", patched)
    results = read.read_sources(conn, FakeClient([reply(), reply()]), ROLE, [sid], chunk_chars=60)
    assert results[0]["outcome"] == "failed" and "time ceiling" in results[0]["error"]


def test_ceiling_scales_with_length(conn):
    reader = read.Reader(conn, FakeClient([]), ROLE)
    assert reader.ceiling_seconds(1_000) == read.MIN_CEILING_SECONDS
    assert reader.ceiling_seconds(150_000) == 3 * 10 * 150


def test_stalled_run_is_closed_by_the_next_batch(conn):
    with conn:
        conn.execute("INSERT INTO run(id, role, model, inputs, started_at, heartbeat_at) VALUES"
                     " ('old', 'reading', 'm', ?, '2026-11-10T01:00:00Z', '2026-11-10T01:05:00Z')",
                     (json.dumps({"source_id": SID}),))
    status = read.run_status(conn)
    assert status[0]["stalled"] is True and status[0]["source_id"] == SID
    read.read_sources(conn, FakeClient([reply()]), ROLE, [SID])
    row = conn.execute("SELECT outcome, error FROM run WHERE id = 'old'").fetchone()
    assert row["outcome"] == "failed" and row["error"].startswith("stalled")
    assert read.run_status(conn) == []


def test_salvage_keeps_complete_items_before_the_cut():
    text = ('{"statements": {"P1": [{"quote": "a \\"quoted\\" bit]", "speaker": "X"}, '
            '{"quote": "second", "speaker": "Y"}, {"quote": "thi')
    data = read.salvage_json(text)
    assert [s["quote"] for s in read.statements_of(data)] == ['a "quoted" bit]', "second"]
    assert read.salvage_json("not json at all") is None


def test_looping_single_passage_is_salvaged_not_failed(conn):
    with conn:
        sid = add_source(conn, "https://example.org/loop", ["We are closing the ministry today, said the board."])
    item = json.dumps({"quote": "We are closing the ministry today", "speaker": "the board", "quoted_party": "",
                       "intermediary": "", "attribution": "reported_speech", "stance": "describes", "claim": False})
    looping = '{"statements": {"P1": [' + ", ".join([item] * 30) + ', {"quote": "We are clos'
    client = FakeClient([("cut", looping), ("cut", looping)])
    results = read.read_sources(conn, client, ROLE, [sid])
    assert results[0]["outcome"] == "succeeded"
    assert results[0]["located"] == 1 and results[0]["repeats_dropped"] == 29
    assert results[0]["salvaged_sections"] == 1


# --------------------------------------------------------------------------- read-v5

V5 = read.load_prompt("read-v5")


def test_read_v5_asks_for_the_ban_position_and_allows_no_unknown_attribution():
    item = read.response_schema(["P1"], V5.features)["properties"]["statements"]["properties"]["P1"]["items"]
    assert "ban_position" in item["required"]
    assert item["properties"]["ban_position"]["enum"] == ["supports", "opposes", "none"]
    assert "unknown" not in item["properties"]["attribution"]["enum"]
    for name in item["required"]:
        assert re.search(rf"\b{name}\b", V5.text), name
    old = read.response_schema(["P1"])["properties"]["statements"]["properties"]["P1"]["items"]
    assert "ban_position" not in old["required"]                      # read-v4 runs stay as they were


@pytest.mark.parametrize("stance, ban, expected", [
    ("endorses", "supports", "criticizes"),       # supporting a ban read as endorsing it
    ("criticizes", "opposes", "endorses"),        # "the ban harms…" read as criticizing
    ("not_applicable", "supports", "criticizes"),
    ("unclear", "opposes", "endorses"),
    ("mixed", "opposes", "mixed"),
    ("minimizes", "opposes", "minimizes"),
    ("describes", "supports", "describes"),       # what a bill contains is not a criticism
    ("reports", "supports", "reports"),           # "22 states have enacted bans"
    ("reports", "opposes", "reports"),
    ("describes", "none", "describes"),
    ("describes", None, "describes"),
])
def test_stance_is_set_from_the_ban_position(stance, ban, expected):
    assert read.stance_from_ban(stance, ban) == expected


def test_v5_reading_maps_stance_and_drops_host_and_guest_as_names(conn):
    answer = good_reply()
    first, second = answer["statements"]["P1"][0], answer["statements"]["P2"][0]
    first.update(stance="endorses", ban_position="supports")
    second.update(speaker="guest", stance="criticizes", ban_position="opposes")
    for items in answer["statements"].values():
        for s in items:
            s.setdefault("ban_position", "none")
    read.read_sources(conn, FakeClient([answer]), ROLE, [SID], prompt=V5)
    rows = {r["quote"]: r for r in conn.execute("SELECT * FROM usage WHERE entity_id IS NULL")}
    closing = rows["We are closing the ministry today."]
    assert (closing["stance"], closing["ban_position"]) == ("criticizes", "supports")
    reason = conn.execute("SELECT reason FROM decision WHERE target_id = ?", (closing["id"],)).fetchone()[0]
    assert "set by software from ban position 'supports' (the reader gave 'endorses')" in reason
    generation = rows["a new generation is looking for change"]
    assert (generation["speaker"], generation["stance"]) == ("unknown", "endorses")
    run = json.loads(conn.execute("SELECT inputs FROM run WHERE role = 'reading'").fetchone()[0])
    assert run["counts"]["stance_from_ban"] == 2 and run["stance_rule"] == read.STANCE_RULE


def test_the_researchers_speaker_map_goes_into_the_header(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("SOGICE_DATA_DIR", str(tmp_path))
    assert "Speakers of this recording" not in read.source_header(conn, SID)
    (tmp_path / "speakers").mkdir()
    (tmp_path / "speakers" / f"{SID}.json").write_text(json.dumps(
        {"speakers": [{"name": "Jane Host", "role": "host"}, {"name": "Joe Guest", "role": "guest"}],
         "note": "The host tells her own story at 12:00."}))
    header = read.source_header(conn, SID)
    assert "Speakers of this recording (named by the researcher): Jane Host (host); Joe Guest (guest)" in header
    assert "The host tells her own story" in header
    read.read_sources(conn, FakeClient([good_reply()]), ROLE, [SID])
    inputs = json.loads(conn.execute("SELECT inputs FROM run WHERE role = 'reading'").fetchone()[0])
    assert len(inputs["speaker_map_sha256"]) == 64


def test_the_speakers_command_writes_the_map(conn, tmp_path, monkeypatch, capsys):
    from archive.__main__ import main
    monkeypatch.setenv("SOGICE_DATA_DIR", str(tmp_path))
    db_path = tmp_path / "a.db"
    assert main(["speakers", SID, "--db", str(db_path), "--set", "Jane Host=host", "--note", "Unlabelled."]) == 0
    assert main(["speakers", SID, "--db", str(db_path), "--set", "Joe Guest=guest"]) == 0
    saved = json.loads((tmp_path / "speakers" / f"{SID}.json").read_text())
    assert [s["name"] for s in saved["speakers"]] == ["Jane Host", "Joe Guest"] and saved["note"] == "Unlabelled."
    assert "Jane Host (host); Joe Guest (guest)" in read.source_header(conn, SID)
