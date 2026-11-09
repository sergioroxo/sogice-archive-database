import os

import pytest

from archive.depot import Depot, DepotError, sha256_bytes, sniff_extension


def test_put_is_content_addressed_and_idempotent(tmp_path):
    depot = Depot(tmp_path)
    first = depot.put(b"%PDF-1.4 hello", "pdf")
    assert first.created and first.size == 14
    assert first.relative_path == (f"objects/{first.sha256[:2]}/{first.sha256[2:4]}/"
                                   f"{first.sha256}.pdf")
    path = tmp_path / first.relative_path
    assert path.read_bytes() == b"%PDF-1.4 hello"
    assert not os.access(path, os.W_OK)          # stored read-only
    again = depot.put(b"%PDF-1.4 hello", "pdf")
    assert not again.created and again.relative_path == first.relative_path
    assert depot.read(first.relative_path) == b"%PDF-1.4 hello"


def test_corruption_is_detected(tmp_path):
    depot = Depot(tmp_path)
    stored = depot.put(b"abc", "txt")
    path = tmp_path / stored.relative_path
    os.chmod(path, 0o644)
    path.write_bytes(b"abd")
    with pytest.raises(DepotError, match="corrupt"):
        depot.read(stored.relative_path)
    with pytest.raises(DepotError, match="corrupt"):
        depot.put(b"abc", "txt")


def test_rejects_bad_input(tmp_path):
    depot = Depot(tmp_path)
    with pytest.raises(ValueError):
        depot.put(b"", "txt")
    with pytest.raises(ValueError):
        depot.put(b"x", "../x")
    with pytest.raises(ValueError):
        depot.absolute("../../etc/passwd")
    with pytest.raises(DepotError, match="missing"):
        depot.read(depot.relative_path(sha256_bytes(b"zz"), "txt"))


@pytest.mark.parametrize("data, ctype, expected", [
    (b"%PDF-1.7 ...", "application/octet-stream", "pdf"),
    (b"PK\x03\x04....word/document.xml", "application/zip", "docx"),
    (b"PK\x03\x04....", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    (b"PK\x03\x04....", "application/zip", "zip"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1", "application/msword", "doc"),
    (b"<p>hi</p>", "text/html; charset=utf-8", "html"),
    (b"  <!DOCTYPE html><html>", "", "html"),
    (b"plain", "text/plain", "txt"),
    (b"\x00\x01", "", "bin"),
])
def test_sniff_extension(data, ctype, expected):
    assert sniff_extension(data, ctype) == expected
