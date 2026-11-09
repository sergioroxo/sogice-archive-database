"""Small atomic file-write helpers for local archive files.

Writes land in a temporary file in the same directory, are flushed/fsynced, then
replace the target path with ``os.replace``. This avoids truncated JSON files
when a laptop sleeps or a worker is killed mid-write.

Ported unchanged from the old repository (``runner/pipeline/atomic_io.py``).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_parent_dir(path.parent)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return path


def _fsync_parent_dir(parent: Path) -> None:
    """Best-effort fsync for the directory entry created by ``os.replace``."""
    try:
        dir_fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        # Some platforms/filesystems do not support fsync on directories. The
        # file itself was already fsynced, so keep this as durability best effort.
        pass
    finally:
        os.close(dir_fd)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    default=None,
) -> Path:
    text = json.dumps(
        payload,
        indent=indent,
        ensure_ascii=ensure_ascii,
        default=default,
    )
    return atomic_write_text(path, text + "\n")
