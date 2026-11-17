"""--sources-file and --limit on read, tag, check and review prepare (docs/ORCHESTRATION.md, S3 step 1). No model."""
import argparse

import pytest

from archive import check, db, review
from archive.__main__ import SelectionError, main, select_sources, sources_from_file
from tests.test_read import TEXT_A, TEXT_B, add_source

URLS = [f"https://example.org/{name}" for name in ("one", "two", "three", "four")]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("SOGICE_DATA_DIR", str(tmp_path / "data"))   # reports go to the test's folder
    path = tmp_path / "a.db"
    conn = db.connect(path)
    db.init_db(conn)
    with conn:
        ids = [add_source(conn, url, [TEXT_A, TEXT_B]) for url in URLS]
        conn.execute("UPDATE source SET merged_into = ? WHERE id = ?", (ids[0], ids[3]))   # four is merged into one
    yield conn, path, ids
    conn.close()


def write(tmp_path, lines, name="sources.txt"):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def namespace(**kw):
    base = {"sources_file": None, "source": None, "testset": False, "limit": None, "split": None, "entry": None}
    return argparse.Namespace(**{**base, **kw})


def test_sources_file_keeps_order_and_skips_comments(tmp_path):
    path = write(tmp_path, ["# reading order", "", "src_b  # 1200 chars", "  src_a", "#src_c", "src_b"])
    assert sources_from_file(path) == ["src_b", "src_a", "src_b"]
    with pytest.raises(SelectionError, match="line 1: one source id per line"):
        sources_from_file(write(tmp_path, ["src_a src_b"], "bad.txt"))
    with pytest.raises(SelectionError, match="cannot read"):
        sources_from_file(tmp_path / "missing.txt")


def test_selection_file_first_then_source_once_each_then_limit(setup, tmp_path):
    conn, _, ids = setup
    one, two, three, four = ids
    path = write(tmp_path, [three, one, three, four])                # four is merged into one: kept once
    assert select_sources(conn, namespace(sources_file=path, source=[two, one])) == [three, one, two]
    assert select_sources(conn, namespace(sources_file=path, source=[two], limit=2)) == [three, one]
    assert select_sources(conn, namespace(source=[four, one])) == [four]   # the id as requested; read resolves it
    with pytest.raises(SelectionError, match="2 unknown source id"):
        select_sources(conn, namespace(sources_file=write(tmp_path, ["src_x", one, "src_y"], "u.txt")))


def test_read_dry_run_takes_the_first_sources_of_the_file(setup, tmp_path, capsys):
    _, path, ids = setup
    order = write(tmp_path, ["# order", ids[2], ids[1], ids[0]])
    assert main(["read", "--db", str(path), "--sources-file", str(order), "--limit", "2", "--dry-run"]) == 0
    lines = [line.split(":")[0] for line in capsys.readouterr().out.splitlines() if line.startswith("src")]
    assert lines == [ids[2], ids[1]]


def test_tag_dry_run_with_a_sources_file(setup, tmp_path, capsys):
    _, path, ids = setup
    order = write(tmp_path, [ids[1], ids[3]])                        # four stands for one
    assert main(["tag", "--db", str(path), "--sources-file", str(order), "--dry-run"]) == 0
    lines = [line.split(":")[0] for line in capsys.readouterr().out.splitlines() if line.startswith("src")]
    assert lines == [ids[1], ids[0]]


def test_unknown_id_or_bad_limit_stops_before_anything_runs(setup, tmp_path, capsys):
    _, path, ids = setup
    bad = write(tmp_path, [ids[0], "src_nowhere"])
    for command in (["read"], ["tag"], ["check"], ["review", "prepare"]):
        assert main([*command, "--db", str(path), "--sources-file", str(bad)]) == 2
        assert "unknown source id" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["read", "--db", str(path), "--source", ids[0], "--limit", "0"])


def test_check_gets_keepers_in_file_order(setup, tmp_path, monkeypatch):
    _, path, ids = setup
    seen = {}

    class NoClient:
        def __init__(self, *args):
            pass

    def fake_check(conn, client, role, sources, **kw):
        seen["sources"] = sources
        return {"model": role.model, "items": 0, "calls": 0, "seconds": 0, "counts": {}}

    monkeypatch.setattr("archive.ollama.OllamaClient", NoClient)
    monkeypatch.setattr(check, "check", fake_check)
    order = write(tmp_path, [ids[2], ids[3], ids[1]])
    assert main(["check", "--db", str(path), "--sources-file", str(order), "--limit", "2"]) == 0
    assert seen["sources"] == [ids[2], ids[0]]


def test_review_prepare_takes_the_selection_and_never_widens_it(setup, tmp_path, monkeypatch):
    _, path, ids = setup
    seen = []
    monkeypatch.setattr(review, "prepare", lambda conn, sources, **kw: seen.append(sources) or {})
    order = write(tmp_path, [ids[3], ids[2]])
    assert main(["review", "prepare", "--db", str(path), "--sources-file", str(order)]) == 0
    assert seen == [[ids[0], ids[2]]]
    # an empty file must not become "every source"
    assert main(["review", "prepare", "--db", str(path), "--sources-file", str(write(tmp_path, ["# none"], "e.txt"))]) == 2
    assert len(seen) == 1
    assert main(["review", "prepare", "--db", str(path)]) == 0 and seen[-1] is None   # no selection: all, as before
