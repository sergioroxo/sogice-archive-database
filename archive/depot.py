"""Content-addressed file store under data/objects/.

Every preserved file is stored once, named by the SHA-256 of its bytes:

    data/objects/ab/cd/abcd...ef.pdf

``put`` is idempotent: the same bytes always land at the same path, and an
existing file is checked rather than rewritten. Stored files are made
read-only. Paths recorded in the database are relative to the data folder
(``objects/ab/cd/<sha>.<ext>``) so the folder can move.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .atomic_io import atomic_write_bytes

_EXTENSION = re.compile(r"^[a-z0-9]{1,8}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
OBJECTS = "objects"


class DepotError(RuntimeError):
    """A stored object is missing or its bytes do not match its name."""


@dataclass(frozen=True)
class StoredObject:
    sha256: str
    relative_path: str   # relative to the data folder
    size: int
    created: bool        # False if the same bytes were already stored


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_extension(data: bytes, content_type: str = "") -> str:
    """File extension from magic bytes first, then the declared content type."""
    head = data[:8]
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        if "wordprocessingml" in ctype or b"word/" in data[:4096]:
            return "docx"
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "doc"
    if ctype in ("text/html", "application/xhtml+xml"):
        return "html"
    if ctype in ("text/plain", "text/markdown"):
        return "txt"
    if ctype in ("text/vtt",):
        return "vtt"
    if ctype in ("application/json",):
        return "json"
    sample = data[:2048].lstrip().lower()
    if sample.startswith((b"<!doctype html", b"<html")) or b"<body" in sample:
        return "html"
    return "bin"


class Depot:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def relative_path(self, sha256: str, extension: str) -> str:
        if not _SHA.match(sha256):
            raise ValueError(f"not a sha256: {sha256!r}")
        if not _EXTENSION.match(extension):
            raise ValueError(f"bad extension: {extension!r}")
        return f"{OBJECTS}/{sha256[:2]}/{sha256[2:4]}/{sha256}.{extension}"

    def absolute(self, relative_path: str) -> Path:
        path = (self.data_dir / relative_path).resolve()
        if self.data_dir.resolve() not in path.parents:
            raise ValueError(f"path escapes the data folder: {relative_path!r}")
        return path

    def put(self, data: bytes, extension: str) -> StoredObject:
        if not data:
            raise ValueError("refusing to store an empty file")
        digest = sha256_bytes(data)
        relative = self.relative_path(digest, extension)
        path = self.absolute(relative)
        if path.exists():
            if sha256_bytes(path.read_bytes()) != digest:
                raise DepotError(f"stored object is corrupt: {relative}")
            return StoredObject(digest, relative, len(data), created=False)
        atomic_write_bytes(path, data)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return StoredObject(digest, relative, len(data), created=True)

    def read(self, relative_path: str, *, verify: bool = True) -> bytes:
        path = self.absolute(relative_path)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise DepotError(f"missing object: {relative_path}") from None
        if verify:
            expected = path.name.split(".", 1)[0]
            if sha256_bytes(data) != expected:
                raise DepotError(f"stored object is corrupt: {relative_path}")
        return data
