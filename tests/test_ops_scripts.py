"""S0 tooling: scripts/verify_objects.py, scripts/backup.sh, scripts/ms_job.sh.

Everything runs on temporary folders and a fixture database; nothing touches data/.
The lock test uses `sleep`, never a model.
"""
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from archive import db
from archive.depot import Depot

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
NOW = "2026-11-17T09:00:00Z"

SPEC = importlib.util.spec_from_file_location("verify_objects", SCRIPTS / "verify_objects.py")
verify_objects = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_objects)

needs_backup_tools = pytest.mark.skipif(
    not (shutil.which("sqlite3") and shutil.which("rsync") and shutil.which("python3")),
    reason="sqlite3, rsync or python3 not installed")


def make_data(base: Path) -> Path:
    """A data folder: archive.db (WAL, real schema) with three versions, two of them sharing a text."""
    data = base / "data"
    depot = Depot(data)
    conn = db.connect(data / "archive.db")
    db.init_db(conn)
    with conn:
        for sid in ("s1", "s2", "s3"):
            conn.execute("INSERT INTO source(id, url, created_at) VALUES (?,?,?)",
                         (sid, f"https://example.org/{sid}", NOW))
        text = depot.put(b"shared extracted text", "txt")
        for sid, body, ext in (("s1", b"<html><body>one</body></html>", "html"),
                               ("s2", b"%PDF-1.4 two", "pdf"),
                               ("s3", b"<html><body>three</body></html>", "html")):
            obj = depot.put(body, ext)
            conn.execute(
                "INSERT INTO source_version(id, source_id, sha256, path, byte_size, acquisition_route,"
                " text_sha256, text_path, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"v-{sid}", sid, obj.sha256, obj.relative_path, obj.size, "live_fetch",
                 text.sha256, text.relative_path, NOW))
    conn.close()
    return data


def object_path(data: Path, source_id: str) -> Path:
    conn = sqlite3.connect(data / "archive.db")
    rel = conn.execute("SELECT path FROM source_version WHERE source_id = ?", (source_id,)).fetchone()[0]
    conn.close()
    return data / rel


def damage(path: Path, data: bytes | None) -> None:
    """Delete (data=None) or overwrite a read-only stored object."""
    os.chmod(path, 0o644)
    if data is None:
        path.unlink()
    else:
        path.write_bytes(data)


# --- verify_objects.py ---------------------------------------------------------------------

def test_verify_reports_every_object_once_and_all_ok(tmp_path):
    data = make_data(tmp_path)
    report = verify_objects.verify(data / "archive.db", data)
    assert report["source_versions"] == 3
    assert report["objects"] == 4          # three files and one shared text
    assert report["counts"] == {"file_ok": 3, "text_ok": 1}
    assert report["problems"] == []
    assert verify_objects.main(["--db", str(data / "archive.db"), "--quiet"]) == 0


def test_verify_lists_missing_and_mismatched_objects_with_source_ids_and_changes_nothing(tmp_path, capsys):
    data = make_data(tmp_path)
    missing = object_path(data, "s1")
    corrupt = object_path(data, "s2")
    damage(missing, None)
    damage(corrupt, b"not what was stored")
    # SQLite may add the -wal/-shm side files of a WAL database even for a read-only reader
    def files():
        return {p: p.stat().st_mtime_ns for p in data.rglob("*")
                if p.is_file() and not p.name.endswith(("-wal", "-shm"))}
    before = files()
    db_bytes = (data / "archive.db").read_bytes()
    out_json = tmp_path / "report.json"
    rc = verify_objects.main(["--db", str(data / "archive.db"), "--json", str(out_json), "--quiet"])
    assert rc == 1
    assert files() == before               # read-only: nothing added, removed or rewritten
    assert (data / "archive.db").read_bytes() == db_bytes
    assert not missing.exists()            # and nothing restored
    out = capsys.readouterr().out
    assert "file objects: 3, ok 1; mismatch 1, missing 1" in out
    assert "problems: 2 objects, 2 sources" in out
    problems = {p["status"]: p for p in verify_objects.verify(data / "archive.db", data)["problems"]}
    assert problems["missing"]["source_ids"] == ["s1"]
    assert problems["mismatch"]["source_ids"] == ["s2"]
    assert problems["mismatch"]["actual_sha256"] != problems["mismatch"]["expected_sha256"]
    assert out_json.read_text(encoding="utf-8").count('"status"') == 2


def test_verify_refuses_paths_outside_the_data_folder_and_a_missing_database(tmp_path):
    data = make_data(tmp_path)
    conn = sqlite3.connect(data / "archive.db")
    with conn:
        conn.execute("UPDATE source_version SET path = '../outside.html' WHERE source_id = 's3'")
    conn.close()
    report = verify_objects.verify(data / "archive.db", data)
    assert [p["status"] for p in report["problems"]] == ["bad_path"]
    assert verify_objects.main(["--db", str(tmp_path / "nothing.db"), "--quiet"]) == 2


# --- backup.sh -----------------------------------------------------------------------------

def run_backup(data: Path, dest, *extra) -> subprocess.CompletedProcess:
    env = {**os.environ, "SOGICE_DATA_DIR": str(data)}
    return subprocess.run([str(SCRIPTS / "backup.sh"), str(dest), *extra], env=env,
                          capture_output=True, text=True, timeout=120)


@needs_backup_tools
def test_backup_copies_the_open_database_and_objects_and_passes_the_restore_check(tmp_path):
    data = make_data(tmp_path)
    holder = db.connect(data / "archive.db")      # the database is open (WAL) during the backup
    with holder:
        holder.execute("INSERT INTO source(id, url, created_at) VALUES ('s4', 'https://example.org/s4', ?)",
                       (NOW,))
    dest = tmp_path / "backup"
    result = run_backup(data, dest)
    holder.close()
    assert result.returncode == 0, result.stderr
    copies = list(dest.glob("archive-*.db"))
    assert len(copies) == 1
    copy = sqlite3.connect(copies[0])
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT count(*) FROM source").fetchone()[0] == 4   # WAL content included
    copy.close()
    # a restore from the backup checks clean with verify_objects
    assert verify_objects.verify(copies[0], dest)["problems"] == []
    line = (data / "reports" / "backups.log").read_text().splitlines()[-1]
    assert "status=ok" in line and "integrity=ok" in line and "objects=4/4" in line and "versions=3" in line
    assert "volume=same" in line                      # tmp and tmp: same volume, so a warning
    assert "WARNING" in result.stderr


@needs_backup_tools
def test_backup_never_deletes_in_dest_and_reports_objects_missing_from_the_source(tmp_path):
    data = make_data(tmp_path)
    dest = tmp_path / "backup"
    (dest / "objects").mkdir(parents=True)
    keep = dest / "objects" / "older-file.txt"
    keep.write_text("from an earlier backup")
    damage(object_path(data, "s1"), None)               # Defender took it
    result = run_backup(data, dest)
    assert result.returncode == 3
    assert keep.exists()
    assert "objects=3/4" in (data / "reports" / "backups.log").read_text()
    assert "status=objects_missing" in (data / "reports" / "backups.log").read_text()


@needs_backup_tools
def test_backup_refuses_a_destination_inside_the_repository_or_data_folder(tmp_path):
    data = make_data(tmp_path)
    for dest in (ROOT / "backup-here", data / "backup", ROOT, tmp_path):   # tmp_path contains data/
        result = run_backup(data, dest)
        assert result.returncode == 1, dest
        assert "overlaps" in result.stderr
    assert not (ROOT / "backup-here").exists()
    assert not (data / "backup").exists()
    log = (data / "reports" / "backups.log").read_text()
    assert log.count("status=refused") == 4


@needs_backup_tools
def test_backup_needs_exactly_one_destination(tmp_path):
    data = make_data(tmp_path)
    assert run_backup(data, tmp_path / "a", str(tmp_path / "b")).returncode == 1
    result = subprocess.run([str(SCRIPTS / "backup.sh")], capture_output=True, text=True,
                            env={**os.environ, "SOGICE_DATA_DIR": str(data)})
    assert result.returncode == 1 and "usage" in result.stderr


# --- ms_job.sh -----------------------------------------------------------------------------

def ms_job(data: Path, *cmd, **kw) -> subprocess.Popen:
    env = {**os.environ, "SOGICE_DATA_DIR": str(data), "MS_JOB_POLL": "0.1"}
    return subprocess.Popen([str(SCRIPTS / "ms_job.sh"), *cmd], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def log_lines(data: Path) -> list[str]:
    return (data / "reports" / "ms_jobs.log").read_text().splitlines()


def test_ms_job_runs_two_jobs_one_after_the_other(tmp_path):
    data = tmp_path / "data"
    first = ms_job(data, "sleep", "1.5")
    deadline = time.time() + 10
    while not (data / "ms.lock.d" / "owner").exists():
        assert time.time() < deadline
        time.sleep(0.05)
    second = ms_job(data, "sleep", "0.2")
    assert first.wait(timeout=20) == 0
    assert second.wait(timeout=20) == 0
    events = [line.split(" ", 2)[2] for line in log_lines(data)]
    pids = [line.split(" ")[1] for line in log_lines(data)]
    assert events[0] == "start: sleep 1.5"
    assert events[1].startswith("waiting for pid=") and pids[1] == f"pid={second.pid}"
    assert events[2].startswith("end rc=0") and events[2].endswith("sleep 1.5")
    assert events[3] == "start: sleep 0.2"                # the second starts only after the first ends
    assert events[4].startswith("end rc=0")
    assert not (data / "ms.lock.d").exists()


def test_ms_job_passes_the_exit_code_through(tmp_path):
    data = tmp_path / "data"
    job = ms_job(data, "sh", "-c", "exit 7")
    assert job.wait(timeout=10) == 7
    assert "end rc=7" in log_lines(data)[-1]
    assert ms_job(data, "no-such-command-xyz").wait(timeout=10) == 127
    assert not (data / "ms.lock.d").exists()


def test_ms_job_takes_over_a_stale_lock(tmp_path):
    data = tmp_path / "data"
    dead = subprocess.Popen(["true"])
    dead.wait()
    lock = data / "ms.lock.d"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(f"{dead.pid}|Mon Jan  1 00:00:00 2001\n")
    job = ms_job(data, "true")
    assert job.wait(timeout=10) == 0
    events = [line.split(" ", 2)[2] for line in log_lines(data)]
    assert events[0].startswith("took over a stale lock")
    assert events[1] == "start: true"
    assert not lock.exists()


def test_ms_job_treats_a_reused_pid_as_stale(tmp_path):
    data = tmp_path / "data"
    lock = data / "ms.lock.d"
    lock.mkdir(parents=True)
    # this test process is alive, but its start time is not the one recorded: the PID was reused
    (lock / "owner").write_text(f"{os.getpid()}|Mon Jan  1 00:00:00 2001\n")
    assert ms_job(data, "true").wait(timeout=10) == 0
    assert log_lines(data)[0].split(" ", 2)[2].startswith("took over a stale lock")


def test_ms_job_needs_a_command(tmp_path):
    job = ms_job(tmp_path / "data")
    assert job.wait(timeout=10) == 2
    assert "usage" in job.stderr.read()
