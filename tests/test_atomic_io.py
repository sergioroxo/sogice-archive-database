from __future__ import annotations

import json

import pytest

from archive.atomic_io import atomic_write_json, atomic_write_text


def test_atomic_write_json_writes_valid_json(tmp_path):
    path = tmp_path / "nested" / "payload.json"

    atomic_write_json(path, {"ok": True, "items": [1, 2, 3]})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "ok": True,
        "items": [1, 2, 3],
    }
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_atomic_write_text_replaces_existing_file(tmp_path):
    path = tmp_path / "artifact.txt"
    path.write_text("old", encoding="utf-8")

    atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "new"


def test_atomic_write_text_preserves_existing_file_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "artifact.txt"
    path.write_text("old", encoding="utf-8")

    def fail_replace(*_args, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr("archive.atomic_io.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".artifact.txt.*.tmp")) == []
