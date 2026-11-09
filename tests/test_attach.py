import json
import os

import pytest

from archive import db
from archive.__main__ import main
from archive.attach import TargetIndex, match_file, run_attach, url_slug
from archive.depot import Depot
from tests.test_extract import make_pdf

NOW = "2026-11-09T12:00:00Z"
TF = "https://www.tandfonline.com/doi/full/10.1080/00918369.2014.926763"
SAGE = "https://journals.sagepub.com/doi/10.1177/1363460718797543"
LONG = "https://example.org/" + "very-long-path-segment/" * 12 + "report.pdf"
WB = "https://web.archive.org/web/20081205061031/http://exodus.to/content/view/67/55/"


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "archive.db")
    db.init_db(connection)
    rows = [("s1", "t1", TF, "Experiences of Ex-Ex-Gay Individuals in Sexual Reorientation Therapy"),
            ("s2", "t2", SAGE, "Telling a poor man he can become rich: Reparative therapy in context"),
            ("s3", "t3", LONG, None), ("s4", "t4", WB, None),
            ("s5", "t5", "https://example.org/other", "Healing Homosexuality Through Prayer Ministries Today"),
            ("s6", "t6", "https://example.net/x", "Healing Homosexuality Through Prayer Ministries Toda")]
    with connection:
        for sid, tid, url, title in rows:
            route = "wayback" if "web.archive.org" in url else "live_fetch"
            connection.execute("INSERT INTO source(id, url, title, created_at) VALUES (?,?,?,?)",
                               (sid, url if route == "live_fetch" else "http://exodus.to/content/view/67/55/",
                                title, NOW))
            connection.execute("INSERT INTO source_target(id, source_id, route, url, created_at)"
                               " VALUES (?,?,?,?,?)", (tid, sid, route, url, NOW))
    yield connection
    connection.close()


def write(folder, name, pages):
    path = folder / name
    path.write_bytes(make_pdf(pages))
    return path


@pytest.mark.parametrize("name", [
    "https___www.tandfonline.com_doi_full_10.1080_00918369.2014.926763.pdf",
    "https%3A%2F%2Fwww.tandfonline.com%2Fdoi%2Ffull%2F10.1080%2F00918369.2014.926763.pdf",
    "www.tandfonline.com-doi-full-10.1080-00918369.2014.926763.pdf",
    "tandfonline.com doi full 10.1080 00918369.2014.926763.pdf",
])
def test_filename_encodings_match(conn, tmp_path, name):
    path = write(tmp_path, name, ["Some article text."])
    match = match_file(path, TargetIndex(conn))
    assert (match.status, match.rule, match.source_id) == ("matched", "filename", "s1")


def test_slug():
    assert url_slug("https://www.A.org/x?y=1") == url_slug("http___a.org_x_y_1") == "aorgxy1"


def test_truncated_name_wayback_name_doi_title_and_review(conn, tmp_path):
    index = TargetIndex(conn)
    cut = write(tmp_path, url_slug(LONG)[:60] + ".pdf", ["Report."])
    assert match_file(cut, index).rule == "filename_prefix"
    wayback = write(tmp_path, "https___web.archive.org_web_20081205061031_http___exodus.to_content_view_67_55_.pdf",
                    ["Archived page."])
    assert match_file(wayback, index).source_id == "s4"
    by_doi = write(tmp_path, "scan001.pdf", ["Journal of Sexualities DOI: 10.1177/1363460718797543", "Body."])
    assert (match_file(by_doi, index).rule, match_file(by_doi, index).source_id) == ("doi", "s2")
    by_title = write(tmp_path, "paper.pdf", ["Experiences of Ex-Ex-Gay Individuals in Sexual Reorientation Therapy",
                                             "Abstract text."])
    found = match_file(by_title, index)
    assert (found.status, found.rule, found.source_id) == ("matched", "title", "s1")
    twins = write(tmp_path, "twins.pdf", ["Healing Homosexuality Through Prayer Ministries Today"])
    assert match_file(twins, index).status == "review"            # two near-identical titles
    other = write(tmp_path, "unknown.pdf", ["Completely unrelated cookbook recipes for summer."])
    assert match_file(other, index).status == "unmatched"
    (tmp_path / "fake.pdf").write_bytes(b"<html>not a pdf</html>")
    assert match_file(tmp_path / "fake.pdf", index).status == "not_pdf"


def test_attach_and_cli(conn, tmp_path):
    folder = tmp_path / "pdfs"
    (folder / "sub").mkdir(parents=True)
    write(folder, "https___www.tandfonline.com_doi_full_10.1080_00918369.2014.926763.pdf",
          ["Experiences of ex-ex-gay individuals: the full article text, page one."])
    write(folder / "sub", "unknown.pdf", ["Completely unrelated cookbook recipes for summer."])
    before = {p: p.stat().st_mtime for p in folder.rglob("*.pdf")}
    summary = run_attach(conn, Depot(tmp_path / "data"), folder, dry_run=True, log=lambda line: None)
    assert summary["counts"] == {"matched:filename": 1, "unmatched": 1}
    assert conn.execute("SELECT COUNT(*) FROM source_version").fetchone()[0] == 0

    summary = run_attach(conn, Depot(tmp_path / "data"), folder, log=lambda line: None)
    assert summary["counts"] == {"attached:filename": 1, "unmatched": 1}
    version = conn.execute("SELECT * FROM source_version").fetchone()
    assert version["acquisition_route"] == "manual_upload" and version["source_id"] == "s1"
    assert json.loads(version["quality_flags"]) == ["matched_by_filename", "researcher_copy"]
    attempt = conn.execute("SELECT * FROM fetch_attempt").fetchone()
    assert (attempt["route"], attempt["outcome"], attempt["target_id"]) == ("local_file", "stored", "t1")
    from archive.acquire import pending_targets
    assert "t1" not in [t["id"] for t in pending_targets(conn)]          # never fetched again
    again = run_attach(conn, Depot(tmp_path / "data"), folder, log=lambda line: None)
    assert again["counts"] == {"already:filename": 1, "unmatched": 1}
    assert {p: p.stat().st_mtime for p in folder.rglob("*.pdf")} == before   # folder untouched


def test_cli_indexes_attached_pdf(conn, tmp_path, monkeypatch, capsys):
    folder = tmp_path / "pdfs"
    folder.mkdir()
    write(folder, "https___journals.sagepub.com_doi_10.1177_1363460718797543.pdf",
          ["Telling a poor man he can become rich. Reparative therapy in context, page one."])
    monkeypatch.setenv("SOGICE_DATA_DIR", str(tmp_path / "data"))
    db_path = tmp_path / "archive.db"
    assert main(["attach-pdfs", str(folder), "--db", str(db_path)]) == 0
    out = capsys.readouterr().out
    assert '"attached:filename": 1' in out and '"indexed": 1' in out
    passage = conn.execute("SELECT * FROM passage").fetchone()
    assert passage["page_start"] == 1 and "Reparative therapy" in passage["text"]
