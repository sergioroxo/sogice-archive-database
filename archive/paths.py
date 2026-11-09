"""Where local data lives. Everything under data/ is ignored by git (public repo).

Set ``SOGICE_DATA_DIR`` to use another folder (e.g. an external disk).
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OLD_PROJECT = (Path.home() / "Library" / "Application Support" / "SurvivingSOGICE"
               / "projects" / "surviving-sogice")
OLD_QUEUE = OLD_PROJECT / "state" / "source_queue.db"


def data_dir() -> Path:
    return Path(os.environ.get("SOGICE_DATA_DIR") or REPO_ROOT / "data")


def database_path() -> Path:
    return data_dir() / "archive.db"


def reports_dir() -> Path:
    return data_dir() / "reports"
